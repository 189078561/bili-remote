# 工作台前端验证记录

日期：2026-11-04（Asia/Shanghai）。源码基线 `db43176`，当前开发分支 `codex/workbench-v2`。所有服务均为测试夹具创建的回环临时服务，用后关闭；合成文件与 SQLite 位于 pytest 临时目录。界面截图仅在被忽略的本 worktree `.dev/`，没有生产数据。

## 已执行命令

| 检查 | 结果 |
| --- | --- |
| `python3 tests/v2/workbench/build_frontend.py` | 通过，生成原生 JS/CSS 静态入口，无安装/打包依赖 |
| `python3 tests/v2/workbench/build_frontend.py --check` | 通过，全部前端 JS 语法与入口源模板一致 |
| `python3 scripts/dev_test.py all` | **221 passed，0 failed，0 skipped，1 warning，145.57 秒** |
| `python3 scripts/dev_test.py workbench` | **22 passed，0 failed，0 skipped，1 warning，33.18 秒**；包含最终刷新真实产物检查 |
| `git diff --check` | 通过；提交前另查暂存差异和文件白名单 |

全量包含既有 210 项与本模块 11 项 pytest 测试；其中一项还运行 Node 内置测试器的 3 个传输契约用例。Starlette/httpx 弃用提示属于既有环境警告，未通过升级依赖消除。

使用只读共享解释器 `/opt/bili-remote/.venv/bin/python`（Python 3.11.2）与现有 Node v24.18.0。正式 Python >=3.12 回归尚未验证，未升级宿主环境。

## 真实本地运行证明

- 使用真实 FastAPI 应用、现有认证/CSRF、FileBrowser/FileService、processing 队列和独立数据库。
- 原生静态入口可直接在运行本分支的测试应用中访问；登录后读取文件目录，未登录只显示登录提示。不是通过静态假数据页面验收。
- ffmpeg 在临时目录合成 3 秒视频；真实单图截图、多图拼图、图片读取及下载通过。
- 真实只读 MediaInfo 读取；真实本地种子生成及下载。报告读取没有调用报告保存 API。
- 图床使用真实服务边界和测试 ImageHostClient，qBittorrent 使用 httpx.MockTransport 模拟登录 SID 与添加接口；均没有真实外部账号和网络访问。
- 新媒体任务 `project_id=NULL`；测试检查 task input 不含 context、release_title、subtitle、overview、BBCode 或 HTML。
- 文件/截图/上传/报告/制种流程后，release_projects、publish_sessions、media_metadata、release_documents 均为零条。
- 新入口刷新后发布字段为空；已完成的真实制种任务可通过“查看产物”再次读取下载链接。
- 390/768/1440 宽度覆盖；无页面横向溢出。四视图、常驻识别区、独立复制及键盘 tablist 实现；测试使用真实浏览器 Chromium。

## 测试替身边界

当前分支没有 V2 识别/命名/主标题/副标题/组合/统一任务接口，不能把如下前端契约测试称为这些后端已完成：

- 测试专用 route interception 返回明确的合成 ResourceContext、主标题、副标题、RenamePreview 和 PublishPreviewResult。
- 验证同一上下文传递、人工编辑保护、只在明确确认后执行改名、参数与 token 契约、三格式切换及独立复制。合成改名适配器只重命名临时文件，随后通过真实文件 meta 读取新指纹并保留人工编辑；这不等于真实 NamingService 已实现。
- 延迟旧识别、主标题、组合和命名响应；资源切换、字段编辑和规则变化后旧响应不生效。
- 验证只有 artifact_refs、没有旧 result 的 V2 任务：真实引用（测试边界提供）进入图床链接区和下一次发布组合请求。
- 合成 download/processing 同 ID 任务验证身份分离、未知进度、取消/重试、完成通知去重和刷新不恢复文案。
- HTML 无权限 iframe、BBCode/Markdown 安全 DOM 渲染；不信任标题中的 HTML 或 javascript 链接。
- 另有**不拦截 V2 的真实缺失接口测试**：选视频或目录后显示尚未接入、主标题为空、命名不可执行。

现有 BDInfo 后端测试仍通过，但本工作台没有用真实蓝光盘目录和实际 BDInfo 命令完成端到端验证。只读报告没有可复用 ArtifactRef，尚不能进入完整发布组合；不能用临时 ID 冒充产物。

## 完成审计

| 用户要求 | 当前证据 | 结论 |
| --- | --- | --- |
| 左文件浏览、右四工作区、桌面/移动 | 共享组件、模板/CSS、真实入口和 3 宽度浏览器测试 | 前端完成 |
| NamingService / SubtitleService 统一调用 | api.js、同 context/revision 的拦截测试 | 前端契约完成；真实服务缺失 |
| 主/副标题/简介独立编辑、预览、复制 | 实际 DOM/剪贴板行为测试 | 完成 |
| 字段变动同步完整内容 | 服务组合调度、慢响应丢弃测试 | 前端完成；真实 composer 缺失 |
| 单集和整季 | 视频/目录真实选择、scope 和结构化引用 | 入口完成；真实整季识别与命名待后端 |
| 缩略图单图/拼图、下载/图床 | 真实合成视频、下载与图床服务替身 | 本地工具完成；真实图床账号未验证 |
| MediaInfo / BDInfo | 真实视频只读报告与既有 BDInfo 回归 | 查看/复制可用；报告任务与组合引用待适配 |
| 本地制种、qBittorrent 制种 | 真实 torrent 下载；手动添加 qBittorrent 的协议替身 | 本地与添加完成；原生 qBittorrent 制种缺接口 |
| 三格式完整预览复制 | 前端拦截测试、源码与安全展示 | 前端完成；真实内容生成待后端 |
| 统一任务进度/通知/错误 | 真实 processing、合成双 kind、恢复和去重 | processing 可用；download 聚合待整合 |
| 无项目/草稿/文案本地存储 | 请求与源码审查、数据库/任务输入断言、禁止 Storage.setItem 的真实入口测试 | 本模块完成 |
| 后端缺失不伪造 | 真实 404 交互、禁用相关操作；handoff 逐项列依赖 | 完成 |
| 构建、交互与相关回归 | 构建检查、模块检查和完整 221 项回归 | 当前环境通过，Python 3.12 未核验 |
| 提交当前分支 | 本模块前端/测试/文档范围提交，交付时核对 git 状态 | 以 Git 提交记录为准 |
| 真正完整端到端 PT 工作台 | 当前缺失接口、报告产物和原生 qBittorrent 制种仍不可调用 | **未达成；不标记总目标完成** |

最终公共装配、端到端 V2 服务联通和生产部署不在本模块权限内。依赖清单、挂载方式与明确限制见 handoff.md；没有为缩小验收范围而将这些要求改为已完成。
