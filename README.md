# 文本转双人播客工作台

将中文文章或笔记制作成双人播客，支持脚本审校、多轮修改、双音色语音、播放和下载。

当前完成范围为第一阶段项目基础与离线配置检查；尚无网页、制作接口或真实服务联调结果。

## 已确定的技术方向

- LangGraph 编排资料整理、播客编剧、内容审校三个 Agent。
- DeepSeek 承担文字模型调用，千问 Qwen-TTS 生成真实语音。
- 默认一次生成完整节目，可选择先预览脚本。
- FastAPI、SQLite、原生网页；完整成品成功后发布，保留旧版本。

用户已明确选择直接基于 LangGraph 开发，不依赖教师的 langraph_agent 框架。该选择与原任务书的指定框架要求存在差异，其余适用要求继续执行。

## 设计文件

- [需求与设计](docs/superpowers/specs/2026-10-24-podcast-workbench-design.md)
- [实施计划](docs/superpowers/plans/2026-10-24-podcast-workbench.md)

## 本地安装与配置

需要 Python 3.11+。本次开发环境为 Windows、Python 3.12.4，虚拟环境位于项目的 .venv。
以下 PowerShell 命令在仓库根目录执行，不需要激活脚本：

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item -LiteralPath .env.example -Destination .env
```

如果 .env 已存在，不重复复制，以免覆盖本地配置。推荐使用已提交的 uv.lock 复现本次依赖版本：

```powershell
uv sync --extra dev --locked
```

本项目当前的 .venv 已创建且依赖已安装，无需重复创建。只需在本地 .env 填写两个密钥：DEEPSEEK_API_KEY、DASHSCOPE_API_KEY。无需将密钥发送到聊天中。

本阶段已验证 LangGraph 1.2.17、FastAPI 0.162.0 可导入。完整的依赖解析版本见 uv.lock；pip 安装路径使用 pyproject.toml 声明的兼容范围，不保证与锁定文件完全相同。

配置优先级为进程环境 > 本地 .env > 仓库 .env.example 中的非敏感默认值。config.yaml 的 api_key 只能引用指定环境变量，明文密钥将被拒绝。运行命令需在仓库根目录执行，或使用 --config 指定配置路径；运行不改变当前进程的环境变量。

## 第一阶段可用命令

```powershell
& .\.venv\Scripts\python.exe -m podcast_workbench doctor
& .\.venv\Scripts\python.exe -m podcast_workbench check-config
& .\.venv\Scripts\python.exe -m podcast_workbench check-config --require-secrets
```

doctor 和 check-config 均不发起网络请求、不计费。离线检查允许密钥为空；加 --require-secrets 后缺少密钥返回非零退出码，已填写也不表示真实供应商鉴权已通过。

检查 FFmpeg 只验证命令是否可找到，不执行音频合成。当前 PATH 中未发现 FFmpeg。后续音频阶段前需安装 FFmpeg，或通过 FFMPEG_PATH 指向现有 ffmpeg.exe；本阶段没有自动下载二进制。

## 接口参数核实

2026-10-24 按官方文档核实：

- DeepSeek 默认使用 deepseek-flash，模型可在 .env 修改；兼容接口基础地址为 https://api.deepseek.com。来源：[官方模型列表](https://api-docs.deepseek.com/quick_start/pricing/)。
- 千问保持任务书指定的 qwen3-tts-flash，使用北京地域的 multimodal-generation/generation 接口，Bearer 鉴权，请求 input 中包含 text、voice、language_type；返回 output.audio.url，有效期 24 小时。来源：[官方 API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)。
- 千问每次输入最多 600 字符，配置限制不能超过此值；长脚本后续分段处理。默认角色音色为 Cherry 和 Ethan，语言为 Chinese。来源：[官方音色列表](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-voice-list)。
- 输入材料上限 20,000 字符、下载音频上限 20 MiB 是本项目的保护参数，不是供应商额度或容量承诺。

模型权限、服务额度、音色实际效果仍需后续真实调用验证。新版本服务不自动替换任务书指定的 TTS 协议。

## 验证命令

```powershell
& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy
```

[开发进度](docs/progress.md)记录用户确认的八阶段顺序。后续按此顺序推进，本阶段没有实现第二阶段的业务输入模型、存储或任何播客制作功能。
