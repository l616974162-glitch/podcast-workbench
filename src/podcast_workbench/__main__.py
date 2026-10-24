"""本地配置检查，不访问供应商网络。"""

import argparse
import shutil
import sys
from pathlib import Path

from podcast_workbench.config import ConfigError, load_config


def main() -> int:
    parser = argparse.ArgumentParser(description="双人播客工作台环境与配置检查")
    parser.add_argument("command", choices=("doctor", "check-config"))
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--require-secrets", action="store_true", help="要求两家服务密钥已配置")
    args = parser.parse_args()
    try:
        config = load_config(args.config, require_secrets=args.require_secrets)
    except ConfigError as error:
        print(f"配置检查失败：{error}", file=sys.stderr)
        return 1
    print("配置结构检查通过（不发起网络请求）。")
    if args.command == "doctor":
        print(f"Python：{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        missing = config.missing_secrets()
        key_status = "尚未填写 " + "、".join(missing) if missing else "已配置（未联网验证）"
        print("服务密钥：" + key_status)
        ffmpeg = shutil.which(config.app.ffmpeg_path or "ffmpeg")
        print("FFmpeg：" + ("已找到" if ffmpeg else "未找到；音频阶段前需安装或配置 FFMPEG_PATH"))
        print("当前阶段仅完成基础配置，尚未提供网页或播客制作接口。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
