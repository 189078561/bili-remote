# 发种工作台精确布局整改

范围：只调整发种工作台及其共享 FileBrowser 的 compact 展示；基于 `v2-integration` / `679b7c7` 的现有未提交工作继续修改。未提交 Git、未部署、未修改后端或数据库。第一、二阶段原有修改均保留。本轮开始前的工作区副本与 404 项全量测试记录保存在忽略目录 `.dev/workbench-precise-baseline/`；本轮修改前另运行工作台测试，27 项通过。

## 实际布局

保留桌面导航 184px、顶部 64px 和主内容两侧 16px。主内容可用宽度分别为 1064px / 1224px；使用 Grid 固定两侧宽度、中间 `minmax(0, 1fr)`，没有复制实例。

| 浏览器窗口 | 左侧资源 | 中间编辑 | 右侧预览 | 栏间距 |
| --- | ---: | ---: | ---: | ---: |
| 1280×800 | 220px | 540px | 280px | 12px |
| 1440×900 | 240px | 652px | 300px | 16px |
| 1024×768 | 340px 抽屉 | 编辑/预览切换 | 共用 808px 可用区 | 单列 |
| 768×1024 | 340px 抽屉 | 编辑/预览切换 | 共用 744px 可用区 | 单列 |
| 390×844 | 340px 抽屉 | 编辑/预览切换 | 共用 366px 可用区 | 单列 |

桌面三栏顶端对齐、高度相同，底部距视口 16px。页面标题区约 64px。窄屏延用原导航抽屉及 12px 页面内边距；主编辑与预览通过显示状态切换。手机工具标签可横向滚动，操作按钮保持至少 44px 触控高度。

左栏：40px 标题行、两列四根目录、当前路径、可展开目录树、一套当前目录搜索、排序与方向、选择数量与全选、44px 单行文件列表、固定“使用所选资源”及提示。目录树移动原节点到路径区的折叠入口；事件监听、节点引用、分页和目录选择保持原有实现。文件名省略显示，悬停保留完整名称。

中栏：正式资源标题与识别摘要；唯一默认关闭的“识别与命名设置”，内部保留原五个配置组和命名确认；48px 工具标签；主标题、副标题、简介独立编辑。前两项文本框高 64px，简介 120px，字段间隔 14px。原工具、任务和错误信息仍在同一中栏中。原表单节点没有克隆或重新挂载。

右栏：独立第三个 Grid 子节点。标题、复制完整内容、格式及效果/源码开关固定在顶部；真实组合内容在正文区域滚动，完整性提示位于底部。HTML 仍用原安全 iframe，BBCode/Markdown 仍用原渲染器；没有截断、二次拼接或模拟生成逻辑。

三个滚动容器分别是 `.file-list`、`.wb-main`、`.wb-preview-body`。左栏根目录及确认区不随列表滚动；右栏控制区不随长文滚动；中栏设置展开后也只在本栏滚动。旧版兼容链接从三栏下方移动到标题说明旁，仍指向 `/legacy`，避免额外整页滚动。

## 本轮实际修改文件

以下清单是相对本轮开始时的工作区，不是相对 Git HEAD；未将以前的修改计入本轮。

| 文件 | 本轮调整 |
| --- | --- |
| [bili_remote/templates/v2/workbench/workbench.html](/opt/bili-remote/bili_remote/templates/v2/workbench/workbench.html) | 合并设置容器、正式资源标题、工具 SVG、独立预览头/正文/底部、刷新及兼容链接 |
| [bili_remote/static/v2/workbench/workbench.css](/opt/bili-remote/bili_remote/static/v2/workbench/workbench.css) | 重整三栏尺寸、等高与独立滚动、字段尺寸、主题和响应式规则；替换旧规则而非追加覆盖 |
| [bili_remote/static/v2/workbench/workbench.js](/opt/bili-remote/bili_remote/static/v2/workbench/workbench.js) | 调用原刷新方法、目录树 summary 纳入抽屉焦点循环、简化候选提示；业务方法不改 |
| [bili_remote/static/v2/workbench/index.html](/opt/bili-remote/bili_remote/static/v2/workbench/index.html) | 用原构建脚本同步独立入口 |
| [bili_remote/static/file-browser.js](/opt/bili-remote/bili_remote/static/file-browser.js) | compact 移动现有目录树节点；compact 根图标、选择数量和全选文字；无新实例 |
| [bili_remote/static/v2/filebrowser/filebrowser.css](/opt/bili-remote/bili_remote/static/v2/filebrowser/filebrowser.css) | 替换 compact 布局段，列表独立滚动、单行文件名、固定控制区；full/picker 布局不变 |
| [bili_remote/templates/index.html](/opt/bili-remote/bili_remote/templates/index.html) | 仅移除工作台下方重复位置的兼容链接，实际链接已移入工作台标题 |
| [tests/v2/workbench/test_browser.py](/opt/bili-remote/tests/v2/workbench/test_browser.py) | 通过新增的设置入口访问原业务测试，保留所有断言 |
| [tests/v2/filebrowser/test_display_modes.py](/opt/bili-remote/tests/v2/filebrowser/test_display_modes.py) | 对齐 compact 目录树入口及零选中文案，继续测三模式全部行为 |
| [tests/v2/integration/test_main_workbench.py](/opt/bili-remote/tests/v2/integration/test_main_workbench.py) | 真实命名流程先展开设置 |
| [tests/v2/integration/test_stage_one.py](/opt/bili-remote/tests/v2/integration/test_stage_one.py) | 原高级命名/撤销回归先展开设置 |
| [tests/v2/integration/test_phase_two_layout.py](/opt/bili-remote/tests/v2/integration/test_phase_two_layout.py) | 精确宽度、等高、首屏字段、滚动隔离、真实内容不重复与双主题截图；继续检查其他页面及实例身份 |
| [docs/v2/workbench-precise-ui.md](/opt/bili-remote/docs/v2/workbench-precise-ui.md) | 本交付记录 |

## 功能保护与验收依据

不是只依据测试数量：对比本轮开始前的源代码，工作台的 `select`、`resolve`、`generate`、`compose`、`naming`、`applyRename`、`refreshTasks`、`track`、`probe`、`screenshots`、`torrent` 函数体逐字一致；FileBrowser 的目录加载、导航、选择、确认、复制移动删除处理、重命名、文本编辑与预览方法逐字一致。模板全部原有 `data-wb/data-edit/data-action/data-copy/data-tab/data-panel` 挂钩及数量均保留。证据在 `.dev/workbench-precise-visual/business-preservation.json`。

候选与正式资源分离、显式确认、同资源不重复识别、人工编辑保护、迟到响应丢弃、改名任务关联、撤销与安全确认仍用第一阶段原实现。布局变化只隐藏/显示既有节点。浏览器矩阵通过实际文件勾选、确认和真实本地识别/组合接口完成，检查工具切换与跨尺寸后工作台、FileBrowser、文本节点身份及资源版本不变。

截图使用隔离临时服务、合成真实短视频和文件、人工输入的独立验收简介；没有重复灌水段落，也没有用假 API 替换截图中的识别与组合流程。浅色/深色各覆盖五种窗口；另检查长预览滚动、资源抽屉与手机预览。共 27 张工作台截图（含修改前对照），见[真实截图索引](/opt/bili-remote/.dev/workbench-precise-visual/README.md)。产物位于 `.dev/workbench-precise-visual/`，其中 `before-1280x800-light.png` 是本轮修改前的真实浏览器对照图。

原独立入口匿名访问回归曾发现新增 flex 规则覆盖了根节点的 `hidden`；已将该规则限定为未隐藏的工作台，保留原登录检查，随后纳入最终全量复验。

## 验证结果

- 完整测试：`python3 scripts/dev_test.py all`，**404 passed / 0 failed / 0 errors / 0 skipped**，326.16 秒；包含第一阶段状态保护、迟到响应、改名关联、撤销及原媒体工具/文件操作回归。
- FileBrowser 模块：**35 passed**，51.51 秒；full、compact、picker 均通过。
- 布局集成测试首轮 **35 passed**（91.45 秒）；补齐窄屏实测及最终截图后复验 **35 passed**（93.12 秒）。
- 原前端构建一致性、JavaScript 语法、`git diff --check` 均通过。
- 完整套件保持本轮开始时的 404 项（第一阶段为 393 项）；本轮加强已有浏览器断言，没有删除原有测试或减少业务断言。唯一警告为既有 Starlette/httpx 弃用提示。
- 五种窗口、两种主题全部通过；桌面精确栏宽、等高、字段首屏位置、实际鼠标滚动隔离、页面无多余整体滚动、预览不重复、实例与人工编辑状态保留均有浏览器断言。

## 限制

本轮不增加视频播放或新的业务功能。外部 B站、TMDB、PT-Gen、图床及 qBittorrent 不使用生产凭据联网验收；相关业务用项目现有隔离测试覆盖。保留现有 Python 3.11 环境与 Starlette/httpx 弃用提示，未升级依赖或改动生产服务。

本轮要求的工作台布局均已完成，无待实现 UI 项。停止于人工验收，未提交或部署。
