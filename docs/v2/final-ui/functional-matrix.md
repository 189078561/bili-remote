# 业务功能保全矩阵

API 均为既有契约。本轮不新建下载/文件 API。每行的回归指向现有测试组或新增浏览器场景；最终执行结果统一记录于 integration-report.md，不能仅凭入口存在认定业务验收。

| 功能 | 原入口 → 新入口 | 主要接口 | 用户操作 / 反馈 | 回归证据 |
| --- | --- | --- | --- | --- |
| 登录、退出、外观 | 原顶栏 → 用户菜单 | /api/auth/login、原 logout | 登录/退出；主题跟随系统、浅色、深色 | 原安全测试；final_product 主题/焦点测试 |
| 浏览目录、分页、搜索、排序 | 原文件浏览器 → 文件主列表/目录导航 | /api/files/roots、browse、meta | 目录导航、列表刷新、搜索/排序；真实路径和时间 | filebrowser；final_ui 文件交互 |
| 文件详情 | 常驻侧栏 → 行更多/选择更多 | /api/files/meta、text、image | 按需面板、图片、文本、媒体参数，Escape 关闭 | phase_two_layout 延迟文本及媒体产物测试 |
| 复制、剪切、粘贴、移动、删除 | 原工具栏 → 选择后的更多 | /api/files/operations/preview、apply | 预览、冲突、明确确认、后台进度；不隐式覆盖 | test_file_service、test_filebrowser、test_browser_assistant |
| 单文件重命名 | 原工具栏 → 选择更多/重命名 | /api/files/rename | 显式名称及安全校验，错误保留 | filebrowser、原重命名测试 |
| 文本预览与编辑 | 原预览 → 更多/预览或文件详情 | GET/PUT /api/files/text | 原内容编辑、保存、冲突提示 | phase_two_layout 真实文本保存 |
| 资源交接 | 文件/下载旧发布入口 → 进入工作台 | /api/v2/resources/resolve、downloads/{id}/recognize | 同一 ResourceRef，直接定位，不再选一次 | final_ui_flow；final_product 下载交接；stage_one 迟到响应 |
| 整季及父子去重 | 原批量命名 → 目录/多视频选择后命名 | 同上、/api/v2/naming/preview | 规范化后去除重复父子引用，后端展开整季 | final_ui_parent_child_selection；naming 季集测试 |
| 影视匹配 | 原主面搜索 → 更换匹配面板 | /api/v2/metadata/search、resources/resolve | 候选来源/年份/身份，多语言名称，确认后摘要 | metadata；final_ui_flow 离线上游匹配 |
| 命名快捷参数 | 原复杂主表单 → 主面名称/季/片源/组 | /api/v2/resources/resolve、naming/preview | 自动预览、原名→目标名、变更总数 | main_workbench；phase_two_layout 自动调整 |
| 逐集、字幕关联、媒体覆盖 | 原高级命名 → 规则与逐集调整面板 | /api/v2/naming/preview | 逐文件集号、关联字幕、显式参数覆盖 | naming；stage_one 保留未提交逐文件值 |
| 安全执行与撤销 | 原确认 → 查看全部变更/确认重命名；命名详情撤销 | /api/v2/naming/apply、undo/preview、undo/apply | 令牌/revision/路径/冲突/指纹预检；任务结果；撤销再次预览 | final_ui_flow 主面改名→撤销字节一致；stage_one 高级命名撤销；naming 安全测试 |
| 规则记忆 | 原工作台规则 → 设置/命名面板 | GET/PUT /api/v2/naming/memory | 全局/系列/季集规则；版本冲突不覆盖输入 | memory、subtitle；final_product 实际保存重载 |
| 主标题 | 原发布生成 → 发布内容主标题 | /api/v2/release-title/render | 自动生成、直接编辑、单独复制、人工锁 | workbench；main_workbench；final_ui_flow |
| 副标题 | 原 SubtitleService → 发布内容副标题 | /api/v2/subtitles/render | 原模板、演员限制；不强制平台；独立复制 | subtitle；workbench；final_ui_flow |
| 简介编辑保护 | 原发布编辑 → 简介编辑器 | 原无状态 compose | 字段编辑及格式按钮；异步结果不覆盖人工文本；无草稿 | workbench 迟到响应/人工锁；edges 长简介复制 |
| 完整预览 | 原按需预览 → 完整预览面板 | /api/v2/publishing/preview | 三种格式、效果/源码、复制、缺项提示；简介不重复 | workbench 格式与安全；final_ui_flow；edges |
| 截图与拼图 | 原文件工具 → 工作台缩略图 | /api/files/screenshots、screenshots/{id} | 单张/拼图、数量/范围/布局、真实结果与下载 | workbench 真 FFmpeg；final_ui_flow |
| 重截、删除单张、排序、拼接记录 | 原缩略图记录 → 文件管理/工具记录/缩略图记录 | screenshots/{id}/items/*、items/order、collage | 原完整截图编辑器及会话记录继续可用 | test_screenshots、原浏览器截图编辑测试 |
| 图床上传 | 原图床工具 → 缩略图生成链接；文件更多上传；工具记录 | /api/uploads/jobs、uploads/{id}/retry | 已生成图片上传，返回真实链接；失败重试 | workbench/stage_one 离线适配器；在线待验证 |
| 媒体报告 | 原报告工具 → 工作台媒体报告；工具记录 | /api/v2/reports、/api/files/text；原 release-info | MediaInfo / 可用时 BDInfo、真实结果、复制/下载 | 真 MediaInfo 流程；蓝光契约；BDInfo 在线样本缺口 |
| 本地种子 | 原制种工具 → 工作台种子制作；工具记录 | /api/torrents/jobs、torrents/{id}/file | 本地执行、任务、下载 .torrent，不要求 qB 原生制种 | workbench、final_ui_flow、test_torrents |
| qBittorrent | 原种子工具 → 种子结果添加；工具记录/种子与 qB | /api/qbittorrent/status、test、torrents | 人工导入、连接失败提示，未配置仍可制种 | 既有离线 transport；在线待验证 |
| Web/TV 与登录 | 原下载账号区 → 线路选择/账号面板 | 原 /api/bili/*、/api/bili/tv/* | 两线路和真实登录状态、扫码、退出 | download 与 bili 离线回归；在线待验证 |
| 链接解析 | 原输入框 → 下载来源行 | /api/resolve | 空态、解析中、成功、失败；旧响应失效 | final_ui_download；download 模块 |
| 单视频、分P、番剧、合集 | 原选择块 → 媒体/选集列表 | /api/resolve、episodes/formats、formats/batch | 全选/正片/反选/清空、统一/逐集可用画质 | download；final_ui_download；edges 120 集 |
| 下载提交与失败重试 | 原队列 → 下载任务列表/全局任务 | /api/jobs、jobs/batch、retry、cancel、log | 正在提交、排队、进度、失败详情、取消/重试；部分批次成功不重复 | final_ui_partial_episode_submission；task_states；原 download |
| 下载完成交接 | 原完成按钮 → 打开文件管理/进入工作台 | /api/v2/downloads/{id}/recognize | 原文件名保留；工作台才做 PT 命名 | final_ui_flow 真队列/本地进程；final_product |
| 首页资源与任务 | 原首页摘要 → 最近资源/下载完成/进度/产物 | files/browse、jobs、/api/v2/tasks | 真实继续处理入口、文件定位、重试与任务详情；不伪造恢复内容 | final_ui_home、task_states、empty 矩阵 |
| 设置保存 | 原卡片表单 → 分类行内设置 | /api/settings/schema、GET/PUT settings | 目录/下载/命名/模板/截图报告/图床种子/影视源/系统；真实保存及未保存提醒 | test_browser_settings；settings 服务；final_ui_settings |
| 设置查找与复杂值 | 原分类逐个找 → 查找设置、按需编辑 | 读取已有 schema，无新接口 | 搜索配置名/用途/键，跳转并展开真实项；凭据不回显 | final_ui_settings_search_and_categories |
| 任务恢复、权限与日志 | 原独立队列 → 顶栏任务抽屉及局部任务 | /api/v2/tasks、tasks/{kind}/{id}/*；原任务接口 | kind:id、防重复、真实重试/取消权限、产物下载；刷新恢复任务不恢复文本 | task_center、ui_shell、workbench |
| 旧版兼容 | 原页面 → /legacy 兼容路由 | 全部既有路由 | 旧工具及已有功能保留；新 V2 不创建旧草稿 | 全量原 tests/test_*；旧版手机溢出回归 |

## 离线样例边界

浏览器完整链路使用临时目录中的 FFmpeg 合成视频、真实 SQLite/队列/文件安全/命名/报告/制种；只替换外部下载进程输入与影视资料上游。故障测试明确注入离线 503 或合成失败任务。外部 Cookie、TMDB/Bangumi/PT-Gen、图床、qB 服务没有在线验收。

例如统一画质外另选一集，仍发送原 `POST /api/jobs/batch`：

```json
{"url":"https://www.bilibili.com/video/BV1xx411c7mD","episode_ids":["3"],"api_mode":"web","format_selector":"136+30280","format_profile":null,"output_template":"%(title)s.%(ext)s","output_dir":""}
```

选择器来自该集真实格式响应；此例只用于合成测试。命名确认继续传既有 preview_token、expected_revision、confirm、idempotency_key；页面不自行执行文件系统写入。
