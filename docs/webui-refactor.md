# WebUI 重构与验收记录

依据：2026-10-23 提供的 `goal-objective.md` 与 `image-1.png`。实现使用真实 DOM、文件与服务数据，没有将参考图嵌入产品。

## 功能映射与实施

| 目标 | 实现及保留的原有能力 |
| --- | --- |
| 五个入口 | 首页、文件管理、发种助手、B站下载、设置；首页去除统计卡片 |
| 桌面布局 | 左侧紧凑文件列表、中间折叠配置、右侧实时结果；对照参考图调整栏宽、顶部位置、间距、蓝色按钮与浅灰背景 |
| 手机/平板 | ≤900px 使用选择文件、配置操作、生成结果三步骤；同一 DOM 和状态，不复制业务逻辑 |
| 统一文件管理 | `FileBrowser` + `/api/files/*`；目录树、面包屑、后端搜索、分页、排序、图片预览、单/多选；保留文本编辑、重命名、复制、移动、删除、保护与路径校验 |
| 媒体与资料 | 选择后自动探测；真实 TMDB 电影/剧集搜索与详情；主标题、副标题、简介分别编辑复制；完整媒体报告直接在结果区展开、复制、下载 |
| 缩略图 | 复用截图队列的采样→最终拼接；单图使用 1×1 完整画布；请求内独立布局、宽度、数量、时间和采样参数；不修改全局默认值 |
| 最终图片 | 白底、可读文件名与媒体参数、中文字体、黑色阴影、黑底白字时间条、JPG 质量95；用户下载的就是预览中的完整图片 |
| 上传 | 原有 Pixhost/Catbox/ImgBB；异步上传最终图、URL与BBCode、明确错误、失败项目重试 |
| 发布与种子 | 可编辑预览、SQLite 发布草稿、原有完整编辑器与资料关联、真实种子下载及 qBittorrent；高级工具在助手内展开 |
| 任务 | 原有 SQLite 队列保留取消、重试与状态；页面内进度与轻量通知；浏览器保存编辑状态和任务引用，刷新重连；组合任务逐步恢复 |
| 保留部署 | FastAPI、SQLite schema、B站下载业务、已有 API、配置和 Docker 挂载未重写；构建使用原 Dockerfile/Compose |

实施顺序：检查代码及205项基线 → 参考图/许可证分析 → 统一框架与助手 → TMDB/媒体/编辑保护 → 完整缩略图 → 下载/上传/种子 → 任务恢复及文件操作 → 浏览器视觉检查和回归。

源代码修改前备份：`backups/webui-goal-before/source.tar.gz`。测试使用独立临时数据库和目录，不改动用户媒体。

## 参考来源与许可证

2026-10-23 读取的参考代码与文档：

- [minfo](https://github.com/mirrorb/minfo)：MIT；README、`usePathBrowser.js`、`path-browser.js`、`screenshot/options.go`、`screenshot_job_run.go`。参考目录导航、搜索、后台任务与采样参数组织。
- [Upload-Assistant](https://github.com/Audionut/Upload-Assistant)：当前 LICENSE 为 UAPL v1.0，包含品牌、凭据和分发限制；读取 `uploadscreens.py`、`torrentcreate.py`，仅参考上传错误分类、参数与制种流程。
- [GG-BOT Upload Assistant](https://github.com/neoxcrazy/gg-bot-upload-assistant)：仓库元数据未声明许可证且未找到 LICENSE；读取 README、`utilities/utils_screenshots.py`，仅参考上传流程，不复制源码。
- [qBittorrent](https://github.com/qbittorrent/qBittorrent)：COPYING 声明源码 GPLv2+（二进制分发 GPLv3+）；读取 `torrentcreator.cpp`、`torrentcreatorcontroller.cpp`，参考状态和制种操作。
- 用户附件 `pt-slt.sh`：核对取样、白色信息栏、30px 黑色时间条、18pt 时间文字、阴影网格与 JPG 质量95；复用项目原有 Pillow 实现并提升信息栏缩放后的可读性。

未导入这些参考项目的代码、依赖或品牌；没有复制影视封面或效果图中的示例文件作为假数据。

## 验证范围

- 改动前：205项测试通过。
- 新浏览器测试使用 Chromium、真实 FastAPI 和真实 FFmpeg 输出；覆盖单/多图生成、JPG下载、TMDB结果选择、编辑保护、上传错误/重试、组合任务刷新恢复、私有种子下载、文件复制与任务恢复。
- 布局检查：320、375、390、768、900、1024、1440、1920px，检查横向溢出与三个步骤的实际可见性。
- API增加验证：分页前搜索、路径逃逸拒绝、请求级布局、精确时间点、JPG下载头、全局配置不变、容量不足提前拒绝。
- 外部 TMDB：使用当前配置真实搜索并读取《最后生还者 / The Last of Us》的中英文详情成功。
- 外部 Pixhost：上传合成测试视频生成的完整 JPG；容器内访问原图返回 HTTP 200、`image/jpeg`。未上传用户媒体。
- 图床暂时失败、TMDB失败与并发响应竞态使用可控测试服务验证，不假称为外部网络成功。
- qBittorrent及 B站已有接口由回归测试覆盖；没有向真实 qBittorrent 添加测试种子或发起新的真实 B站下载。
- 关闭浏览器时，已经入队的任务继续执行；组合任务中尚未提交的下一步在重新打开页面后继续。浏览器本地草稿不是跨设备同步；点击“保存发布草稿”后记录进入原 SQLite 发布服务。

## 最终结果

- 全量回归：`210 passed`；随后助手内批量重命名/编辑同步相关的16项针对性回归通过。
- 相关 Python 文件 Ruff 检查通过；修改的 JavaScript 均通过 `node --check`；新增助手 JS/CSS 通过 Prettier 检查。
- `docker compose config --quiet`、镜像构建通过；已更新运行中的 `bili-remote` 容器，健康状态 `healthy`，`/healthz` 返回 `ok`。
- 部署后使用 Chromium 登录实际容器，验证五入口、真实目录、真实 TMDB 结果选择、B站下载/设置页面、390px 三步骤；无横向溢出和 JavaScript 错误。
- 部署后 SQLite `quick_check` 为 `ok`；下载任务、工具任务、配置、上传、发布草稿、种子、媒体报告的记录数与更新前一致。
- 源码和 SQLite 一致性备份位于 `backups/webui-goal-before/`。更新前确认没有活动下载或工具任务，未删除或移动用户文件。

### 可查看的验收产物

截图与图片来自隔离测试视频，彩条是 FFmpeg 真实生成的视频内容，不是伪造任务结果。

- [桌面三栏及完整结果](webui-artifacts/desktop-results.png)
- [手机结果步骤](webui-artifacts/mobile-single.png)
- [生成并下载的多图 JPG](webui-artifacts/generated-grid.jpg)
- [生成并下载的单图 JPG](webui-artifacts/generated-single.jpg)

视觉对照已检查：五入口、桌面区域比例与顶部位置、紧凑表头/文件行、六组折叠参数、蓝色生成/上传按钮、结果标签、图片完整预览、手机三步骤、下载与复制操作。样例内容使用实际媒体与服务返回值；没有复刻效果图中的影视剧素材。
