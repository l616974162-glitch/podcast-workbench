# 文本转双人播客工作台

将中文文章或笔记制作成双人播客，支持脚本审校、多轮修改、双音色语音、播放和下载。

当前处于设计阶段，尚无可运行应用，也未完成真实服务联调。

## 已确定的技术方向

- LangGraph 编排资料整理、播客编剧、内容审校三个 Agent。
- DeepSeek 承担文字模型调用，千问 Qwen-TTS 生成真实语音。
- 默认一次生成完整节目，可选择先预览脚本。
- FastAPI、SQLite、原生网页；完整成品成功后发布，保留旧版本。

用户已明确选择直接基于 LangGraph 开发，不依赖教师的 langraph_agent 框架。该选择与原任务书的指定框架要求存在差异，其余适用要求继续执行。

## 设计文件

- [需求与设计](docs/superpowers/specs/2026-10-24-podcast-workbench-design.md)
- [实施计划](docs/superpowers/plans/2026-10-24-podcast-workbench.md)

设计阶段没有生成密钥文件。后续 .env 仅用于本地配置，不提交到 Git。
