# 双人播客工作台实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建可真实调用 DeepSeek 与千问 TTS 的文本转双人播客工作台。

**Architecture:** LangGraph 固定三 Agent pipeline，FastAPI 提交后台任务，SQLite 保存会话和不可变修订，网页轮询任务进度。本地音频按内容指纹复用，完整合成成功后发布成品。

**Tech Stack:** Python 3.11+、FastAPI、LangGraph、Pydantic、httpx、PyYAML、SQLite、FFmpeg、原生 HTML/CSS/JavaScript、pytest、ruff、mypy。

**Spec:** ../specs/2026-10-24-podcast-workbench-design.md

## Global Constraints

- 直接使用 LangGraph，不冒充教师 langraph_agent 封装；README 明确任务书框架差异。
- 三个 Agent 使用真实 DeepSeek；主音频链路使用真实 Qwen-TTS。
- 禁止提交 .env；日志不含密钥、签名 URL 或完整供应商错误正文。
- 默认一次生成完整节目，预览模式不调用 TTS。
- 旧成品只在新版完整成功后替换，音频与脚本修订明确关联。
- 单进程本机使用，默认监听 127.0.0.1；不实现非目标功能。
- 每个任务先编写行为测试，运行并观察预期失败，再实现并验证；依赖检查与静态配置不强制测试。

## Task 1 配置和输入模型

**Files:** pyproject.toml、config.yaml、.env.example、.gitignore、src/podcast_workbench/config.py、models.py；tests/test_models.py、test_config.py。

**Interfaces:** ChatRequest(action, session_id, message, source_text, settings, preview_only)；ProgramSettings(audience, style, target_minutes, host_voice, guest_voice)；Settings 保存解析后的环境与 YAML 配置。

- [ ] 核实供应商当前模型名、千问请求参数、音色和限制，将核实结果与来源写入 README。检查 Python 和 FFmpeg，不擅自下载未知二进制。
- [ ] 创建最小 Python 项目和开发依赖，忽略 .env、.venv、output、logs、SQLite 文件、缓存。
- [ ] 编写模型测试：空白材料、超长材料、非法时长、无 session_id 的修改必须拒绝；合法设置保留用户值。配置测试验证环境引用与缺失密钥错误不暴露值。
- [ ] 运行 pytest tests/test_models.py tests/test_config.py，确认失败来自待实现行为；实现 Pydantic 验证及环境解析，重跑通过。
- [ ] 运行 ruff check 和 mypy，提交本任务文件。

## Task 2 会话和任务存储

**Files:** src/podcast_workbench/store.py、observability.py；tests/test_store.py、test_logging.py。

**Interfaces:** Store.create_session() -> str；Store.save_revision(session_id, script) -> str；Store.publish_audio(session_id, revision_id, artifact_id) -> None；Store.get_session(session_id) -> dict；任务记录包含 status、stage、trace_id、error。

- [ ] 测试两个会话隔离、修订不可覆盖、失败任务不改变上一版成功产物、同一会话并发任务被拒绝。
- [ ] 运行对应 pytest 观察失败，然后用 SQLite 事务实现状态变化、修订与产物索引。
- [ ] 测试日志可通过 trace_id 串联；给错误样本加入虚构密钥和签名 URL，断言不出现在落盘日志中。
- [ ] 实现受控字段 JSONL 日志，不直接序列化异常响应；重跑测试，提交。

## Task 3 DeepSeek 三 Agent 流水线

**Files:** src/podcast_workbench/llm.py、graph.py；config.yaml；tests/test_graph.py、test_llm.py。

**Interfaces:** DeepSeekClient.complete(messages, schema) 返回经模型验证的结构化结果；graph 输入包含 source_text、settings、current_script、revision_request，输出 analysis、script、review_summary。

- [ ] 测试审校后脚本成为最终版本，修改输入包含原材料和当前脚本，结构化输出失败最多补偿一次，失败不继续语音流程。
- [ ] 使用测试专用网络隔离验证 HTTP 协议、超时与错误脱敏，测试不作为验收演示材料。
- [ ] 运行失败测试后，实现可配置提示词、真实 HTTP 客户端与 LangGraph 顺序图；设置 recursion_limit。
- [ ] 验证每个节点都有 begin/end 或 error 日志，运行测试和静态检查后提交。

## Task 4 千问协议与音频制作

**Files:** src/podcast_workbench/tools/base.py、qwen_tts.py、registry.py、audio.py；tests/test_qwen_tts.py、test_audio.py。

**Interfaces:** ToolClient.execute(text, voice) -> Artifact；QwenTTSClient._build_request 构造请求；_parse_response 下载 output.audio.url；AudioRenderer.render(script, settings) -> Artifact，返回完整节目与时长。

- [ ] 测试 Bearer 请求及 model/input 字段、JSON 地址解析、缺失 URL、Content-Length 超限、无长度头流式超限、下载失败不发布产物。
- [ ] 先观察测试失败，再实现模板方法、协议注册与受限下载；服务端错误转换为安全摘要。
- [ ] 编写缓存测试：相同文本和音色复用；换音色失效；修改一段只重做该段；乱序下载完成仍按脚本排序；缺一段时禁止发布。
- [ ] 观察失败后实现内容指纹缓存与 FFmpeg 格式统一、顺序合成。用测试生成的短音频验证真实拼接和时长，不依赖供应商计费。
- [ ] 确认进程调用不经过 shell，临时文件不被当作成功产物；运行检查并提交。

## Task 5 后台任务与 HTTP 接口

**Files:** src/podcast_workbench/service.py、api.py；tests/test_api.py、test_service.py。

**Interfaces:** POST /chat 返回 202 + job_id/session_id/trace_id；GET /jobs/{id}；GET /sessions/{id}；GET /sessions/{id}/artifacts；GET /artifacts/{id}；GET /health。

- [ ] 测试创建和修改、预览不调用 TTS、render_audio 不改写脚本、422/404/409、安全错误、失败保留旧成品。
- [ ] 运行失败测试后实现任务状态机、单会话互斥、启动时将遗留运行任务标记中断。轮询状态独立于原始 HTTP 连接。
- [ ] 测试产物只能通过索引读取，未知 ID 不泄露路径；健康检查不暴露密钥。
- [ ] 接入完整业务流程，跑 API 和服务测试、静态检查后提交。

## Task 6 对话网页

**Files:** src/podcast_workbench/static/index.html、app.js、style.css；tests/test_web_contract.py。

**Interfaces:** 严格消费 Task 5 API，不从正文解析音频路径，不访问供应商密钥。

- [ ] 使用 cc-design 预览协议展示风格方案，在用户选定风格后保存设计系统，再编写应用页面。
- [ ] 测试最小页面契约及资源可访问；使用脚本检查关键表单、播放器、状态区和错误区存在。
- [ ] 实现材料与设置、预览开关、后台轮询、脚本展示、对话修改、三个快捷操作、播放器和下载。
- [ ] 将脚本内容作为文本或受控 DOM 渲染；所有按钮有忙碌及错误状态。保留 session_id 以恢复刷新后的会话。
- [ ] 浏览器检查窄屏和桌面、键盘焦点、422/502 与任务失败提示、旧版本标记，不展示伪造成功音频；提交。

## Task 7 真实联调与交付

**Files:** README.md、docs/acceptance.md、docs/recording-script.md；既有测试和配置。

- [ ] 运行 pytest、ruff check、mypy 和 git diff --check；记录真实结果，不将网络隔离测试称为真实供应商联调。
- [ ] 提示用户在本地 .env 填写 DeepSeek 和 DashScope 密钥；无密钥时保留待验证状态，不伪造通过。
- [ ] 用真实材料验证默认制作、预览后制作、两轮修改、音色变更、局部复用、播放下载；保存脱敏的 trace_id 验证记录。
- [ ] 编写安装、FFmpeg、配置、启动、浏览器地址、版本与缓存规则、编排图、护栏依据、框架差异和本机部署边界。
- [ ] 编写四段录屏脚本和验收清单；实际成员贡献及学校报告按用户提供的信息填写，不虚构。
- [ ] 复核 Git 不包含密钥、音频、数据库或日志；提交代码。远程推送依用户授权执行。

## 计划自检

功能规格中的输入、设置、三 Agent、双人音频、多轮修改、片段复用、错误状态、结构化产物、追踪、测试与文档均映射到上述任务。供应商参数核实是 Task 1 的实际工作，不能用猜测值跳过；网页风格选择是 Task 6 的前置步骤；真实密钥和学校报告模板是用户提供的外部依赖。
