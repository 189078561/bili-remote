# Bili Remote 2.0 最终集成报告

本报告覆盖 `/opt/bili-remote` 的 `v2-integration`。开发、迁移和媒体验收均使用合成数据与隔离存储；没有读取生产配置、Cookie、密钥或数据库，没有部署、重启生产服务或推送。

## 分支与交付审计

初始共同基线为 `db43176`。详细文件清单、交付差异和原工作区状态见 [integration-audit.md](integration-audit.md)。五个分支的实际 HEAD 与用户报告一致，模块 worktree 无未提交修改。当前核验没有发现 Naming 与 Workbench 的 Git 状态不一致；不能从当前事实确定先前报告的具体原因，未作猜测或重置。

| 顺序 | 模块分支 | 核实的交付提交 | 集成合并提交 |
| --- | --- | --- | --- |
| 1 | codex/naming-v2 | bd80c6a，含 c9eec04 | 320ded4 |
| 2 | codex/subtitle-v2 | 7dbb1c3 | 5ead5b2 |
| 3 | codex/download-v2 | 5c09fff | e5c39b0 |
| 4 | codex/filebrowser-v2 | e206b35 | 8d16253 |
| 5 | codex/workbench-v2 | 1b6c1b1 | d4edc41 |

五次合并均无冲突；逐次检查工作区并执行模块专项。整合前公共 UI 修改保存在 `59dc6d1`；审计过程中另一会话交接的 shared 文件保存在 `ecf14e2`。用户明确确认其他会话暂停，由本会话接管。后续 UI 交接报告与补丁保留在 `ui-refactor-report.md`、`ui-refactor-followup.patch`，补丁已审查并应用，不应重复应用。Download 对公共任务文档的越权修改已恢复为共同基线内容。

公共实现提交：`7dd711e58ba416ddef77dfcc820217b813789391`（`7dd711e`）。后续文档提交仅记录最终报告与验收摘要，不修改实现。

## 最终结构与公共装配

- `bili_remote/v2/contracts/`：共享 ResourceRef、ResourceContext、影视资料、候选标题、人物、季集、媒体参数、下载来源、任务和产物模型。
- `v2/metadata/`：MetadataResolver 与 TMDB、Bangumi、可选 PT-Gen 适配器。
- `v2/naming/`：识别、规则、ffprobe、预览、两阶段无覆盖改名、关联字幕同步和撤销。
- `v2/subtitle/`：四种内置 PT 副标题与受限自定义模板；复用同一规则仓储。
- `v2/workbench/release_title.py`：独立 ReleaseTitleBuilder，以结构化字段生成主标题。
- `v2/workbench/publishing.py`：无状态 PublishingService，组合主标题、副标题、简介、图片链接与实际媒体报告，支持 BBCode/HTML/Markdown。
- `v2/storage.py`：现有 SQLite 上的规则、下载来源和重命名审计仓储。
- `v2/task_center/`：既有下载队列与 processing 队列的统一投影；没有新增执行引擎。
- `v2/runtime.py`、`v2/router.py`：认证、CSRF、服务注入、按会话隔离上下文、公共 API 与后台任务装配。
- `services.py` → 现有主入口：初始化迁移、设置、V2 服务、未完成批次恢复和原有工作线程。
- `templates/v2/`、`static/v2/`：真实主页面挂载、下载、文件管理、命名、工作台及统一任务中心。

默认 `/` 使用集成界面；`/legacy` 保留旧工作区，刷新及设置返回仍保持旧入口。旧 API、表和核心功能保留。V2 不调用旧项目/草稿保存接口，不使用 localStorage 保存发布内容。

## 公共 API

全部 `/api/v2/*` 要求现有登录会话；非 GET 请求要求 CSRF，响应 no-store，请求体最多 4 MiB。错误采用 `detail.code/message/fields`。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | /api/v2/resources/reference | 路径权限校验与统一指纹；兼容旧选择器指纹并复核变化 |
| POST | /api/v2/resources/resolve | 真实来源、资料解析与逐文件探测，返回共享上下文 |
| POST | /api/v2/resources/probe | 既有队列上的媒体探测任务 |
| GET | /api/v2/metadata/search | 多源候选搜索，未配置来源明确降级 |
| GET | /api/v2/metadata/capabilities | 当前资料源能力状态；本身不请求外网 |
| POST | /api/v2/naming/preview | 命名预览，不写文件 |
| POST | /api/v2/naming/apply | 明确确认、版本/指纹/冲突复核后入队 |
| POST | /api/v2/naming/undo/preview | 撤销预检 |
| POST | /api/v2/naming/undo/apply | 确认后安全撤销 |
| GET / PUT | /api/v2/naming/memory | 全局、系列、季规则读取与乐观锁保存 |
| POST | /api/v2/subtitles/render | PT 副标题生成 |
| POST | /api/v2/release-title/render | 独立发布主标题生成 |
| POST | /api/v2/publishing/preview | 完整发布内容即时组合，无保存 |
| GET | /api/v2/tasks | 两种队列的过滤、分页聚合 |
| GET | /api/v2/tasks/{kind}/{id} | 真实进度、状态和实际产物引用 |
| POST | /api/v2/tasks/{kind}/{id}/{cancel,retry} | 委托原执行器；文件变更禁止通用重试 |
| POST | /api/v2/reports | 无项目依赖的实际媒体报告任务 |
| GET | /api/v2/reports/{artifact_id}/file | 认证后的报告下载 |
| POST | /api/v2/downloads/{job_id}/recognize | 已完成下载进入识别和命名预览 |
| GET | /api/v2/openapi.json | 认证后的完整机器可读 API schema |

截图/拼图、图床、制种和日志继续复用原 `/api/files/screenshots*`、`/api/uploads/jobs`、`/api/torrents/jobs`、`/api/jobs/{id}/log`、`/api/tasks/{id}/logs`。原报告 API 兼容保留；V2 改用不保存发布文档的报告任务。

## 数据库迁移与持久化

迁移 5 为增量迁移，新增 `naming_memory`、`download_resource_origins`、`v2_rename_batches`、`v2_rename_items` 及 V2 任务幂等索引。复用现有 Database、settings、jobs 和 processing_tasks，不创建影视项目/草稿的重复业务模型。

规则仅保存白名单配置，命名、副标题、季集映射在同一记录分区内共存；字幕规则更新不会抹掉命名规则。`BEGIN IMMEDIATE` 与 expected_revision 防止并发覆盖。简介、生成副标题、完整发布文本、上下文和草稿标识被拒绝保存。

下载来源只保留最小结构化事实；未知 ID 为 null，不保存下载签名 URL。规则可跨进程重新读取。执行审计只在确认后创建，逐步持久化 intent；执行中断后定位真实文件恢复，绝不覆盖新文件。重启恢复排除当前批次自身的活动任务保护，同时保留其他任务的保护。预览 token 和识别上下文仅在内存，刷新或服务重启后重新选择并预览。

迁移沿用现有自动 SQLite 一致性备份机制。测试覆盖合成 v1、v2、v3、v4 库升级、已有记录保留、重复初始化、迁移 SQL 失败不记录版本。未对生产库调用迁移。

## 真实联动与关键修复

1. 下载成功后接收 Web/TV 结构化来源，以及 yt-dlp 每个实际产物的白名单字段；多分 P 保存全部实际输出路径，缺失每集事实时不复用另一集 ID。来源入库后识别、ffprobe、预览；识别失败不改变下载成功状态。只有用户确认才改名。
2. 文件管理选择视频或整季，转成统一引用进入工作台；同资源复用上下文，新资源不携带前一个影视身份。规则由真实仓储加载，命名预览来自后端。
3. 工作台使用同一上下文调用命名、主标题、副标题与内容组合，人工编辑和慢响应隔离保留。高级命名执行后重新读取实际路径、指纹并刷新共享文件管理。
4. 截图、缩略图、图床上传、MediaInfo、制种使用真实服务和既有任务队列。报告文件和工具产物可下载，生成文案不持久化。图床和 qBittorrent 的离线验收只替换外部传输，未伪装成在线成功。
5. 公共截图问题在真实 10fps/1fps 短片的 2.95 秒定位并复现。按帧间隔向前回退，并处理新版 ffmpeg 明确的“no packets/无输出帧”错误；其他错误仍报错。没有降低原测试的成功张数要求。
6. 修复模块独立加载路径、规则词汇不一致、下载适配返回形状、任务产物投影、旧界面刷新跳转、测试模块同名导入冲突及迁移版本断言。未删除测试或屏蔽异常。

## 测试结果

| 验收 | 结果 |
| --- | --- |
| Python 3.11.2 全量 | **376 passed，0 failed，0 errors，0 skipped**，235.84 秒 |
| Python 3.12.15 隔离容器全量（正式目标） | **376 passed，0 failed，0 errors，0 skipped**，251.64 秒 |
| 最终页面挂载位置修复后，3.11 / 3.12 集成复验 | **各 17 passed**，20.52 / 24.90 秒；包括主页面真实流程及 390/768/1440 布局和侧栏遮挡检查 |
| 前端构建 | canonical 模板与独立入口一致，原生 JS/CSS 构建检查通过 |
| JavaScript 语法 | 全部 20 个文件通过 |
| Python wheel | 隔离构建成功，103 个条目；V2 Python、嵌套模板和前端资源完整，无运行数据 |

全量由旧版 **210 项**、Download 7、FileBrowser 15、公共集成 17、Metadata 12、Naming 70、Subtitle 34、Workbench 11 组成。完整执行包括 API、真实 ffprobe、四种副标题、批量改名/撤销/中断恢复、下载回归、路径安全、迁移/持久化、桌面/移动端及截图→拼图→上传传输→报告→种子→完整文案组合。图床和 qBittorrent 外部传输是明确标注的替身，文件产物与应用服务是真实运行。

两次全量之后仅调整主模板挂载位置及其布局断言，随后分别补跑全部集成用例；后端和 JavaScript 没有再变更。机器可读结果、时间、分组、命令、JUnit 路径及应用摘要见 [integration-test-results.json](integration-test-results.json)。仅剩既有 Starlette/httpx 弃用警告，未屏蔽；Python 3.12 使用 ffmpeg 7.1.5、pytest 9.2.0、Playwright 1.64.1。

提交文件检查未发现运行数据库、日志、媒体、依赖目录或高置信密钥特征。代码差异空白检查通过；原样保留的历史 `.patch` 第 33 行是统一 diff 必需的空白上下文标记，单独排除其文档格式提示，未改动原补丁内容。

可复现命令：

```sh
python3 scripts/dev_test.py all
python3 tests/v2/workbench/build_frontend.py --check
docker build -t bili-remote-v2-test:py312 - < scripts/Dockerfile.test
python3 scripts/dev_test_container.py all
```

全量日志：`.dev/integration-full-py311.log`、`.dev/integration-full-py312.log`；最终布局复验日志：`.dev/integration-focused.log`、`.dev/integration-focused-py312.log`。JUnit：`.dev/test-results-all.xml`、`.dev/test-results-integration.xml`，容器对应文件位于 `.dev/py312-src/.dev/`。

容器基于本机已有 `bili-remote:1.0.0` 构建单独测试镜像；测试运行无网络，仅挂载白名单源码副本 `.dev/py312-src`，无生产数据/配置挂载，不改宿主 Python、Node 或现有生产镜像。浏览器与应用仅使用回环随机端口。原始运行日志与 JUnit XML 保存在 `.dev/`，不提交运行日志、数据库、媒体、缓存或虚拟环境。

## 外部接口配置与受阻项

独立只读核验结果见 [integration-provider-verification.json](integration-provider-verification.json)。该文件只含结构类型、状态和能力摘要，不含真实内容或密钥。

| 服务 | 配置 | 本次验收状态 |
| --- | --- | --- |
| TMDB | V2_TMDB_TOKEN；或现有加密 tmdb_api_key，支持 v3 Key/Bearer | 缺独立开发凭据；适配器及离线异常/字段测试完成，在线认证与真实资料验收受阻 |
| PT-Gen | V2_PTGEN_URL、可选 V2_PTGEN_API_KEY；或现有 ptgen_url/ptgen_api_key | 缺测试地址及认证要求；离线覆盖 /info→/search 与旧 worker 形式，在线版本/认证/结构验收受阻；始终可选 |
| Bangumi | V2_BANGUMI_ENABLED=true；可选 V2_BANGUMI_TOKEN；设置 v2_bangumi_enabled/v2_bangumi_api_key | 无凭据的公开只读 search/detail 核验通过；默认禁用网络请求，未验收私有授权场景 |
| B站 | 独立测试账号的 Web/TV 登录 | 无独立账号/Cookie；下载回归与真实本地完成产物联动通过，真实登录/网络下载受阻 |
| 图床 / qBittorrent | 独立测试服务与必要开发凭据 | 接口链路使用传输替身；真实上传/远端添加未执行 |

没有读取生产环境来判断“是否已有密钥”，没有在代码或文档中写入真实密钥。PT-Gen 不可用不阻断手动识别、命名和内容组合。TMDB/PT-Gen 在线验收分别保持未通过状态，不能用 mock 结果替代。

## 部署与回滚说明（仅文档，未执行）

1. 用户另行批准部署后，在隔离演练环境确认测试结果和外部配置；使用独立服务端口及数据目录，不占生产 8111。
2. 正式切换前由操作者制作现有 SQLite 的一致性备份，并备份部署版本及配置；禁止在仍有文件变更任务执行时切换。保留旧代码版本、数据库和媒体。
3. 使用 Python 3.12、既有依赖与 ffmpeg/ffprobe/MediaInfo；保持单应用 worker。安全重命名要求 Linux renameat2(RENAME_NOREPLACE)，不降级成可能覆盖的重命名方式。
4. 按原部署流程启动经批准的新版本。启动会自动执行增量迁移 5；核验健康、登录、旧入口、新入口、任务与规则读写。外部凭据通过部署环境/既有加密设置提供。
5. 需要回滚时先停止接收新任务并让正在执行的文件操作结束；未恢复的临时文件保留，按审计处理。优先切回旧应用版本并保留新增表；迁移没有删除或修改旧表语义，不执行降级 SQL。
6. 若必须恢复数据库备份，由操作者另行审批并先保留当前库；恢复前核对备份后的实际文件操作，数据库恢复不能自动撤销媒体文件改名。禁止直接覆盖当前数据库或删除旧库。完成文件状态核对后再恢复服务。

当前无已知未修复的离线测试失败。未完成项为表中外部在线验收；这些项仍须独立测试凭据，不能声称已经上线验收。安全边界包括单 worker、Linux 无覆盖改名、刷新后重新构建上下文，以及历史下载来源因文件另行移动而失效时从文件管理重新选择实际文件。

没有自动部署、重启、强制推送或分支删除。外部凭据受阻项是上线前必须补验的项目。
