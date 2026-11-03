# 轻量发种工作台并行开发任务

工作区 `/opt/bili-remote-wt-workbench`；分支 `codex/workbench-v2`。本阶段不开发业务；后续按用户指令执行，先读 AGENTS 和统一契约。正式补充明确禁止旧项目/草稿模式，旧 README 中相关流程不适用于 V2。

## 功能范围

左侧复用 FileBrowser，右侧四视图为发布内容、缩略图、媒体报告、种子制作；识别和命名基础区始终可达。选单视频/整季或接受下载完成的 ResourceRef，调用同一 Naming/Resolver 加载规则、探测和预览，用户确认后改名。不得重新上传资源或新建资源项目。

在本模块内建立独立的 `release_title.py:ReleaseTitleBuilder` 和 `publishing.py:PublishingService`。主标题从统一参数及独立站点模板生成，不能直接复制文件名。副标题只调用 SubtitleService；简介复用现有只读能力并允许人工编辑。三者独立编辑复制，组合预览即时更新 BBCode/HTML/Markdown，慢响应不能覆盖新 revision。

复用截图/拼图/图床/MediaInfo/BDInfo/制种/qBittorrent 工具；任务状态可刷新恢复、完成通知去重，但不能恢复发布草稿。添加 qBittorrent 须用户明确操作，不向 PT 自动发布。侧边栏仅五入口，媒体工具为内部视图；导航修改交整合会话。

## 修改文件范围

仅 `bili_remote/v2/workbench/`、`bili_remote/static/v2/workbench/`、`bili_remote/templates/v2/workbench/`、`tests/v2/workbench/`、`docs/v2/modules/workbench/`。CSS 加模块命名空间，不改全局布局。FileBrowser 只能消费 filebrowser 模块导出，不复制其源码。

## API 依赖与数据结构

消费 ResourceContext、SubtitleResult、RenamePreview、ArtifactRef、TaskRef；实现 ReleaseTitleResult/PublishPreviewResult 和独立 `/api/v2/release-title/render`、`/api/v2/publishing/preview` router。依赖 NamingService、SubtitleService、现有媒体工具及 TaskCenter 只读投影。

V2 新流程禁止调用 `/api/publish/sessions*`、`/api/releases*`、metadata.save 或任何发布保存端点。不以 release_document_id/project_id/session_id 作为流程前置条件；报告适配只引用实际媒体报告产物。任务 input/result、localStorage 和 telemetry 不得写完整 context、主/副标题、简介或完整内容。旧表/路由暂保留兼容，本模块不删除。

## 测试要求

`python3 scripts/dev_test.py workbench`；新增 tests/v2/workbench/ 自动加入。测主标题与文件模板不同、三职责分开、人工编辑保护、缺信息 ready=false 但允许预览复制、三格式转义、过期响应丢弃、同一 context 跨工具使用。

用存储 spy/临时 SQLite/HTTP 请求拦截证明端到端流程零项目/草稿写入，刷新只恢复后台进度。测试截图/报告/制种实际产物引用、取消/重试/通知；图床与 qBittorrent 使用 fake 默认，不使用真实账号。浏览器覆盖 390/768/1440 宽度、四视图、识别区可达和独立复制。

## 交付标准及公共文件禁改

交两个独立 builder/composer、模块 UI/模板片段、router、契约/浏览器测试及 handoff.md，列主页面挂载、工具适配和任务中心所需公共变更。ReleaseTitleBuilder/SubtitleService/NamingService 不能合并成一个大服务，不能让 JS 生成业务文本。

禁改 web.py、database.py、services.py、main.py、config.py、index.html、app.js、现有 assistant.js/modules.js/media-tools.js/workspace.js、全局 CSS/API 注册、迁移入口、旧 publishing.py/release_info.py、公共模型、全局设置、依赖和测试夹具；其他公共文件同 file-ownership.md。
