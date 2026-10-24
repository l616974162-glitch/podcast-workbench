"""千问非 OpenAI 兼容 TTS 协议及限量音频下载。"""

from pathlib import Path
from uuid import UUID

import httpx

from podcast_workbench.errors import ServiceError
from podcast_workbench.media import wav_duration
from podcast_workbench.models import Artifact, ProgramSettings
from podcast_workbench.tools.base import ToolClient


class QwenTTSClient(ToolClient):
    def _build_request(self, text: str, voice: str) -> dict[str, object]:
        return {
            "model": self.config.model,
            "input": {
                "text": text,
                "voice": voice,
                "language_type": self.config.language_type,
            },
        }

    async def _parse_response(
        self,
        response: httpx.Response,
        destination: Path,
        revision_id: UUID,
        settings: ProgramSettings,
    ) -> Artifact:
        url = httpx.URL(response.json()["output"]["audio"]["url"])
        if (
            url.scheme not in {"http", "https"}
            or url.userinfo
            or not url.host.endswith(".aliyuncs.com")
            or url.port not in {None, 80, 443}
        ):
            raise ServiceError("audio_error")
        url = url.copy_with(scheme="https", port=None)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            request = self.http.build_request("GET", url, timeout=self.config.timeout_seconds)
            request.headers.pop("Authorization", None)
            download = await self.http.send(request, auth=None, stream=True, follow_redirects=False)
            try:
                download.raise_for_status()
                length = download.headers.get("Content-Length")
                if length is not None and int(length) > self.config.max_audio_bytes:
                    raise ServiceError("audio_error")
                total = 0
                with temporary.open("wb") as output:
                    async for chunk in download.aiter_bytes(65536):
                        total += len(chunk)
                        if total > self.config.max_audio_bytes:
                            raise ServiceError("audio_error")
                        output.write(chunk)
            finally:
                await download.aclose()
            duration = wav_duration(temporary)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
        return Artifact(
            revision_id=revision_id,
            kind="audio",
            path=destination.resolve(),
            mime_type="audio/wav",
            duration_seconds=duration,
            audio_settings=settings,
        )
