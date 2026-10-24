# 后端接口契约

版本：v0.1.0-backend-preview。未对接前端，未开启跨域或鉴权；仅用于本机单进程。默认不启动服务，用户需要时按 README 手动运行。

## 创建与修改

POST /chat，Content-Type 为 application/json。

```json
{
  "action": "create",
  "source_text": "用户提供的文章或笔记",
  "message": "可选的制作要求",
  "settings": {"audience": "general", "style": "casual", "target_minutes": 3},
  "preview_only": true
}
```

- create 不携带 session_id，必须有 source_text；原材料保存后不被修改操作替换。
- revise 携带已有 session_id 和非空 message；settings 只合并实际传入的字段，未传字段保留。首次脚本生成失败也可通过 revise 在原会话重试。
- render_audio 携带已有 session_id，只使用当前脚本、不调用 LLM；settings 仅允许 host_voice、guest_voice。没有脚本时返回 409，不能同时指定 preview_only=true 或 message。
- 普通大众／初学者／专业听众对应 general／beginner／professional；轻松访谈／知识讲解对应 casual／educational；target_minutes 只能是 2／3／5。
- 预览仅需要 DeepSeek 密钥；完整制作还需要 DashScope 密钥和可用 FFmpeg。
- source_text 硬上限为 20,000 字符，并应用 config.yaml 中更低的限额；message 上限 4,000 字符。

成功返回 202，包含 job_id、session_id、trace_id、status。后端不会让一个 HTTP 请求等待整个制作结束。每个会话只允许一个活动任务；当前进程最多四个任务，超过时返回 409。

## 任务查询

GET /jobs/{job_id} 返回 queued／running／succeeded／failed、stage、revision_id、error_code、error_message、artifacts。阶段为 queued、researcher、writer、reviewer、tts、merge、done 或 failed。

后台上游失败以任务 status=failed 和安全错误分类返回，轮询 HTTP 本身仍可为 200；前端不能只判断 HTTP 状态。轮询建议间隔一秒，遇到终态停止。任务 trace_id 与 POST /chat 相同。

产物包含 id、revision_id、job_id、kind、mime_type、duration_seconds、audio_settings、created_at、available、url，不返回本地文件路径。任务结果只列该 job_id 的产物，不混入同一脚本后来换音色产生的文件。URL 为相对当前 API 的地址；available=false 时 url=null。

## 会话与文件

- GET /sessions/{session_id}：返回原材料、有效节目设置、消息、当前脚本、活动任务、上一版成功音频。audio_is_stale 表示旧音频属于之前脚本版本。
- GET /sessions/{session_id}/artifacts：返回所有已登记版本的产物数组；前端按 revision_id 区分，不从脚本正文猜路径。
- GET /artifacts/{artifact_id}：读取文本或 WAV，音频支持 HTTP Range；加 ?download=true 使用下载响应头。未知、丢失或越出输出目录的文件返回 404。
- GET /health：返回 llm_configured、tts_configured、ffmpeg_available、frontend_connected=false。配置存在不等于真实鉴权已验证。

GET 等独立请求会分配自己的 X-Trace-ID 响应头；任务查询正文中的 trace_id 保留原制作任务标识，不能用轮询请求头替换制作链路标识。

## 错误

错误正文包含 trace_id 和 error.code／error.message，不回显输入原文。

| HTTP 状态 | 含义 |
| --- | --- |
| 422 | 参数或操作语义不合法 |
| 404 | 会话、任务、产物或文件不存在 |
| 409 | 会话忙、没有可渲染脚本或状态冲突 |
| 502 | 同步音频环境或上游处理错误 |
| 503 | 缺少所需本地服务配置 |
| 500 | 内部环境错误，正文不含原始异常 |

后台错误分类为 upstream_error、invalid_output、audio_error、interrupted、internal_error，使用固定可读消息。强制关闭服务后不自动续跑，下一次启动将活动任务标记 interrupted。CLI 与 API 不要同时操作同一数据库，不使用多 worker。
