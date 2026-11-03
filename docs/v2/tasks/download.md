# B站下载 WebUI 与联动并行开发任务

工作区 `/opt/bili-remote-wt-download`；分支 `codex/download-v2`。当前阶段只做环境初始化，后续业务开发按用户指令进行，先读 AGENTS 与统一契约。

## 功能范围

重点为页面布局、批量选集、画质和编码选择、高级设置、任务/进度展示、通知和文件管理跳转。保留 Web/TV 双线路、扫码登录和 Cookie、番剧/普通视频/分P/合集解析、aria2、队列、失败重试、日志及下载历史。无必要不改下载引擎，不删除可用接口，不另建任务中心。

下载成功后以窄适配器发出 bvid、season_id、ep_id、cid、原始标题、剧集索引、原始路径/文件指纹；交统一识别服务探测和生成预览。默认不覆盖原名；高置信度自动改名仅由公共设置显式开启并使用 NamingService 安全执行。不得实现第二套命名或生成副标题。识别服务超时不能把下载 completed 变成 failed。

## 修改文件范围

仅 `bili_remote/v2/download/`、`bili_remote/static/v2/download/`、`bili_remote/templates/v2/download/`、`tests/v2/download/`、`docs/v2/modules/download/`。现有 downloader.py/bili.py 及 app.js 属公共兼容入口，发现必须修复的问题交整合会话，不在本分支改写。

## API 依赖与数据结构

保留原 `/api/bili/*`、resolve、episodes/formats*、jobs*、history* 请求响应；UI 从 TaskCenter 消费 TaskRef（kind=download），行为仍委托既有 jobs 引擎。来源使用 DownloadOrigin/ResourceRef，识别消费 ResourceContext 与 NamingService，不直接调上游 metadata 或创建项目。

持久来源关联由整合会话提供 download_resource_origins 仓储和完成钩子；本模块交适配器与调用建议。没有已知来源时填 null/告警，不从签名 URL 或猜测文件名编造 bvid/ep_id，不泄露 Cookie 或 token。

## 测试要求

运行 `python3 scripts/dev_test.py download`；新增 tests/v2/download/ 自动加入。现有 tests/test_downloader.py/test_validation.py/test_bili.py 必须保持。使用 fake_ytdlp 与 HTTP transport fake 验证 Web/TV、分P/合集、批量画质、排队/取消/失败重试、日志、历史与进度。

测完成事件仅一次交付（幂等）、所有来源字段传递、缺字段 null、识别服务离线仍下载成功、默认无文件写入/改名、文件跳转携带同一引用与上下文。浏览器测 390/768/1440、登录展示/批量选集/高级设置/任务通知。禁止用生产登录态和真实下载资源。

## 交付标准及公共文件禁改

交独立 WebUI 片段/控制器、DownloadCompletionAdapter、兼容样例、测试和 handoff.md；注明下载完成 hook、原 app.js 挂载及全局通知需整合会话接入。没有服务注册不算已上线，但可在测试应用独立验收。

不得修改 web.py、database.py、services.py、main.py、config.py、index.html、app.js、全局 CSS/API 注册、迁移入口、downloader.py、bili.py、processing.py、tool_jobs.py、全局设置/任务中心/公共模型/测试夹具/依赖清单或其他模块文件。
