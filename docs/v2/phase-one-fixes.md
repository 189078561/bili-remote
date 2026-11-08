# V2 第一阶段基础整改交付

日期：2026-11-05。工作区 `/opt/bili-remote`；分支 `v2-integration`；基准 `679b7c7`。开始时工作树干净。本次没有切换、重置或清理工作区，没有提交、推送、部署或重启生产服务。

## 行为与修复

- 工作台 FileBrowser 勾选只更新候选选择。点击“使用所选资源”才提交；提交前验证引用，非法选择不清空正式资源、编辑、预览或产物。
- 切换时如有人工标题、副标题或简介，浏览器确认明确说明将清空的内容；取消保留全部状态。相同资源（含多选顺序变化）重复提交不重置，也不重复识别。
- 文件列表与 V2 交接使用不同格式的指纹。同路径指纹不一致时复用已有资源引用接口校验；整季目录每次正式使用都复核完整指纹，避免子文件变化而目录时间未变的漏检。
- 正式资源切换递增资源版本；字段编辑版本、请求序号、上下文修订、命名预览有效期与任务所属资源继续参与校验。旧识别、主标题、组合预览、报告提交、下载识别及高级命名响应不能覆盖新资源。
- 原地改名仅作废旧请求版本，保持任务所属资源，避免在途截图、上传、报告失联；重新读取真实路径和指纹，再识别。高级命名仍能执行、预览撤销、确认撤销，同资源刷新保留未提交的逐文件人工参数。
- 同一 FileBrowser 增加 `full`、`compact`、`picker` 展示模式；原 `mode` 继续决定操作/选择能力。工作台使用 compact，保留一套组件搜索、目录树开关、面包屑、列表、分页、排序与选择。文件操作的预览、冲突、指纹和确认流程未改动。
- 文件管理交接先准备上下文，目的地确认后才提交和导航；媒体信息预览不改正式上下文。异步交接同时检查上下文对象及资源版本，包括 context 尚为空的识别期间。
- `report` 正确打开媒体报告视图；`upload` 复用 `BiliModules.runFileTool('upload', ...)` 的真实上传与任务结果流程；下载完成按钮及说明统一为“进入工作台”。高级工具入口保留。

## 前端调用约定样例

```javascript
new FileBrowser(host, {
  mode: 'select-file', displayMode: 'compact',
  selectionType: 'both', allowMultiple: true,
  onSelectionChange: items => workbench.stageSelection(items),
  onConfirm: items => workbench.select(items),
  confirmLabel: '使用所选资源'
});
```

`select()` 是显式提交入口：非法、取消或被新选择取代返回 `false`；当前资源接受/复用返回 `true`。识别失败保留已接受的资源并显示错误，可通过“重新识别”重试。候选勾选不调用该入口。

`FileBrowserAdapter.contextFor(items, root, {commit:false})` 只准备交接数据；`handoff()` 接受目的地返回 `false` 取消。整合页面由工作台独占正式状态写入，并传入 `getContextVersion: () => workbench.epoch` 防止迟到交接。

工作台 `v2:context` 事件携带 `{context, epoch, reason}`，`reason` 为 `selection`、`rename` 或 `update`。高级命名据此作废旧请求并区分正式换资源与原地改名；任务提交前快照资源及 epoch。以上均为页面内调用约定，没有新增或修改后端接口。

## 实际修改文件

| 文件 | 用途 |
| --- | --- |
| `bili_remote/static/v2/workbench/workbench.js` | 候选/正式选择分离，编辑保护、指纹比较、请求版本与任务关联 |
| `bili_remote/templates/v2/workbench/workbench.html` | 移除重复搜索，添加明确使用提示 |
| `bili_remote/static/v2/workbench/index.html` | 由原构建脚本同步独立入口 |
| `bili_remote/static/file-browser.js` | 展示模式标记、异步确认和工具错误处理 |
| `bili_remote/static/v2/filebrowser/filebrowser.css` | 仅组件范围的紧凑布局及树开关 |
| `bili_remote/static/v2/filebrowser/adapter.js` | 延迟提交、取消、只读预览、交接版本检查 |
| `bili_remote/static/v2/shared/integration.js` | 报告/上传/下载入口与高级命名同步 |
| `bili_remote/static/v2/naming/naming.js` | 高级请求失效、任务快照、撤销批次和人工参数保护 |
| `bili_remote/static/v2/download/workbench.js` | 完成按钮文案及入口错误提示 |
| `bili_remote/templates/v2/download/workbench.html` | 对齐完成动作的说明文案 |
| `tests/v2/workbench/test_browser.py` | 勾选、切换、人工编辑、指纹、旧响应及改名后任务关联回归 |
| `tests/v2/filebrowser/test_adapter.py` | 媒体预览隔离与取消交接回归 |
| `tests/v2/filebrowser/test_display_modes.py` | 三模式浏览、搜索范围、排序分页、单多选与目录选择 |
| `tests/v2/integration/test_stage_one.py` | 真实入口、上传、下载交接、命名/撤销和并发回归 |
| `docs/v2/phase-one-fixes.md` | 本交付说明 |

## 验证记录

| 执行命令 | 结果 |
| --- | --- |
| `python3 scripts/dev_test.py workbench` | 26 passed，47.66 秒；之后补充的整季指纹用例由最终全量运行覆盖 |
| `python3 scripts/dev_test.py filebrowser` | 35 passed，48.75 秒 |
| `python3 scripts/dev_test.py integration` | 24 passed，40.11 秒 |
| `python3 scripts/dev_test.py all` | **393 passed，0 failed，0 skipped，256.53 秒** |
| 原前端构建脚本 `--check`、修改 JS 语法、`git diff --check` | 通过 |

最终全量运行包含所有本阶段新增用例及旧版兼容测试。模块首轮曾有测试等待/定位断言错误（请求事件与路由回调时序、顶层变量定位、链接 href 断言），已修正并由最终运行覆盖。现有 Starlette/httpx 测试客户端产生 1 条弃用警告，未为消除警告更改依赖环境。

结果文件位于 `.dev/test-results-{workbench,filebrowser,integration,all}.xml`，均为忽略的本地测试产物。原业务回归包括真实临时文件复制/移动/删除/编辑及冲突保护、真实改名/撤销、截图/拼图、报告、制种、上传任务产物、任务日志/重试和旧兼容入口；外部上传与 qBittorrent 通信仅用测试 transport。

测试使用现有 `scripts/dev_test.py`，临时配置、临时数据库及合成文件；服务仅在测试夹具的随机回环端口运行。真实临时文件操作、媒体工具与任务管线参与回归；第三方 HTTP 使用测试替身，延迟响应测试使用合成契约或暂缓真实本地响应。未读取生产配置、凭据、数据库或下载资源。

静态入口一致性、修改的 JavaScript 语法和 `git diff --check` 均通过。另已检查 390/1440 宽工作台截图，确认搜索、使用按钮、树开关和业务视图可访问；390/768/1440 的响应式及四视图自动化检查也包含在现有测试中。

## 边界与下一阶段

本阶段没有更改后端 API、数据库结构、识别/TMDB 算法、旧版兼容入口或全局 CSS；没有重做首页、下载页、设置页，没有新增框架或项目模型。下载页仅修改完成入口文字与错误处理。

本地回归不能代替真实 B站、TMDB、PT-Gen、图床和 qBittorrent 联网验收；这些服务未使用生产凭据测试。测试复用现有 Python 3.11 依赖环境，项目声明的 Python >=3.12 环境复验仍需独立环境完成。确认切换后清除旧资源编辑、刷新不恢复发布草稿，均保留 V2 当前契约。

下一阶段建议先确认工作台及文件管理的区域尺寸、信息层级和窄屏交互，再在现有组件上进行布局调整，并将本阶段回归作为门禁。本次停在第一阶段，不自动开始视觉重构。
