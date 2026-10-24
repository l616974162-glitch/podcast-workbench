"""本地配置检查，不访问供应商网络。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from pydantic import ValidationError

from podcast_workbench.config import ConfigError, load_config
from podcast_workbench.errors import ServiceError
from podcast_workbench.media import resolve_ffmpeg
from podcast_workbench.models import ChatRequest
from podcast_workbench.service import RequestError, create_service
from podcast_workbench.store import NotFoundError, StateConflictError


async def run_request(config_path: Path, request_path: Path) -> int:
    config = load_config(config_path)
    try:
        body = ChatRequest.model_validate_json(request_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValidationError):
        raise RequestError("请求文件不可读或 JSON 字段不合法，请检查示例文件。") from None
    async with httpx.AsyncClient(trust_env=False) as http:
        service = create_service(config, http)
        service.store.recover_interrupted_jobs()
        job = service.submit(body)
        print(
            json.dumps(
                {
                    "session_id": str(job.session_id),
                    "job_id": str(job.id),
                    "trace_id": str(job.trace_id),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        await service.drain()
        result = service.store.get_job(job.id)
        artifacts = [
            item.model_dump(mode="json")
            for item in service.store.list_artifacts(job.session_id)
            if item.job_id == result.id
        ]
        print(
            json.dumps(
                {"job": result.model_dump(mode="json"), "artifacts": artifacts}, ensure_ascii=False
            ),
            flush=True,
        )
        return 0 if result.status == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="双人播客工作台环境与配置检查")
    parser.add_argument("command", choices=("doctor", "check-config", "run"))
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--require-secrets", action="store_true", help="要求两家服务密钥已配置")
    parser.add_argument("--request-file", type=Path, help="run 命令的 JSON 请求文件")
    args = parser.parse_args()
    try:
        if args.command == "run":
            if args.request_file is None:
                raise RequestError("run 命令需要 --request-file。")
            return asyncio.run(run_request(args.config, args.request_file))
        config = load_config(args.config, require_secrets=args.require_secrets)
    except (ConfigError, RequestError, ServiceError, StateConflictError, NotFoundError) as error:
        print(f"配置检查失败：{error}", file=sys.stderr)
        return 1
    print("配置结构检查通过（不发起网络请求）。")
    if args.command == "doctor":
        print(f"Python：{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        missing = config.missing_secrets()
        key_status = "尚未填写 " + "、".join(missing) if missing else "已配置（未联网验证）"
        print("服务密钥：" + key_status)
        print(
            "TTS 引擎："
            + config.tts.protocol
            + (
                "（无需 TTS API Key，仍需联网）"
                if not config.tts.needs_api_key
                else "（需要 DashScope Key）"
            )
        )
        try:
            resolve_ffmpeg(config.app.ffmpeg_path)
            print("FFmpeg：已找到（系统命令或项目随包版本）。")
        except ServiceError:
            print("FFmpeg：未找到，请安装依赖或配置 FFMPEG_PATH。")
        print("当前提供同源网页、后端 API 和命令行制作；启动方式见 README。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
