# 发种工作台前端交付

状态：前端模块实现；**模块 03 一级资源浏览与选择已完成定向验证，但尚未整合、部署或视为上线验收通过**。
当前分支 `codex/workbench-resource-browser-v2`，工作区 `/opt/bili-remote-wt-workbench-resource-browser-v2`，基于 `11c3000`。本次只修改工作台模块及 doc/test 范围，没有修改后端、公共入口或生产环境。

## 入口与装配

已提供 `/static/v2/workbench/index.html` 独立入口，复用现有静态文件挂载及 `/api/auth/session` 登录会话；没有增加服务器路由。未登录仅显示登录提示。入口由 canonical 模板构建，不包含模拟响应；请求均发往同源真实接口。

仅在运行本分支源码的隔离测试应用中验证了该入口；生产服务没有加载本分支，也未部署或重启。不能用生产 URL 验收当前开发代码。

整合会话可改主页面导航并嵌入模块，不需嵌入独立入口：

```html
<link rel="stylesheet" href="/static/v2/workbench/workbench.css">
{% include "v2/workbench/workbench.html" %}
<script src="/static/v2/workbench/api.js"></script>
<script src="/static/v2/workbench/preview.js"></script>
<script src="/static/v2/workbench/workbench.js"></script>
```

在现有 `app.js`、共享 `file-browser.js` 及 CSRF meta 已加载后：

```js
const controller = await BiliWorkbench.mount(document.querySelector('[data-workbench]'));
// 文件管理/下载入口交接；复用同一 ResourceContext，未携带 context 时重新识别。
await controller.select(resources, {context, origins});
// 真正卸载页面时清理监听器、轮询与内存中的文案。
controller.destroy();
```

`resources` 可为 FileBrowser 项，或带 `kind=file/directory` 的 ResourceRef。不要把发布文案传入 URL、持久化状态或任务输入。`mount` 对同一节点重复调用会销毁旧实例。普通切换工作视图不应 destroy，否则当前页面编辑自然清空。

`standalone.js` 仅供独立入口使用，提供 FileBrowser 所需宿主工具和单层预览对话框；主应用已有这些工具，嵌入时不要再加载该文件。

## 已实现

- 一级资源选择器复用共享 FileBrowser，四类根、目录树、目录进入/返回上级、当前目录搜索、分页和视频/目录多选；未复制组件源码或另建文件浏览后端。
- 未选择资源时，工作台主区域直接显示文件浏览器；选择只暂存在 `pendingSelection`，点击“使用所选资源”后才切换当前资源。取消、返回当前资源或按 Esc 不改变已确认资源。已有资源时顶部按钮变为“更换资源”，重新打开同一一级浏览器。
- 右侧发布内容、缩略图、媒体报告、种子制作四视图；识别区跨视图保留；390/768 单列，1440 双列。
- 同一个 ResourceContext/revision 调用识别、媒体探测、命名预览、主标题服务和 SubtitleService。首次识别返回未探测文件时提交一次探测任务；不自行解析标题或季集。
- 主/副标题和简介分别预览、人工编辑、独立复制；同一资源重新识别保留人工编辑。覆盖人工主/副标题需要连续明确点击该字段的生成按钮。
- 修改发布文案即时撤销旧完整预览，220ms 合并请求服务端组合，支持三格式源码与格式效果预览。HTML 使用无权限 sandbox iframe；BBCode/Markdown 使用安全 DOM 展示常用格式，不执行 HTML。
- 命名预览展示源/目标、冲突与有效期；规则/字段/资源变化使旧预览失效。执行只提交 token、expected_revision、confirm 和同一幂等键；不传目标路径、不隐式覆盖。完成后通过真实 meta 接口重读路径与指纹，再继续识别；同一资源的人工发布编辑保留。
- 截图单图及拼图，显式截取范围，真实图片下载与图床上传分开。制种真实产物下载，添加 qBittorrent 需要独立用户操作。
- 只读 MediaInfo/BDInfo 报告查看与复制；不调用旧报告保存和草稿接口。
- TaskRef 按 kind/id 区分身份，未知进度不虚构百分比；取消/重试委托现有机制，重命名不通用重试；完成通知去重、错误反馈、日志、刷新后任务与产物读取。
- V2 聚合未注册时，明确标示只连接旧 processing 队列；仅在 404 时降级，权限和网络错误不会变成“无任务”。
- 无 localStorage/sessionStorage/IndexedDB/发布文案持久化，无项目/草稿/历史保存入口。

## 必须由后端/整合会话补齐

| 依赖 | 当前本分支事实 | 用户可见影响 |
| --- | --- | --- |
| `/api/v2/resources/resolve`、probe、metadata/search | 全部尚未注册 | 真实选择显示“尚未接入”，不产生虚构资料或参数 |
| NamingService preview/apply 与规则记忆 | 尚未实现 | 安全改名和规则加载只完成前端契约，不能执行真实 V2 改名 |
| `/api/v2/subtitles/render` | 尚未实现 | 不能生成真实副标题；可人工编辑和独立复制 |
| `/api/v2/release-title/render` | 尚未实现 | 不能生成真实主标题；本次前端限定明确禁止补写 Python builder |
| `/api/v2/publishing/preview` | 尚未实现 | 不能生成真实完整三格式发布内容；不在 JS 拼接业务文案 |
| `/api/v2/tasks` 聚合与 TaskRef 产物 | 尚未实现 | 暂只能恢复现有 processing 工具任务，下载统一视图待整合 |
| 无草稿报告任务/ArtifactRef | 旧 report.create 会写 release_documents | 目前只用 analyze 查看、复制；报告无法直接附加到完整发布组合，刷新不恢复只读分析 |
| qBittorrent 原生制种 | 现有 API 只有连接/列表/添加 torrent | 已支持本地制种后手动添加；不能宣称支持 qBittorrent 原生制种，不展示假按钮 |
| 主应用五导航挂载 | index.html/app.js 是公共文件 | 独立入口可验收；主应用仍需整合会话替换旧工作台入口 |

不得通过调用旧 `/api/publish/sessions*`、`/api/releases*`、`/api/media/metadata` 保存、`/api/release-info/jobs` 或创建临时发布项目绕过上述依赖。

报告当前使用真实只读 `/api/release-info/analyze`。该请求在服务端线程运行，页面可继续编辑，完成后通知，但不是可刷新恢复的后台 TaskRef。报告任务接入及产物存储白名单需要整合会话审计；没有自行发明新路径。

V2 TaskRef 仅有 artifact_refs，没有旧任务 result/input。前端接受真实 ArtifactRef 供组合；本分支生成工具仍使用原任务响应和读取接口。最终整合应验证新任务适配与真实引用的一致性，尤其报告、探测完成后上下文刷新、重命名后的新路径/指纹。

## 文件清单

- `bili_remote/templates/v2/workbench/workbench.html`：唯一编辑源模板片段。
- `bili_remote/static/v2/workbench/workbench.css`：命名空间与响应式样式。
- `bili_remote/static/v2/workbench/api.js`：契约传输、CSRF、错误和旧工具任务兼容。
- `bili_remote/static/v2/workbench/workbench.js`：页面状态与服务编排。
- `bili_remote/static/v2/workbench/preview.js`：安全展示格式，不生成发布业务文本。
- `bili_remote/static/v2/workbench/standalone.js`：独立入口宿主与登录校验。
- `bili_remote/static/v2/workbench/index.html`：构建生成的真实同源入口。
- `tests/v2/workbench/`：构建检查、传输契约测试和浏览器交互/真实工具测试。
- `docs/v2/modules/workbench/`：交付、契约样例及验证记录。

模块 03 删除旧的全屏资源抽屉模板和其专属布局样式；保留共享 FileBrowser 的目录、搜索、选择和安全 API。移动端选择器使用普通文档流，不通过固定遮罩锁住 body 滚动。文件管理/下载入口仍通过现有 `select(resources, {context, origins})` 交接，直接进入 studio，不重复选择。

未改公共入口、后端、全局 CSS、公共模型、依赖、其他模块、顶层任务文档或生产配置；没有数据库迁移、分支切换、合并、推送或部署。

## 验证

执行命令、最终结果、实测与替身范围见 `validation.md`。更新模板后执行 `python3 tests/v2/workbench/build_frontend.py`；只读校验为同命令加 `--check`。构建用现有 Node 做语法校验及生成静态入口，不安装包、不改 Python/Node 环境。

## 仍需共享 OpenAPI 明确的细节

当前顶层契约只把 `manual_overrides` 和 `expected_fingerprints` 写为 object；前端使用 selected_title/episode 的人工覆盖，以及 `root_key/relative_path` 为键的指纹映射。最终命名 router/shared schema 应核对这些细节，不能把当前缺少真实 schema 的请求拦截测试写成后端契约已验证。

主标题规则在 ResourceContext.effective_rules 中没有正式注册字段，目前空规则请求交服务校验，用户可在调整区填写 template。整合会话应补齐主标题默认站点规则装配及完整 ReleaseTitleRule 校验；前端不会据 basename 伪造主标题作为降级。

## minfo 式交互优化（后续分支）

本节更新上述历史交付状态：`codex/workbench-minfo-motion-v2` 基于模块 03 的 `d59cd96`，当前 V2 识别、报告及任务接口已供测试应用使用。本次不新增后端路由，也不变更现有命名、媒体工具或任务执行契约。

- 参考 minfo 源码的 PathBrowser 单击/双击事件分离、App.vue 配置折叠、ActionButtons 忙态、TaskProgressBar 进度过渡、OutputPanel / ImageLinksPanel 结果入场及 NoticeToast。使用现有主题与原生 DOM；折叠采用自适应网格高度，不照搬固定高度或第三方样式。
- 仅修改工作台模板、对应生成入口、工作台 JS/CSS 及其浏览器测试。共享 FileBrowser 仍负责列表数据与四根安全校验；工作台只包装该实例的加载状态。共享任务刷新仍是唯一轮询来源，百分比变化只更新进度，不替换列表。
- 工作台按现有 TaskRef 关联进度。例如 `{"kind":"processing","id":81,"task_type":"torrent_build","status":"running","progress":42}` 显示 42%；`progress:null` 不渲染百分比。返回实际可读取产物后才在当前资源下显示生成成功。图片、报告和种子消费既有产物引用；日志用原接口读取最近 100 行。
- 验证：前端生成一致性、Node 语法和 Git 差异检查通过；`python3 scripts/dev_test.py workbench` 共 30 项通过，未运行仓库全量测试。真实工具链只处理临时合成视频，图床使用替身；移动端为 Chromium 390px 和触摸友好入口模拟，并非实体手机验收。
- 未合并或推送；依照当前工作区限制未接触生产数据库、Docker 或凭据，未部署。
