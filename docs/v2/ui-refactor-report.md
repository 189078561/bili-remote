# Bili Remote 2.0 WebUI 统一重构审计与阶段交付

日期：2026-11-04（Asia/Shanghai）。**状态：公共 UI 基础改造已写入，整体重构未完成，不是最终验收报告。**

## 当前工作区与并发边界

本会话开始时 `/opt/bili-remote` 位于 `v2-integration`，工作树干净，尚无 `bili_remote/v2/` 或模块 V2 前端。已阅读 architecture、api-contract、data-model、file-ownership、acceptance 及模块任务。

本会话修改公共入口期间，另一整合会话在相同工作区提交并合入模块。本会话的四个公共文件修改被 `59dc6d1` 保全，两个共享文件被 `ecf14e2` 保全；这些提交、模块合并不是本会话执行的动作。后续观察到该会话继续修改数据库、截图、文件浏览器、共享契约等文件。

为遵守 AGENTS 第 3 条“整合会话独占 /opt/bili-remote 的 v2-integration”，本会话已暂停写入共享运行代码，并请求用户确定唯一继续写入的整合会话。没有重置、清理、切分支、回退或覆盖另一会话的修改。只新增本报告和独立待审补丁。

下文测试针对已注明的固定代码快照，不能据此放行持续变化中的整合 HEAD。

## 前端结构审计

- Jinja 模板 `bili_remote/templates/index.html` 集中定义页面；原生 JS 按 app → file-browser → 工具/模块 → workspace/settings/assistant 加载，无需 Node 构建。
- 旧全局样式 styles/workspace/media-tools/ui/assistant 多次重定义顶栏、侧栏、字体与断点；assistant.css 末尾还把桌面顶栏限制在侧栏宽度。
- 旧主导航已有五项，但称为“发种助手”；首页概况被 hidden；工作台为文件/配置/结果三列。
- FileBrowser、下载引擎、截图、图床、报告、种子、设置和旧发布兼容入口可复用，不应重新实现后端。
- 旧工作台仍持久化浏览器 state，含发布文案，且可保存旧草稿。这与 V2 无发布草稿契约不相符。仅更名和重排旧工作台不能算 V2 完成；须用已交付的 V2 workbench 替换主路径，保留必要旧兼容入口。
- 旧工具各自查询 processing 任务；下载页查询 jobs。全局任务入口应只投影这两个队列，不新增执行器、数据库表或前端持久队列。
- 随后合入的 Naming、Subtitle、Download、FileBrowser 以及 Workbench 文件需由唯一整合会话统一挂载。模块存在不代表 API 已注册，也不代表主页面端到端已通过。

## 本会话修改清单

| 文件 | 修改及用途 |
| --- | --- |
| `bili_remote/templates/index.html` | 五项导航措辞统一；顶部当前页面与任务通知入口；导航遮罩、跳转内容链接；首页概况可见；工作台左文件/右配置结果外框；发布内容/缩略图/媒体报告/种子四结果视图；图床链接归入缩略图；全局任务抽屉和通知容器；加载共享资源 |
| `bili_remote/static/v2/shared/design-system.css` | 颜色、字体、间距、圆角变量；按钮、输入、表格、文件行、标签、弹窗、通知、加载与空状态样式；桌面双栏；移动单列、抽屉、44px 常用触区、下载任务卡片、可折叠配置、减少动画偏好 |
| `bili_remote/static/v2/shared/shell.js` | 统一导航状态、键盘焦点、Escape/遮罩关闭、通知历史；jobs/processing 统一投影与 kind:id 身份；任务筛选、进度、错误、取消/重试/日志；终态转换通知去重；processing 读取请求复用，无新后端队列 |
| `bili_remote/static/app.js` | 下载查询结果/失败交给全局任务投影；部分错误提示转统一非阻塞通知，保留原下载接口 |
| `bili_remote/static/assistant.js` | 通知转全局入口；工具轮询用共享读取；图床结果映射到缩略图视图；保留旧编辑与工具动作，未假接 V2 服务 |
| `bili_remote/static/media-tools.js` | 工具任务读取改用共享读取入口；仍委托原服务执行 |
| `docs/v2/ui-refactor-followup.patch` | 隔离副本中已复测的后续修复和两个浏览器测试，尚未应用到共享运行代码 |
| `docs/v2/ui-refactor-report.md` | 本报告 |

所有运行数据由现有 API 提供；未加入业务演示数据，未改下载引擎或文件操作安全语义。本会话没有改模块归属的组件文件。

## 验证与发现

环境：只读复用现有 Python 3.11.2 依赖；临时数据库、合成视频、fake_ytdlp/上游测试替身。测试服务绑定临时回环端口，未使用生产 8111、生产 .env、Cookie、API Key、数据库或私有备份。未部署、重启、推送或执行生产迁移。

| 执行范围 | 结果与边界 |
| --- | --- |
| `python3 scripts/dev_test.py download` | **40 passed**，含模块独立片段和旧下载回归；不等于真实 B站联网下载验收 |
| `python3 scripts/dev_test.py filebrowser` | **30 passed**，含真实临时文件操作、权限/指纹/冲突及浏览器测试 |
| 共享工作区最初的 `python3 scripts/dev_test.py all` | 收集失败：download/naming 的 `test_browser.py`、naming/subtitle 的 `test_memory.py` 重名。未写成通过；另一个整合会话随后正在调整 runner |
| 固定副本首次 UI 浏览器检查 | **4 passed, 1 failed**；发现上传失败重试读取旧终态缓存；截图另发现桌面顶栏宽度被旧 CSS 限制 |
| 固定副本修复后针对性复测 | **3 passed**：上传失败重试与组合工具流程；五页 390/768/1440 导航、无页面横向溢出及任务抽屉；kind:id 同号任务、通知去重、文件操作禁止重试与网络错误状态 |
| 固定副本旧全量回归 + 新 UI 检查 | **210 passed, 2 failed**。一个旧浏览器断言仍匹配旧首页标题；一个新增任务测试的合成状态被真实后台轮询覆盖。已在副本更新标题断言并隔离测试响应；随后对两项失败及上传重试、多屏宽导航复测 **4 passed（24.04 秒）**。没有再次重跑全部 212 项，不将旧全量运行写成全通过 |

固定副本：`/tmp/bili-ui-review-hqkmmkh2`，由 `8d16253` 的 Git 跟踪源码加本会话 shared 文件生成，不复制任何运行配置或数据库。副本 runner 临时采用 importlib，并为 UI 检查选择旧根测试与新增 UI 测试；它不是当前整合分支所有 V2 测试的全量结果。

证据：

- `/tmp/bili-ui-review-hqkmmkh2-browser-results.txt`：首次 4 通过 / 1 失败。
- `/tmp/bili-ui-review-hqkmmkh2-recheck.txt`：修复后 3 项针对性通过。
- `/tmp/bili-ui-review-hqkmmkh2-baseline-results.txt`：固定副本旧回归输出，210 通过 / 2 失败。
- `/tmp/bili-ui-review-hqkmmkh2-final-targeted.txt`：最终 4 项针对性复测通过。
- `node --check` 与 `git apply --check docs/v2/ui-refactor-followup.patch`：通过；只验证语法和补丁可应用性，未实际应用。
- `/tmp/bili-ui-review-hqkmmkh2/screenshots/`：五页 × 390/768/1440 截图。截图中的任务、文件均为测试数据。

## 待审补丁

`ui-refactor-followup.patch` 是可审阅的普通 unified diff。它尚未写回共享运行代码，原因是两个整合会话并发写入，唯一所有者仍待确定。

1. 将 `.topbar` 明确设为 `width:100%`，将 `.account-actions` 恢复正常定位，消除旧 assistant.css 桌面规则残留。
2. `readProcessing` 不复用终态缓存，确保失败任务重试后读取服务器的新状态；真实测试已复现失败并验证修复。
3. 更新旧 `tests/test_browser_modules.py` 的首页标题断言为“首页”，保留真实工具和导航断言。
4. 新增 `tests/v2/integration/test_ui_shell.py`，包含五页多屏宽导航和任务投影/去重/失败状态测试。任务身份与通知测试中的合成记录只存在于测试，不写应用源码。

唯一整合会话确认后，应先比对当前文件再应用补丁并复跑，不能把隔离副本通过等同于当前 HEAD 通过。

## 用户验收矩阵

| 验收点 | 当前结论 |
| --- | --- |
| 五页视觉统一 | 公共组件样式基础已完成；桌面顶栏修复仅在待审补丁；V2 模块挂载后的视觉仍待检查 |
| 导航与页面切换 | 固定副本 390/768/1440 五页通过；含抽屉关闭、任务入口与焦点行为 |
| 统一任务通知 | 已有跨页投影；同 ID 不同 kind、去重、错误提示有测试；需接入新 V2 模块统一订阅，并消除剩余模块局部重复展示 |
| 所有主要按钮 | 原能力保留；下载/文件回归与旧工作台针对性流程有证据；未穷尽新模块全部按钮 |
| 发种流程连贯 | **未完成 V2 验收**；当前仍有旧草稿与浏览器拼标题逻辑，待真正装配 V2 主/副标题和无状态组合服务 |
| 下载正常 | fake_ytdlp 与模块回归通过；真实 B站/Web/TV/aria2 外网未测 |
| 文件操作正常 | 临时文件真实读写与安全测试通过；新 ResourceContext 跨模块真实联动待验收 |
| 桌面布局正常 | 固定副本导航/溢出测试通过；已检查截图并准备顶栏修复；配置与结果仍较长，需使用正式工作台分视图布局 |
| 手机布局正常 | 固定副本抽屉、单列、触区、下载卡片及三步工作流通过；新 V2 完整发种路径待验收 |

## 必须继续的整合事项

- 确定一个公共工作区写入所有者，再接入已交付 V2 workbench/download/filebrowser/naming 组件；不要继续扩写第二份旧工作台业务逻辑。
- 共用真正的 ResourceContext/revision、NamingService、SubtitleService、ReleaseTitleBuilder、PublishingService 和任务订阅，拒绝接口尚未注册时的假成功。
- 当前主页任务卡仅统计旧 processing，且“打开”仍去文件页；需要改为共用任务中心汇总下载与工具，处理读取失败状态。
- 新 FileBrowser 自带搜索与旧 assistant 搜索并存，截图可见重复搜索栏；挂载正式组件时应只保留一套。
- 媒体报告生成后的旧 `setTab("preview")` 应调整为报告视图；报告空态不得被 `renderReport` 清空而留白。
- `asset_version()` 当前显式路径清单没有两个 shared 新资源，应纳入缓存版本更新；生产打包也需包含 V2 静态资源。
- 审核工具快速完成、重试、刷新恢复、权限失败和部分失败的通知；当前抽屉为避免抢焦点而推迟聚焦行刷新，操作完成后需要验证显示及时更新。
- 设置已复用现有 schema 二级分类和未保存保护；新模块配置仍应通过整合后的白名单接入，不能假设已齐全。
- 在固定最终 HEAD 运行全部旧测试、新 V2 测试及真正主应用浏览器端到端；补 Python >=3.12 验证。未测/外部未配置项继续明确列出。

本会话没有把上述待办标记为完成，也没有授权另一会话继续部署或清理数据。
