"""Agnes 图像生成协议；限制下载并验证像素后发布 PNG。"""

import asyncio
import base64
import json
from io import BytesIO
from pathlib import Path
from time import perf_counter

import httpx
from PIL import Image

from podcast_workbench.config import ImageConfig
from podcast_workbench.errors import ServiceError
from podcast_workbench.models import Artifact, CoverOptions, Revision
from podcast_workbench.observability import RunContext


class AgnesImageClient:
    def __init__(self, config: ImageConfig, http: httpx.AsyncClient) -> None:
        self.config, self.http = config, http

    async def _download(self, url_value: str) -> bytes:
        url = httpx.URL(url_value)
        if (url.scheme != "https" or url.userinfo or url.port not in {None, 443}
                or url.host not in self.config.download_hosts):
            raise ValueError("untrusted image address")
        request = self.http.build_request("GET", url, timeout=self.config.timeout_seconds)
        request.headers.pop("Authorization", None)
        response = await self.http.send(request, stream=True, auth=None, follow_redirects=False)
        try:
            response.raise_for_status()
            if int(response.headers.get("Content-Length", "0")) > self.config.max_image_bytes:
                raise ValueError("image too large")
            output = bytearray()
            async for chunk in response.aiter_bytes(65536):
                output.extend(chunk)
                if len(output) > self.config.max_image_bytes:
                    raise ValueError("image too large")
            return bytes(output)
        finally:
            await response.aclose()

    def _write_png(self, data: bytes, path: Path) -> tuple[int, int]:
        if not data or len(data) > self.config.max_image_bytes:
            raise ValueError("image bytes invalid")
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if (image.format not in {"PNG", "JPEG", "WEBP"}
                    or width != height or not 512 <= width <= 4096):
                raise ValueError("invalid image format or size")
            image.load()
            image.convert("RGB").save(path, format="PNG")
        if path.stat().st_size > self.config.max_image_bytes:
            raise ValueError("image too large")
        return width, height

    async def execute(
        self, revision: Revision, options: CoverOptions, directory: Path, ctx: RunContext,
    ) -> Artifact:
        from uuid import uuid4

        started = perf_counter()
        ctx.stage("image")
        ctx.emit("tool.start", node="image", provider="agnes")
        path = directory / str(revision.id) / (str(uuid4()) + ".png")
        temporary = path.with_suffix(".png.part")
        try:
            async with asyncio.timeout(self.config.timeout_seconds):
                async with self.http.stream(
                    "POST", str(self.config.endpoint),
                    headers={"Authorization": "Bearer " + self.config.api_key.get_secret_value()},
                    json={"model": self.config.model, "prompt": options.prompt + "\n"
                          + {"illustration": "Illustration style.", "photo": "Photographic style.",
                             "abstract": "Minimal abstract style."}[options.style]
                          + " Square podcast cover. No text, lettering, logos or watermarks.",
                          "size": "1K", "ratio": "1:1", "return_base64": True},
                    timeout=self.config.timeout_seconds, follow_redirects=False,
                ) as response:
                    response.raise_for_status()
                    payload = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        payload.extend(chunk)
                        if len(payload) > self.config.max_image_bytes * 4 // 3 + 65536:
                            raise ValueError("response too large")
                item = json.loads(payload)["data"][0]
                encoded = item.get("b64_json")
                data = (base64.b64decode(encoded, validate=True) if encoded
                        else await self._download(item["url"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            writing = asyncio.create_task(asyncio.to_thread(self._write_png, data, temporary))
            try:
                width, height = await asyncio.shield(writing)
            except asyncio.CancelledError:
                await writing
                raise
            temporary.replace(path)
            ctx.emit("tool.end", node="image", provider="agnes", status="succeeded",
                     duration_ms=(perf_counter() - started) * 1000)
        except Exception as error:
            ctx.emit("tool.error", node="image", provider="agnes", status="failed",
                     error_code="image_error", http_status=(
                         error.response.status_code if isinstance(error, httpx.HTTPStatusError)
                         else None
                     ))
            raise ServiceError("image_error") from None
        finally:
            temporary.unlink(missing_ok=True)
        return Artifact(revision_id=revision.id, job_id=ctx.job_id,
                        kind="image", path=path.resolve(),
                        mime_type="image/png", image_width=width, image_height=height,
                        image_model=self.config.model, cover_options=options)
