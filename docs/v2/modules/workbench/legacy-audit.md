# 工作台旧版控制器清理审计（只读）

本文档记录发种工作台相关旧版操作框架的当前引用关系、V2 对应能力和删除建议。它是只读审计结论，不修改任何公共文件；实际删除需由整合会话在确认“旧入口已彻底不再可达”后执行。

## 1. 结论摘要

- 当前 V2 页面已由 `templates/v2/workbench/workbench.html` + `static/v2/workbench/workbench.js` 接管命名、缩略图、MediaInfo/BDInfo、本地制种、PT-Gen 简介和发布预览。
- 但 `templates/index.html` 仍保留旧版“工具记录 / 上传记录 / 媒体报告 / 本地种子”入口，`app.js` 在 V2 模式下仍会把 `uploads`、`torrents`、`release-info`、`screenshots` 路由到 `window.FileTools.open(...)`，从而继续可达旧版 `ui.js` / `assistant.js` / `media-tools.js` / `modules.js`。
- 因此在旧入口被工作台工具启动器完全取代之前，不能盲删旧脚本；否则会把仍可达的页面打成未捕获异常。

## 2. 旧版控制器与 V2 对应能力

| 旧入口（kind） | 旧文件/函数 | 现状 | V2 对应位置 |
| --- | --- | --- | --- |
| `screenshots`（缩略图） | `workspace.js`/`media-tools.js`/`collage.js` | `index.html` 不再有 `data-file-tool="screenshots"` 主入口，但 `app.js` 仍把 `screenshots` 映射为旧工具 | 工作台“缩略图”面板 `data-action="screenshots"`，原地展示、下载、图床上传 |
| `uploads`（上传记录/图床） | `index.html` 的 `uploads-view`、`media-tools.js`/`modules.js` | 仍有 `data-file-tool="uploads"` 按钮，且 `index.html` 内完整 `uploads-view` 未删 | 工作台 `data-action="upload"` + `renderUploadLinks()` 生成图床链接并复制链接/BBCode |
| `release-info`（媒体报告） | `index.html` 的 `release-info-view`、`media-tools.js` | 仍有 `data-file-tool="release-info"` 按钮 | 工作台“媒体报告”面板 `data-action="report"`，原地复制/下载 |
| `torrents`（本地种子） | `index.html` 的 `torrents-view`、`media-tools.js` | 仍有 `data-file-tool="torrents"` 按钮 | 工作台“种子制作”面板 `data-action="torrent"`，直接下载 `.torrent` |
| 旧批量重命名 | `assistant.js`（`SeedAssistant.openTool("rename")` 等） | `index.html` 有 `assistant-rename` 按钮 | 工作台命名主界面 `data-action="naming/apply/undo"` |
| 发布信息/旧发布编辑器 | `assistant.js`、`modules.js`（`openPublishSession` 等） | `index.html` 有 `assistant-compose`、`assistant-legacy-open` | 工作台标题/副标题/简介及发布预览 |

## 3. 仍然可达的旧入口（V2 模式）

以下入口当前仍会触发旧控制器，删除相关文件前必须先移除或改向：

1. `templates/index.html` 中 `data-file-tool="release-info"`、`data-file-tool="uploads"`、`data-file-tool="torrents"`（文件历史菜单与工具记录菜单各一组）。
2. `app.js` `switchAppView()` 中 `legacyTool` 名单 `["uploads", "torrents", "release-info", "screenshots"]`，将旧工具映射回 `files` 视图并 `window.FileTools?.open(legacyTool)`。
3. `ui.js` 中 `window.FileTools = {open:openTool,...}` 以及 `[data-file-tool]` 点击委托。
4. `index.html` 中 `assistant-rename`、`assistant-compose`、`assistant-legacy-open`、`data-assistant-tool` 等助手入口，仍由 `assistant.js`/`modules.js` 处理。

## 4. 仍被引用、暂不能盲删的公共文件

以下均为整合会话/公共文件（见 `docs/v2/file-ownership.md`），本工作树**不修改、不删除**：

- `bili_remote/static/workspace.js`
- `bili_remote/static/assistant.js`
- `bili_remote/static/media-tools.js`
- `bili_remote/static/collage.js`
- `bili_remote/static/modules.js`
- `bili_remote/static/file-browser.js`
- `bili_remote/static/ui.js`

其中 `ui.js` 是 `window.FileTools` 的实现入口，删除前必须先让 `app.js` 的旧工具路由不再调用它。

## 5. 建议的删除/保留顺序（提交整合会话）

建议按以下顺序在整合会话处理，每步先验证页面无 JS 异常：

1. 先让工作台工具启动器完全接管 `screenshots`、`uploads`、`release-info`、`torrents` 四个入口，并将 `index.html`/`app.js` 中这些入口改为打开 V2 工作台对应面板。
2. 移除 `index.html` 中不再可达的 `screenshots-view`、`uploads-view`、`release-info-view`、`torrents-view` 以及助手侧旧按钮。
3. 从 `app.js` 移除 `legacyTool` 名单及 `window.FileTools?.open` 调用，从 `ui.js` 移除 `FileTools` 与 `[data-file-tool]` 委托（如无其他调用方）。
4. 最后才删除 `workspace.js`、`assistant.js`、`media-tools.js`、`collage.js`、`modules.js` 中确认无引用的旧控制器，保留下载引擎、媒体探测、文件安全、本地制种和数据库兼容所需的底层实现。

## 6. 已确认删除（工作台分支已完成）

- qBittorrent 连接/推送：提交 `45dbc03` 删除 `bili_remote/qbittorrent.py`，净删 602 行，涉及 `web.py`、`services.py`、`settings_registry.py`、`publishing.py`、旧 JS/CSS/HTML 与测试。该提交触碰公共文件，需整合会话复核，不属于工作台模块自有改动。

## 7. 本文件状态

本文件为只读审计结论，供整合会话参考；不在本分支执行任何公共文件删除。
