# Bili Remote 2.0 整合前审计

日期：2026-11-04（Asia/Shanghai）。只读审计完成后生成本文。整合授权以本次用户指令为准，覆盖早期“仅准备环境”和“不合并”的阶段限制；生产隔离及安全约束继续有效。

已完整阅读 AGENTS.md、五份顶层架构契约、五份 tasks 与五份 handoff。主工作区 /opt/bili-remote，分支 v2-integration，起点 db43176883bd30ffe4291435ae5281b032758d13。五模块均以该提交为共同祖先；报告提交全部存在且等于分支 HEAD。

## 未提交状态与报告差异

五模块 worktree 当前全部干净。Naming 有 c9eec04、bd80c6a 两次提交，Workbench 有 1b6c1b1；当前没有二者所述历史 Git 状态差异。无法从当前 Git 事实推断先前报告的具体时间/原因，不猜测、不重置。

主整合工作区初始存在四个未提交文件：static/app.js、static/assistant.js、static/media-tools.js、templates/index.html（均在 bili_remote 下）。这些修改增加共享通知、任务中心和布局引用，但引用的 static/v2/shared/shell.js、design-system.css 尚不存在。保留全部初始修改并以独立保全提交记录，后续补齐其依赖；不声称这是完整功能。

Download 实际提交还修改了公共 docs/v2/tasks/download.md，与 handoff 的白名单声明不符。已逐行审阅：将原任务约束替换为交付摘要，非业务实现。合并后由整合所有者恢复原任务规范，交付说明仍保留在模块 handoff。Workbench 明确为前端限定交付，没有原任务要求的 Python ReleaseTitleBuilder/PublishingService；由整合补齐。

## 依赖和未完成事项

- Naming：公共模型/OpenAPI、真实规则仓储、最小改名审计仓储、下载来源仓储、会话隔离服务、processing handler/幂等/启动恢复、主页面挂载、包资源构建。TMDB/PT-Gen 开发认证未实测。
- Subtitle：纯生成四模板已交付；共享规则仓储真实落库与重启验证、公共 API 注册未完成。
- Download：现有引擎适配和 UI 已交付；主页面、下载完成 hook、持久来源、跨重启幂等和 TaskCenter 待补。
- FileBrowser：共享组件安全改进和上下文适配已交付；主页面与真实 Naming/Workbench 接线待补，旧 meta 指纹与 V2 指纹需明确转换。
- Workbench：前端模块已交付；缺独立主标题 builder、无状态发布 composer、全部公共 V2 API 与统一任务投影、无草稿真实报告产物。
- 公共截图：Naming handoff 指出低帧率片尾采样超过最后一帧；必须用合成视频复现后修复，不能只重跑随机测试。
- 环境：现有依赖解释器为 3.11；宿主未发现 python3.12，Docker 可用，优先准备无生产挂载的隔离 3.12 验收环境。

## Git 原始核验及实际修改文件

### naming

工作区：/opt/bili-remote-wt-naming

```text
## codex/naming-v2
bd80c6a Bound naming scans before recursive fingerprinting
c9eec04 Implement isolated v2 naming and metadata services
A	bili_remote/static/v2/naming/naming.css
A	bili_remote/static/v2/naming/naming.js
A	bili_remote/templates/v2/naming/panel.html
A	bili_remote/v2/metadata/__init__.py
A	bili_remote/v2/metadata/providers.py
A	bili_remote/v2/metadata/resolver.py
A	bili_remote/v2/metadata/verify.py
A	bili_remote/v2/naming/__init__.py
A	bili_remote/v2/naming/episodes.py
A	bili_remote/v2/naming/errors.py
A	bili_remote/v2/naming/execution.py
A	bili_remote/v2/naming/filesystem.py
A	bili_remote/v2/naming/memory.py
A	bili_remote/v2/naming/probe.py
A	bili_remote/v2/naming/recognition.py
A	bili_remote/v2/naming/router.py
A	bili_remote/v2/naming/service.py
A	bili_remote/v2/naming/tasks.py
A	bili_remote/v2/naming/templates.py
A	docs/v2/modules/naming/contract-examples.json
A	docs/v2/modules/naming/handoff.md
A	docs/v2/modules/naming/provider-capabilities.md
A	docs/v2/modules/naming/provider-schemas.json
A	docs/v2/modules/naming/task-status.md
A	tests/v2/metadata/test_resolver.py
A	tests/v2/naming/test_browser.py
A	tests/v2/naming/test_episodes.py
A	tests/v2/naming/test_execution.py
A	tests/v2/naming/test_memory.py
A	tests/v2/naming/test_probe.py
A	tests/v2/naming/test_service_api.py
A	tests/v2/naming/test_templates.py
```

### subtitle

工作区：/opt/bili-remote-wt-subtitle

```text
## codex/subtitle-v2
7dbb1c3 feat: implement v2 subtitle service
A	bili_remote/v2/subtitle/__init__.py
A	bili_remote/v2/subtitle/memory.py
A	bili_remote/v2/subtitle/renderer.py
A	bili_remote/v2/subtitle/router.py
A	bili_remote/v2/subtitle/rules.py
A	docs/v2/modules/subtitle/handoff.md
A	tests/v2/subtitle/test_anime.py
A	tests/v2/subtitle/test_custom_template.py
A	tests/v2/subtitle/test_memory.py
A	tests/v2/subtitle/test_movie.py
A	tests/v2/subtitle/test_router.py
A	tests/v2/subtitle/test_tv_episode.py
A	tests/v2/subtitle/test_tv_season.py
```

### download

工作区：/opt/bili-remote-wt-download

```text
## codex/download-v2
5c09fff feat(download): add v2 download workbench adapter
A	bili_remote/static/v2/download/workbench.css
A	bili_remote/static/v2/download/workbench.js
A	bili_remote/templates/v2/download/workbench.html
A	bili_remote/v2/download/__init__.py
A	bili_remote/v2/download/completion.py
A	docs/v2/modules/download/handoff.md
M	docs/v2/tasks/download.md
A	tests/v2/download/test_browser.py
A	tests/v2/download/test_completion.py
```

### filebrowser

工作区：/opt/bili-remote-wt-filebrowser

```text
## codex/filebrowser-v2
e206b35 feat(filebrowser): improve shared interactions and add v2 context handoff
M	bili_remote/static/file-browser.js
A	bili_remote/static/v2/filebrowser/adapter.js
A	bili_remote/static/v2/filebrowser/filebrowser.css
A	bili_remote/templates/v2/filebrowser/browser.html
A	docs/v2/modules/filebrowser/contract-example.json
A	docs/v2/modules/filebrowser/handoff.md
A	tests/v2/filebrowser/test_adapter.py
A	tests/v2/filebrowser/test_interactions.py
A	tests/v2/filebrowser/test_safety.py
```

### workbench

工作区：/opt/bili-remote-wt-workbench

```text
## codex/workbench-v2
1b6c1b1 feat(workbench): build responsive stateless publishing frontend
A	bili_remote/static/v2/workbench/api.js
A	bili_remote/static/v2/workbench/index.html
A	bili_remote/static/v2/workbench/preview.js
A	bili_remote/static/v2/workbench/standalone.js
A	bili_remote/static/v2/workbench/workbench.css
A	bili_remote/static/v2/workbench/workbench.js
A	bili_remote/templates/v2/workbench/workbench.html
A	docs/v2/modules/workbench/contracts.md
A	docs/v2/modules/workbench/handoff.md
A	docs/v2/modules/workbench/validation.md
A	tests/v2/workbench/build_frontend.py
A	tests/v2/workbench/test_browser.py
A	tests/v2/workbench/test_frontend.py
A	tests/v2/workbench/transport.test.cjs
```

## 整合决策

代码和交付状态已核实。按用户指定 Naming → Subtitle → Download → FileBrowser → Workbench 合并，每次前后查状态并跑模块测试。未解决集成依赖不冒充已完成；测试失败先定位记录，公共修复后重新验收。其他 worktree 保持原状，不推送、不部署。
