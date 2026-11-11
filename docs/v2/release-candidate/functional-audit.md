# Bili Remote 2.0 功能验收矩阵

日期：2026-11-11
源码候选提交：`7659e48c251fb9546b21911cd9832844ae55231e`
分支：`codex/final-ui-v2`
工作区：`/opt/bili-remote-wt-final-ui`

## 结论口径

本矩阵不以“页面存在按钮”作为通过条件。每一行必须同时有三类证据：真实业务接口或既有契约、用户可完成的页面操作、实际执行过的测试或完整链路。结论按以下口径记录：

- **真实通过**：使用真实文件系统、SQLite、FFmpeg、ffprobe 或 MediaInfo 执行，并校验结果。
- **离线通过**：业务队列、状态机、接口契约和下游映射已执行，但外部账号、上游服务或进程使用经明确隔离的替身。
- **外部阻塞**：没有合法测试凭据、授权资源或独立测试实例，不能标记为在线通过。
- **未发现新增失败**：本轮全量范围没有失败或跳过；不代表未配置的外部服务已被在线验证。

## 页面与业务入口

| 模块 | 功能与用户操作 | 主要入口/接口 | 验收结果 | 权威证据与边界 |
| --- | --- | --- | --- | --- |
| 首页 | 最近资源来自真实文件；打开文件并定位资源 | `/api/files/browse`、文件管理深链 | 真实通过 | `test_final_ui_home_and_settings_real_save`、`test_final_ui_flow` |
| 首页 | 最近下载完成只来自真实完成记录 | jobs/download 任务投影 | 真实通过 | 完成记录由隔离下载进程生成，队列、文件注册和完成状态为真实服务 |
| 首页 | 后台任务进度、错误、重试与通知 | `/api/v2/tasks`、原任务接口 | 离线通过 | `test_task_identity_notifications_and_failures`、`test_final_ui_task_states_retry_and_long_content` |
| 首页 | 最近工具产物只来自真实工具记录 | 截图、报告、种子及任务产物记录 | 真实通过 | 完整流程生成真实截图、MediaInfo 和 `.torrent` |
| 首页 | “继续处理”进入真实资源；“进入工作台”传递同一资源 | `/api/v2/resources/resolve` | 真实通过 | `test_final_ui_flow` 从完成任务进入文件管理再进入工作台 |
| 首页 | 不创建发布草稿或伪造恢复正文 | 无持久化发布接口 | 真实通过 | `test_real_api_rename_undo_and_no_drafts`；刷新只恢复任务 |
| 文件管理 | 四类根目录、真实目录树、列表、搜索、排序 | `/api/files/roots`、`browse`、`meta` | 真实通过 | FileBrowser 回归及 `test_final_ui` 浏览器矩阵 |
| 文件管理 | 单选、多选、整季目录选择；批量条按选择状态出现 | 共享 FileBrowser 状态 | 真实通过 | `test_final_ui_edges`；未选择时批量条隐藏，不显示无意义禁用按钮 |
| 文件管理 | 父目录与子文件同时选择时规范化去重 | `/api/v2/naming/preview` | 真实通过 | `test_real_naming_matrix_preview_conflict_apply_and_undo` 断言三条视频无重复 |
| 文件管理 | 复制、剪切、粘贴、移动、删除 | `/api/files/operations/preview`、`apply` | 真实通过 | 原 FileService/FileBrowser 安全回归；显式预览与确认 |
| 文件管理 | 单文件重命名、文本编辑、图片预览、媒体信息 | 原文件服务、text/image/meta API | 真实通过 | `test_phase_two_layout`、FileBrowser 与文件安全测试 |
| 文件管理 | 危险操作路径、冲突和覆盖预检 | 既有安全接口 | 真实通过 | 越权、路径穿越、目标存在及并发冲突测试均保持拒绝 |
| 文件管理 | 选中资源进入发种工作台且不重复选择 | `/api/v2/resources/resolve` | 真实通过 | 完整链路校验同一资源路径和文件内容 |
| B站下载 | Web/TV 线路界面、登录状态、扫码和退出 | 原 `/api/bili/*`、`/api/bili/tv/*` | 外部阻塞 | 离线契约通过；没有合法测试账号，未做在线登录 |
| B站下载 | 空态、解析中、成功、失败，迟到响应不覆盖新输入 | `/api/resolve` | 离线通过 | `test_final_ui_download_states_and_episode_quality` |
| B站下载 | 普通视频、分P、番剧、合集与多选集 | resolve/episodes/formats API | 离线通过 | 下载模块回归及 120 集浏览器状态测试 |
| B站下载 | 统一画质、单集画质和编码选择 | `formats`、`formats/batch` | 离线通过 | 选择器来自解析响应契约；未连接真实 B站格式服务 |
| B站下载 | 提交批次、排队、进度、失败详情、取消和重试 | `/api/jobs`、`jobs/batch` | 离线通过 | 浏览器任务状态与实际 worker 执行；外部下载器进程为离线替身 |
| B站下载 | 完成后打开文件管理，并保留原始文件名 | `/api/v2/downloads/{id}/recognize` | 真实通过 | 隔离下载进程写出真实视频；下游注册和文件路径为真实服务 |
| B站下载 | 完成后进入工作台，资源路径正确传递 | `/api/v2/downloads/{id}/recognize` | 真实通过 | `test_final_ui_flow` 校验真实输出文件与目标资源 |
| 设置 | 分类导航、搜索、行内编辑和真实 schema | `/api/settings/schema`、`GET/PUT /api/settings` | 真实通过 | `test_final_ui_settings_search_and_categories`、设置中心测试 |
| 设置 | 保存后重新读取且非法值不产生部分写入 | 设置服务 | 真实通过 | `test_runtime_and_browser_defaults_persist_and_apply` |
| 设置 | 凭据脱敏，不回显明文密钥 | 设置 schema/API | 真实通过 | `test_settings_schema_and_responses_do_not_leak_credentials` |
| 设置 | 活跃下载时阻止影响运行任务的不安全运行时修改 | 设置中心 | 离线通过 | `test_active_download_blocks_only_idle_runtime_settings`；外部下载未在线复验 |
| 设置 | 目录、下载、命名、副标题、截图报告、图床种子、影视源、日志系统均可访问 | 现有设置注册表 | 真实通过 | 分类覆盖和真实保存/重载浏览器测试 |
| 工作台 | 当前资源、路径、数量、季集、媒体摘要和资料状态 | 资源解析、媒体探测 | 真实通过 | 真实 ffprobe 与浏览器完整链路 |
| 工作台 | 更换资源时保留人工编辑并防止迟到响应覆盖 | 资源上下文版本控制 | 真实通过 | 工作台迟到响应、资源切换与人工锁测试 |
| 工作台 | TMDB/Bangumi 候选、多语言名称、手动名称 | metadata search/resolve | 外部阻塞 | 离线 provider 契约通过；无在线凭据与真实响应 |
| 工作台 | 文件名与影视身份分离，自动生成可编辑发布字段 | 发布标题、SubtitleService、简介编辑器 | 真实通过 | 人工值保护、单独复制、无草稿及长度边界测试 |
| 工作台 | 命名预览、逐集调整、字幕关联、规则来源和变更计数 | `/api/v2/naming/preview` | 真实通过 | `test_release_media_matrix` |
| 工作台 | 安全改名、冲突拒绝、内容一致性和撤销 | `/api/v2/naming/apply`、undo API | 真实通过 | 实际文件改名后逐字节一致，撤销后原名与内容恢复 |
| 工作台 | 全局、系列和季集规则记忆，不改写当前人工输入 | `/api/v2/naming/memory` | 真实通过 | memory、subtitle 与工作台状态测试 |
| 工作台 | 缩略图单图、多图、拼接、范围、行列和真实结果 | screenshots/collage API | 真实通过 | 真 FFmpeg 输出，浏览器确认图片可见并支持下载 |
| 工作台 | MediaInfo 报告、复制和下载 | `/api/v2/reports` | 真实通过 | 真实 MediaInfo 24.18.0 输出并进入完整预览 |
| 工作台 | BDInfo（可用时） | release-info | 外部阻塞 | 契约和失败不伪造测试通过；无合法完整蓝光样本 |
| 工作台 | 本地制作私有 torrent、Tracker、Piece Size 和下载 | `/api/torrents/jobs` | 真实通过 | 生成并下载真实 `.torrent`，文件数与总大小校验通过 |
| 工作台 | 源文件改名后旧 torrent 失效，新路径重新制种 | torrent 下载 API | 真实通过 | 旧源下载返回 `422`“源资源已变化，请重新制种”；新种子可下载 |
| 工作台 | qBittorrent 连接和导入 | qBittorrent API | 外部阻塞 | 离线 transport 契约通过；无独立测试实例 |
| 工作台 | BBCode/HTML/Markdown 完整预览、效果/源码、复制 | `/api/v2/publishing/preview` | 真实通过 | 真实工具内容参与预览；人工简介只出现一次 |
| 全局 | 任务中心恢复、重试、取消、产物下载和通知 | `/api/v2/tasks` 与旧任务接口 | 离线通过 | 任务状态机与产物下载真实；外部下载失败场景为隔离注入 |
| 安全 | API 登录、CSRF、路径越权、非法文件与请求大小限制 | 原安全层 | 真实通过 | 全量安全与 FileBrowser 回归 |
| 兼容 | `/legacy` 旧版入口、旧 API 返回结构和旧工具入口 | 旧路由 | 真实通过 | 全量原测试及浏览器旧版移动端回归 |
| 数据 | 无发布草稿、无自动保存正文、无自动发种 | 无相关持久化 | 真实通过 | 无草稿与任务恢复测试 |
| 数据 | 数据库迁移兼容和重复初始化 | 既有迁移入口 | 原库回归通过 | `test_old_database_upgrades_preserves_data_and_backs_up`、V2 合成迁移矩阵；本轮无新增迁移 |

## 已发现并修复的问题

1. 文件名中的季集证据未被用于选择剧集命名模板，导致 `Motion.Study.S01E01.mp4` 曾落入不含季集的电影回退样式。`f269c47` 修复模板选择，真实矩阵现已生成包含 `.S01E01.` 的目标名，并返回规则来源 `filename_episode`。
2. 显式片源和制作组可能被自动识别结果覆盖。`9b3ee6b` 保留人工来源与制作组 provenance。
3. 源目录改名后，历史 torrent 仍可被下载，可能把已失效的路径元数据当作当前资源。`7659e48` 在下载和导入前核对源存在性、文件数、总大小、info hash、布局、路径和逐文件大小；不匹配返回 `422`，要求重新制种。

## 仍需外部条件

- B站 Web/TV 真实登录、真实解析和获准资源下载。
- TMDB、Bangumi、PT-Gen 与 Minfo 的真实在线响应。
- 图床真实上传和公网链接。
- 独立 qBittorrent 测试实例。
- 合法完整蓝光样本及 BDInfo 实际输出。

这些项目仅完成离线契约或界面状态验证，不能计入在线功能通过。

## 人工预发布复核清单

1. 使用专用测试账号验证 B站 Web/TV 登录、普通视频、分P、番剧和合集下载，并确认完成后资源路径。
2. 配置独立外部服务凭据，逐项核对 TMDB、Bangumi、PT-Gen、Minfo 和图床返回值，日志不得记录密钥。
3. 在隔离 qBittorrent 实例中执行连接、上传和核对，不自动向真实 PT 站点发布。
4. 使用合法蓝光样本验证 BDInfo；无 BDInfo 时仍应明确显示不可用而非伪造报告。
5. 以真实任务量复核设置修改与活跃下载互锁、任务刷新恢复、移动端软键盘和长简介操作。
