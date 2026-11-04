# 文件管理交付说明

日期：2026-11-04。分支 `codex/filebrowser-v2`，工作区 `/opt/bili-remote-wt-filebrowser`。

## 实现范围

复用 `window.FileBrowser` 和原 `/api/files/*`；无新增后端文件操作、路由、数据表、目录布局或命名算法。四根 downloads/torrents/images/release_info 保持；配置选择器 `scope=mounts` 保持原调用方式。

共享组件改进：手机单列布局与可展开目录树；面包屑长名称换行；44px 操作触区；键盘 Enter 打开、空格选择；多选全选/半选状态；选择器同样支持搜索；切目录清空搜索，搜索更新清空旧选择；列表请求序号隔离慢响应；编辑和普通重命名锁定原根，避免背景导航改变写入对象；只读文本禁止编辑保存；任务轮询在销毁时停止；剪切仅移除逐项成功的文件，失败/跳过保留。复制、移动、删除仍使用后端 preview/apply，明确确认后执行，覆盖仍单独确认。原文件管理、工作台、嵌套选择器导出兼容。

`FileBrowserAdapter` 提供 `select(items, rootKey)`、`contextFor(items, rootKey)`、`handoff(destination, items, rootKey)`、`mediaInfo(item, rootKey)`、`mount(host, options)`。先通过真实 `/api/files/meta` 校验可访问性、符号链接和选择指纹，选中目录作为 directory 引用交接，不自行扫描或解析季集。调用统一 `/api/v2/resources/resolve`，相同引用和指纹复用当前页面 ResourceContext 对象及 revision；并发选择/上下文更新使旧响应失效。媒体弹窗消费同一 context.files[].probe，显示来源、未知值与错误，不猜参数。

## 整合会话必须接入的公共装配

本分支没有公共文件写权限，尚未在原主页面启用 V2 入口。`web.py` 没有 `/api/v2/resources/resolve`；统一 NamingService、共享模型与 V2 工作台也不在此工作区。下面的联动测试使用明确的 resolve 替身，仅证明模块消费契约，**不能证明完整应用联动已完成**。

整合会话在 `index.html` 中于原 file-browser.js 后加载 `/static/v2/filebrowser/adapter.js`，再由页面原文件管理挂载点调用：

```js
const adapter = new FileBrowserAdapter({
  getContext: () => pageResourceContext,
  setContext: context => { pageResourceContext = context; },
  openWorkbench: context => workbench.open(context),
  openNaming: context => naming.open(context)
});
const browser = adapter.mount(host, {title: '文件管理', onTool, onCollage});
await browser.init();
```

上面的变量是整合方注入的接口，不代表本分支已有这些全局对象。工作台亦消费同一个页面上下文。挂载前销毁旧实例，避免双实例操作同一 DOM。可包含 `templates/v2/filebrowser/browser.html` 作为宿主片段。样式由共享脚本加载模块 CSS，不改全局样式。

必须继续使用原 `api()`，保留登录、CSRF 和错误展示。整合方注册统一 resolve/probe 服务，并保证文件指纹变化时旧 probe 失效、人工字段不被自动覆盖。V2 智能命名回调使用 NamingService；原旧页面在未接入 adapter 时保留旧普通/批量改名能力以兼容，不能宣称已切换到 V2。

完整目标仍缺公共装配后真实 NamingService→文件管理→工作台端到端验收。未经整合，不标记整个目标完成；不越权修改公共文件或合并其他分支。无生产部署、服务重启、生产凭据读取或生产文件变更。

## 测试与契约

`tests/v2/filebrowser/test_interactions.py`：真实原 API 与临时文件的编辑冲突、保存、文件/目录改名、复制、移动取消与确认、删除确认；390/768/1440 浏览器布局、树、搜索分页、批量选择、图片在线预览；resolve 替身验证同对象/同 revision 交接和媒体显示。

`test_adapter.py`：源指纹变化、慢识别不覆盖新 context、部分移动保留失败剪贴板、嵌套选择器返回父弹窗。`test_safety.py`：遍历、绝对路径、NUL、根外 symlink 在所有读取接口均拒绝，保护根、目录移入自身、模拟 OS 拒绝写入的真实保存边界。

原 `tests/test_file_service.py`、`tests/test_filebrowser.py` 继续覆盖根/活动任务保护、CSRF、覆盖确认、源指纹重验、大小写冲突、部分失败和旧契约。测试只使用 tmp_path、隔离数据库与回环临时服务；没有访问真实上游或使用生产媒体。

最终验证结果（2026-11-04）：

| 命令 | 结果 | 覆盖范围 |
| --- | --- | --- |
| `python3 scripts/dev_test.py filebrowser` | **30 passed，0 failed，0 skipped，41.33 秒** | 15 个既有文件测试 + 15 个模块安全/交互/移动端测试 |
| `python3 scripts/dev_test.py all` | **225 passed，0 failed，0 skipped，148.39 秒** | 210 个既有测试 + 15 个模块测试，含原浏览器/下载/媒体工具回归 |
| `git diff --check` / 暂存差异检查 | 通过 | 空白和修改范围审查；9 个文件全部属于本模块白名单 |

两组测试均只有原 Starlette TestClient/httpx 弃用警告，未屏蔽错误或升级依赖。首次完整回归 223 项也通过；最终补充只读权限与跨根编辑测试后，以此处结果为准。最终专项还覆盖了加载回调对旧选择通知的兼容调整。

Python 3.12 完整集成验收由整合会话按顶层验收文档完成；本机实查复用既有 Python 3.11.2 依赖，不升级环境。

## 验收状态

| 需求 | 本分支证据与状态 |
| --- | --- |
| 四根、列表/树、面包屑、搜索分页、图片预览 | 原 API 测试 + 三屏宽真实浏览器测试通过 |
| 文本预览/保存、指纹冲突、权限拒绝 | 真后端临时文件验证通过；冲突保留编辑、跨根不误写、只读拒绝写入 |
| 复制粘贴、移动、删除、文件/目录改名、批量选择 | 真 API/浏览器测试通过；确认前无变更、移动取消不执行；逐项错误与剪贴板另测 |
| 路径安全、保护根/活动任务、覆盖/大小写冲突 | 原安全回归与新增负面测试通过，未修改底层安全实现 |
| 媒体参数、命名/工作台共用 context | 模块消费和交接测试通过，统一 resolve 以替身提供；**真实服务装配及主页面入口待整合，整体未完成** |
| 手机单列、目录树可访问、无横向溢出 | 390/768/1440 浏览器验证通过，页面无 JS 异常 |
| 当前分支提交 | 提交本模块白名单文件，提交标识以 Git 日志为准；不推送、不合并 |


## 文件清单

- `bili_remote/static/file-browser.js`
- `bili_remote/static/v2/filebrowser/adapter.js`
- `bili_remote/static/v2/filebrowser/filebrowser.css`
- `bili_remote/templates/v2/filebrowser/browser.html`
- `tests/v2/filebrowser/test_interactions.py`
- `tests/v2/filebrowser/test_adapter.py`
- `tests/v2/filebrowser/test_safety.py`
- `docs/v2/modules/filebrowser/handoff.md`
- `docs/v2/modules/filebrowser/contract-example.json`
