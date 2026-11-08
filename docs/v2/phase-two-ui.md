# 第二阶段：工作台与文件管理布局重构

工作区 `/opt/bili-remote`；分支 `v2-integration`；起点 `679b7c7`。保留第一阶段 15 个未提交文件，开始前把这些文件、差异、校验值和基准测试报告保存在忽略目录 `.dev/phase-two-baseline/`。没有提交、切换、重置、清理工作区或部署。

## 实测与布局

先启动现有测试夹具提供的随机回环服务，实测原始页面：1440/1280/1024 下全局导航均为 **184px**，768/390 下沿用已有的 240px 导航抽屉。原工作台叠加页面 32px 和组件 28px 留白，1440 下布局实际宽度仅 1136px。现在两个目标页面使用 16px 外边距，900px 以下 12px，不再叠加工作台内边距。

| 屏幕 | 内容净宽 | 发种工作台 | 文件管理 |
| --- | ---: | --- | --- |
| 1440×900 | 1224px | 250 / 约645 / 300px，间隔约14.4px | 210 / 690 / 300px，间隔12px |
| 1280×800 | 1064px | 250 / 约488 / 300px，间隔约12.8px | 210 / 530 / 300px，间隔12px |
| 1024×768 | 808px | 资源抽屉，编辑/预览切换 | 目录与列表常驻，详情侧滑 |
| 768×1024 | 744px | 资源抽屉，编辑/预览切换 | 目录抽屉、单列列表、详情侧滑 |
| 390×844 | 366px | 资源抽屉，编辑/预览切换 | 目录抽屉、单列列表、全屏详情 |

工作台与详情栏在 1180px 以下改变呈现；全局导航继续使用已有 900px 断点。Grid 使用 `minmax(0,1fr)`，文件名与发布文本可换行，侧栏各自滚动。窄屏保留触控尺寸，不通过缩小字号和按钮实现适配。

工作台的资源栏、中间编辑/工具区、右侧预览成为三个同级 DOM 节点。媒体识别、人工参数、规则、高级命名和逐文件参数保留折叠入口；发布内容默认显示，标题、副标题、简介独立编辑，删除重复字段镜像。完整预览独立于四个工具面板，保留三种格式、效果/源码切换、完整复制、实时组合和服务端缺项提示。

资源栏仍使用原 FileBrowser compact 实例。四根目录固定在组件顶部；树、当前目录搜索、排序、列表、分页与选择共用原逻辑。底部保留唯一“使用所选资源”按钮及候选说明。抽屉关闭仅改变布局状态。

文件管理的根目录和树移至左栏，中栏分为导航/搜索排序、选择操作、列表和分页；右栏增加可收起详情。无选择、单文件、多文件、目录各有独立呈现，多选显示数量、文件合计和列表，不隐式预览第一项。图片/文本使用已有读取接口，视频媒体信息复用原适配器；“查看已有截图”从最近 200 条真实记录中按资源路径及指纹匹配可用图片，不创建视频播放器。

常用操作直接展示；移动、智能命名、截图、报告、上传、制种和发布资料保留在“更多操作”，删除独立分组。详情快捷操作调用原按钮处理流程，文本编辑、冲突检查、预览令牌及安全确认均复用原实现。工具历史和旧版入口保留。

## 组件与样式归属

- 复用原工作台实例、字段节点、任务/产物状态、API 客户端、预览渲染器、高级命名模块和每个页面原 FileBrowser 实例。响应式切换不调用 mount/destroy。
- FileBrowser 的 full、compact、picker 样式集中在共享 `filebrowser.css`。从 workspace.css、ui.css、assistant.css、design-system.css 移走相应组件布局选择器；旧 assistant 的专属嵌入选择器及文件操作弹窗逻辑保留。
- workbench.css 重整为三栏、工具区、发布预览、资源抽屉规则，删除重复 `.wb-files .file-*` 实现。
- 新增局部主题令牌，支持系统浅深主题及 `data-theme` 覆盖；仅目标页面、共享浏览器及相关弹窗使用。导航/顶栏仅在这两个页面激活时跟随主题，其他三个页面的布局没有重构。
- 文件类型、树展开按钮及两个目标导航入口使用本地 Lucide SVG 子集，附许可文件。没有引入前端框架。

## 第一阶段保护与业务回归

除运行测试，还逐方法对比了开始时保存的第一阶段源码：工作台 `select`、`resolve`、`generate`、`applyRename`、`refreshTasks`、`track` 没有改写；FileBrowser 的 `performAction`、`startOperation`、`openRename`、`openTextEditor`、`openPreview`、`confirm` 没有改写。

保留候选/正式资源分离、人工编辑确认、请求序号与资源版本、慢响应丢弃、命名预览失效、任务归属、跨页面资源交接。新增视图状态不写业务状态；资源入口交接后只切回编辑视图。新详情的异步文本、媒体信息和截图同样检查选择版本。

第一阶段独有的下载 JS/模板、高级命名 JS、适配器回归测试、三模式测试和第一阶段交付文档内容保持原样。共享/重叠文件在其基础上增量修改。

真实业务验证包含：临时文件文本保存与冲突、复制/移动/删除与确认、普通和高级改名/撤销、真实视频探测、截图/拼图、报告、制种、任务产物与上传/下载入口。外部图床、qBittorrent 等继续使用项目已有测试 transport；没有读取生产配置、数据库或资源，没有联系真实第三方账号。

## 验证结果

开始前全量基准：**393 passed，0 failed，273.02 秒**。

- 工作台模块：27 passed。
- 文件浏览模块：35 passed。
- 整合模块（含新视觉矩阵与详情回归）：35 passed。
- 最终全量：`python3 scripts/dev_test.py all`，**404 passed，0 failed，0 errors，0 skipped，325.11 秒**。第一阶段 393 项全部保留，新增 11 项。报告：`.dev/test-results-all.xml`。
- 新增 11 个测试实例：五尺寸 × 两主题布局矩阵，加迟到文本/真实媒体截图/手机原编辑器回归。矩阵验证原节点和实例身份、编辑内容、资源版本、工具切换、三栏几何、详情收起扩展和无横向溢出。
- 原独立入口构建一致性、修改 JS 语法及 `git diff --check` 通过。

测试首轮暴露并已处理的验收适配：CSS 共享导入需要使用真实静态 URL；旧测试需按新的折叠和更多菜单路径点击；测试宿主的临时端口范围包含浏览器禁止端口，夹具改为选择 >=20000 的随机空闲回环端口，不改系统配置。原 Starlette/httpx 弃用警告仍保留，未升级环境。

[五尺寸、两主题真实截图索引](/opt/bili-remote/.dev/phase-two-visual/README.md)。含桌面三栏、窄屏编辑/预览、资源抽屉、目录抽屉、文件列表与详情、真实媒体信息和已有截图。另在同一临时应用中对照重构前/后的全局 CSS，首页、B站下载、设置五档宽度共 15 组布局测量一致，记录在 `.dev/phase-two-visual/other-pages-layout-comparison.json`。原页面及其他页面前后截图在 `.dev/phase-two-baseline/`、`.dev/phase-two-current/`；异步加载状态可能造成像素差异，已核对其布局和入口，未把截图完全相同作为唯一验收标准。

## 实际修改文件（相对第一阶段工作区）

- [bili_remote/static/assistant.css](/opt/bili-remote/bili_remote/static/assistant.css)
- [bili_remote/static/file-browser.js](/opt/bili-remote/bili_remote/static/file-browser.js)
- [bili_remote/static/ui.css](/opt/bili-remote/bili_remote/static/ui.css)
- [bili_remote/static/v2/filebrowser/adapter.js](/opt/bili-remote/bili_remote/static/v2/filebrowser/adapter.js)
- [bili_remote/static/v2/filebrowser/filebrowser.css](/opt/bili-remote/bili_remote/static/v2/filebrowser/filebrowser.css)
- [bili_remote/static/v2/naming/naming.css](/opt/bili-remote/bili_remote/static/v2/naming/naming.css)
- [bili_remote/static/v2/shared/design-system.css](/opt/bili-remote/bili_remote/static/v2/shared/design-system.css)
- [bili_remote/static/v2/shared/icons.LICENSE](/opt/bili-remote/bili_remote/static/v2/shared/icons.LICENSE)
- [bili_remote/static/v2/shared/icons.svg](/opt/bili-remote/bili_remote/static/v2/shared/icons.svg)
- [bili_remote/static/v2/shared/integration.js](/opt/bili-remote/bili_remote/static/v2/shared/integration.js)
- [bili_remote/static/v2/shared/workspace-theme.css](/opt/bili-remote/bili_remote/static/v2/shared/workspace-theme.css)
- [bili_remote/static/v2/workbench/index.html](/opt/bili-remote/bili_remote/static/v2/workbench/index.html)
- [bili_remote/static/v2/workbench/workbench.css](/opt/bili-remote/bili_remote/static/v2/workbench/workbench.css)
- [bili_remote/static/v2/workbench/workbench.js](/opt/bili-remote/bili_remote/static/v2/workbench/workbench.js)
- [bili_remote/static/workspace.css](/opt/bili-remote/bili_remote/static/workspace.css)
- [bili_remote/templates/index.html](/opt/bili-remote/bili_remote/templates/index.html)
- [bili_remote/templates/v2/workbench/workbench.html](/opt/bili-remote/bili_remote/templates/v2/workbench/workbench.html)
- [docs/v2/phase-two-ui.md](/opt/bili-remote/docs/v2/phase-two-ui.md)
- [tests/test_browser_modules.py](/opt/bili-remote/tests/test_browser_modules.py)
- [tests/test_browser_settings.py](/opt/bili-remote/tests/test_browser_settings.py)
- [tests/v2/filebrowser/test_interactions.py](/opt/bili-remote/tests/v2/filebrowser/test_interactions.py)
- [tests/v2/integration/test_main_workbench.py](/opt/bili-remote/tests/v2/integration/test_main_workbench.py)
- [tests/v2/integration/test_phase_two_layout.py](/opt/bili-remote/tests/v2/integration/test_phase_two_layout.py)
- [tests/v2/integration/test_stage_one.py](/opt/bili-remote/tests/v2/integration/test_stage_one.py)
- [tests/v2/workbench/test_browser.py](/opt/bili-remote/tests/v2/workbench/test_browser.py)

## 边界与停止点

没有后端/API/数据库变更，没有改造识别或 TMDB，没有发布草稿持久化，也没有视频流播放能力。图片/文本和既有截图可预览；视频播放仍等待有合法读取接口时再单独实现。截图详情明确提示最近 200 条记录的检索范围，完整高级工具入口继续可用。

验收使用现有 Python 3.11 与 Chromium；没有升级系统、启动生产 compose、占用 8111 或重启服务。真实第三方服务联调及其他浏览器引擎不在此次隔离验收范围。

本阶段完成后停在未提交、未部署状态，等待人工验收，不自动继续其他页面或下一阶段。
