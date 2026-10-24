# 后端第一版验收步骤

目前不连接网页。以下命令由用户在仓库根目录的 PowerShell 执行。真实模型和语音调用会使用账户额度；本文件不是已完成真实联调的证明。

## 准备

```powershell
uv sync --extra dev --locked
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

在本地 .env 填写 DEEPSEEK_API_KEY 和 DASHSCOPE_API_KEY。千问地址对应北京地域，密钥需匹配地域。不要把 .env 提交到 Git。FFmpeg 可用随包版本，无需另改系统 PATH。

```powershell
& .\.venv\Scripts\python.exe -m podcast_workbench doctor
& .\.venv\Scripts\python.exe -m podcast_workbench check-config --require-secrets
```

预期：配置和 FFmpeg 检查通过。密钥已配置仅表明变量有值，不证明服务鉴权成功。

## 先生成脚本

```powershell
$podcastLines = & .\.venv\Scripts\python.exe -m podcast_workbench run --request-file examples/create-preview.json
if ($LASTEXITCODE -ne 0) { throw "脚本生成失败，请检查命令输出。" }
$podcastAccepted = $podcastLines[0] | ConvertFrom-Json
$podcastResult = $podcastLines[-1] | ConvertFrom-Json
$podcastSessionId = $podcastAccepted.session_id
$podcastAccepted
$podcastResult
```

检查标题、简介、主持人／嘉宾脚本和审校摘要。预览不应产生 audio 产物，日志应该包含三个 LLM 节点。原文不足时允许时长短于目标，不允许添加无依据事实。

## 同一脚本生成双人音频

```powershell
New-Item -ItemType Directory -Path data -Force | Out-Null
@{
    action = "render_audio"
    session_id = $podcastSessionId
    settings = @{ host_voice = "Cherry"; guest_voice = "Ethan" }
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath data/render-request.json -Encoding utf8
& .\.venv\Scripts\python.exe -m podcast_workbench run --request-file data/render-request.json
```

检查终态 succeeded、本地 WAV 存在且可打开试听、两种声音顺序正确、duration_seconds 为正。日志应有 tool 和 audio_merge 事件，不应再次调用 LLM。

## 连续修改和音色保留

```powershell
@{
    action = "revise"
    session_id = $podcastSessionId
    message = "保留原材料事实，讲得更通俗，让主持人多提一个澄清问题。"
    preview_only = $true
} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath data/revise-request.json -Encoding utf8
& .\.venv\Scripts\python.exe -m podcast_workbench run --request-file data/revise-request.json
```

应创建新脚本版本，保留原材料、其他设置和旧成功音频。可修改请求文件中的 message 再运行一次，验证上下文。之后再次运行 render-request.json 制作新版音频；不变的片段可出现 cache.hit，变化片段才调用千问。cache.hit 不表示供应商调用。

要调整音色，修改 render-request.json 中实际支持的 voice 值；不得把同一种音色分配给两人。新音频成功后，音色选择应保留到后续制作。

## 调用证据与结果记录

```powershell
Get-Content -LiteralPath logs/agent.log | Select-String -SimpleMatch $podcastAccepted.trace_id
```

按同一 trace_id 核对 run → node → llm／tool → audio_merge；每次新操作有新 trace_id，同一节目保留 session_id。检查日志不含密钥、原文、签名 URL 或供应商错误正文。

实际验收后记录：操作时间、使用的模型和音色、各次 trace_id、脚本版本、实际音频时长、是否可播放、失败及重试结果。不要记录密钥，不用自动测试的虚构 trace_id 代替真实调用记录。

## 自动检查

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe -m mypy
```

已验证自动测试 76 项；涉及供应商的测试使用网络隔离，LangGraph、SQLite、FFmpeg 和 API 集成实际执行。真实供应商鉴权、额度、音色质量及完整浏览器验收仍待用户操作。

如果需要单独测试 HTTP 接口，先结束 CLI，再按 README 启动本地 API，用 PowerShell 或 API 客户端验收；不需要连接网页，不要同时运行两个后端进程。
