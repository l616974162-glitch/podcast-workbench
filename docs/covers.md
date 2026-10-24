# 节目封面使用与验收

采用用户选择的 A 同屏布局。网页保留大材料框和紧凑说话人行，节目区提供节目／脚本／封面三个页签。

## 使用

1. 先生成脚本，或恢复已有脚本会话；制作音频不是生成封面的前提。
2. 打开封面页签，选择插画、摄影感或极简抽象。
3. 点击根据脚本整理，由独立 DeepSeek 封面策划 Agent 返回描述与简短说明。也可直接填写画面描述，跳过该次 LLM 调用。
4. 编辑并确认描述，点击生成节目封面；只有这一步调用 Agnes。默认 1K、1:1、无文字，每次一张。
5. 成功后在节目页看到缩略图，在封面页预览、下载 PNG；重新生成不重写脚本或配音。

图片和音频独立保存。失败保留已有结果；脚本新版本出现后显示旧封面提示，由用户选择是否更新。

## 本地配置

在 .env 填写 AGNES_API_KEY。默认 AGNES_IMAGE_URL 为 https://apihub.agnes-ai.com/v1/images/generations，AGNES_IMAGE_MODEL 为 agnes-image-2.5-flash。保存后重启后台。

缺少 Agnes 密钥时，只禁用生成封面；已有 DeepSeek 配置仍可整理描述，原脚本和配音正常使用。不把密钥发送到浏览器，不在控制台或日志回显密钥。

官方当前价格不作为项目的永久免费承诺，实际权限、配额和收费以用户账户为准。官方说明见 https://wiki.agnes-ai.com/en/docs/agnes-image-25-flash 和 https://wiki.agnes-ai.com/en/docs/pricing 。

## 接口

POST /chat 新增两个 action，均需 session_id 和当前 revision_id：

```json
{
  "action": "plan_cover",
  "session_id": "当前会话 UUID",
  "revision_id": "当前脚本版本 UUID",
  "cover": {"style": "illustration", "prompt": "可选的画面要求"}
}
```

generate_cover 使用相同结构，cover.prompt 必须非空，且不调用任何 LLM。style 可为 illustration／photo／abstract。不接受替换原文、节目设置或 preview_only，避免进入原脚本制作分支。

仍返回 HTTP 202 与 job_id，查询 /jobs/{job_id}。策划任务返回 cover_plan，生成任务返回对应 image 产物。/sessions/{id} 新增 cover_plan、published_cover、cover_is_stale。图片通过 /artifacts/{id} 读取，?download=true 下载 PNG；不返回本地路径。

GET /health 新增 image_configured 和 image_model，不证明鉴权或服务可用性。缺密钥 503、旧版本或会话忙 409、无效参数 422；后台图像失败标记 image_error。独立请求依旧具有自己的 HTTP trace_id，任务正文保留制作 trace_id。

## 实现与安全

cover_planner 的节点与 LLM 日志、Agnes 的工具日志由制作 trace_id 串联。计划按任务保存，图片绑定不可变 revision_id。创建任务时在同一 SQLite 事务中核对版本，避免用户确认的旧脚本与实际制作版本不一致。

图像协议注册为 agnes_image，优先处理 Base64；URL 输出仅从 config.yaml 的已允许 HTTPS 域名读取，不跟随跳转、不转发 Authorization。响应与下载均有字节上限；Pillow 验证格式、正方形尺寸、完整像素后重新编码成 PNG，再保存和发布。未使用占位图片充当生成结果。

## 当前验证边界

自动测试覆盖完整策划／生图生命周期、图片下载、失败保留、旧版提示、版本事务保护、缺密钥、危险下载地址、重定向、超限、损坏图片和鉴权头隔离。供应商网络在这些测试中被隔离，不能当作真实 Agnes 证据。

浏览器检查覆盖三个页签、手动描述和画风提交、策划结果恢复、生成失败保留、旧封面提示和原有脚本／音频流程；真实本地会话可恢复，缺密钥时正确禁用生成按钮，并保留 Word 下载。用户选择先验收界面，尚未调用真实 Agnes 服务。

后续用户本地填好密钥后，需要验证一次真实图片生成、缩略图与 PNG 下载，并在日志中对照相同 trace_id。若服务返回新的 CDN 域名，先核实官方来源再调整 download_hosts，不放开任意 URL。

本轮最终结果：99 项 pytest、ruff、mypy 22 个源文件检查通过；浏览器交互及 375／768／1024／1440 屏宽通过。
