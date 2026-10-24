# Edge 配音与千问配音切换

## 当前谁负责 TTS

三个 DeepSeek Agent 负责资料整理、编写与审校。审校后的脚本包含 host／guest 角色，AudioRenderer 按角色分段、分配设置中的音色，并调用注册的语音客户端；最后 FFmpeg 统一格式、按序合并为完整 WAV。

TTS 是确定执行的工具节点，不需要再调用一个 LLM 来管理。更换音色使用 render_audio，复用当前脚本。

## Edge 模式

默认 .env.example 已配置 TTS_PROTOCOL=edge_tts。Edge 调用在线服务，不需要 TTS API Key 或安装 Edge 浏览器；不是本地离线模型。

默认主持人：zh-CN-XiaoxiaoNeural（晓晓）；默认嘉宾：zh-CN-YunxiNeural（云希）。新建会话自动采用当前引擎的默认音色。DeepSeek 仍需要自己的密钥，只有已有脚本的单独 Edge 配音不需要两家服务密钥。

在本地 .env 中设置：

```dotenv
TTS_PROTOCOL=edge_tts
DEEPSEEK_API_KEY=在本地填写真实密钥
```

使用原来的 run／POST /chat 流程即可。没有自动付费回退：Edge 失败就报告失败，不会偷偷改用千问。Edge 的网络可用性、音色和服务行为可能变化，不承诺永久或无限可用。

库的公开说明与调用方式见 https://github.com/rany2/edge-tts 。本项目只使用公开的 Communicate.stream()，不修改库内部鉴权、TLS 或协议行为。

## 千问与课程验收

任务书明确要求实现并使用 Qwen-TTS 主链路。项目保留原 Qwen-TTS 模板方法、注册、密钥检查和测试。日常可用 Edge；课程验收按任务书切回千问并做真实联调：

```dotenv
TTS_PROTOCOL=qwen_tts
DASHSCOPE_API_KEY=在本地填写北京地域的真实密钥
```

千问新会话默认 Cherry／Ethan。切换引擎后，旧会话保留自己的音色；若旧音色属于另一引擎，需要显式把音色改为当前引擎的名称，不静默替换用户选择。

配置里 qwen 的 model／endpoint／api_key 字段仍保留。Edge 不发送这些配置，也不要求 DashScope Key。

## 一致性和限制

- Edge 返回 MP3 音频流，接收后转换为 24kHz 单声道 PCM WAV，再进入现有合成流程。
- 沿用每段 600 字符、最多 48 段、20 MiB 音频保护上限；这是本项目在 Edge 模式下的限制，不是 Edge 官方配额声明。
- 缓存包含引擎标识，Edge 不复用千问片段；角色、文本、音色、参数不变的同引擎片段可复用。
- 正式音频元数据记录 tts_protocol 和实际 audio_settings，日志 provider 为 edge 或 qwen。
- 超时、空流、超限与解码失败不发布成品，清理临时文件并保留旧结果。
- 网页已对接，启动时读取 /health 的 tts_protocol 和 default_voices，默认显示晓晓与云希；只换音色使用 render_audio，跳过三次 LLM 调用。

## 验证记录

2026-10-24 使用 edge-tts 7.3.1：实际在线音色列表包含晓晓和云希；两句非个人信息的测试文本真实合成为双人 WAV，时长 9.24 秒，trace_id 为 6df29dc9-86e9-457d-b57c-ce000d888d9f。未调用 DeepSeek，不算完整播客链路验收。

测试文件与日志只在本地 output/edge-smoke 和 logs/edge-smoke.log，被 Git 忽略，不以测试结果替代后续用户材料的真实生成。自动回归同时覆盖千问和 Edge 配置、流式接收、超时、超限、无密钥配音及缓存隔离。

最终自动检查：84 项 pytest 通过，ruff 通过，mypy 检查 19 个源文件通过。旧千问契约测试固定在 qwen_tts 模式；新增 Edge 测试显式选择 edge_tts，并另行验证新安装的默认模式确为 Edge。
