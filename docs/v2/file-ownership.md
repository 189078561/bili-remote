# 并行会话文件归属

本文件是写入白名单；未列出的既有文件默认属于最终整合会话。只读依赖允许，越界修改须经明确授权。一个文件只有一个写入会话，不能靠拆分函数同时编辑公共文件。

## 工作区与独占范围

| 会话/分支 | 工作区 | 可修改/新增的业务文件 | 独占测试与模块文档 |
| --- | --- | --- | --- |
| 整合 / v2-integration | /opt/bili-remote | 所有下述公共入口和未分配文件；bili_remote/v2/contracts/、bili_remote/v2/task_center/、bili_remote/v2/__init__.py、static/v2/shared/ | tests/v2/conftest.py、tests/v2/contracts/、tests/v2/integration/、docs/v2/ 顶层契约和 tasks/ |
| naming / codex/naming-v2 | /opt/bili-remote-wt-naming | bili_remote/v2/naming/、bili_remote/v2/metadata/、static/v2/naming/、templates/v2/naming/ | tests/v2/naming/、tests/v2/metadata/、docs/v2/modules/naming/ |
| subtitle / codex/subtitle-v2 | /opt/bili-remote-wt-subtitle | bili_remote/v2/subtitle/（不得含主标题/简介组合）、static/v2/subtitle/、templates/v2/subtitle/ | tests/v2/subtitle/、docs/v2/modules/subtitle/ |
| workbench / codex/workbench-v2 | /opt/bili-remote-wt-workbench | bili_remote/v2/workbench/（独立 release_title.py、publishing.py）、static/v2/workbench/、templates/v2/workbench/ | tests/v2/workbench/、docs/v2/modules/workbench/ |
| download / codex/download-v2 | /opt/bili-remote-wt-download | bili_remote/v2/download/（仅联动/展示适配器）、static/v2/download/、templates/v2/download/ | tests/v2/download/、docs/v2/modules/download/ |
| filebrowser / codex/filebrowser-v2 | /opt/bili-remote-wt-filebrowser | bili_remote/v2/filebrowser/（复用现有后端）、static/file-browser.js、static/v2/filebrowser/、templates/v2/filebrowser/ | tests/v2/filebrowser/、docs/v2/modules/filebrowser/ |

表中 static/、templates/ 均指 `bili_remote/static/`、`bili_remote/templates/`。这些 V2 目录是后续实现位置，本阶段不放入业务实现。各模块可在其独占包中加 `__init__.py`，但 `bili_remote/v2/__init__.py` 和 `tests/v2/conftest.py` 是公共文件。测试直接使用已有 tests/conftest.py 的临时 Settings，或模块内私有夹具，不改全局夹具。

## 公共文件（默认只由最终整合会话修改）

- `bili_remote/web.py`：全局 API 路由注册、认证和 CSRF 接入；各模块可交独立 router.py，不能自行注册到主应用。
- `bili_remote/database.py`：SCHEMA、MIGRATIONS、Database.initialize、备份及所有数据库迁移入口。
- `bili_remote/templates/index.html`、login.html：应用骨架和脚本/片段加载。
- `bili_remote/static/app.js`：全局状态、入口、导航和挂载；modules.js、workspace.js、assistant.js、media-tools.js、settings*.js、ui.js 等既有跨模块脚本亦为公共文件。
- 现有全部全局 CSS：styles.css、ui.css、workspace.css、assistant.css、media-tools.css；模块仅可新增自己 static/v2/<模块>/ 内带命名空间的样式，不修改全局选择器。
- `bili_remote/services.py`、main.py、config.py、security.py、validation.py、runtime_settings.py、settings_registry.py：服务装配、生命周期、权限、配置和设置白名单。
- `bili_remote/v2/contracts/`：共享模型和 provider/storage Protocol；整合会话按已审阅的文档实现，模块不能私自改变字段。
- `bili_remote/v2/task_center/`、processing.py、tool_jobs.py：任务中心聚合和执行器注册，禁止各模块新建独立任务表/worker。
- 现有 naming.py、filebrowser.py、file_service.py、media_probe.py、metadata.py、publishing.py、release_info.py、screenshots.py、collage.py、image_hosts.py、torrents.py、qbittorrent.py：既有复用边界与兼容桥接。模块通过自己的适配层复用，需改变底层时交整合会话处理。
- 现有 downloader.py、bili.py、workspace.py：下载引擎/登录和旧资源兼容路径，未经专项授权不重写。
- `pyproject.toml`、requirements*.txt、Dockerfile、docker-compose.yml、.dockerignore、.gitignore、AGENTS.md、scripts/、deploy/、README.md、既有 tests/*.py 及顶层架构文档。

## 交付与整合协议

每个模块在 `docs/v2/modules/<模块>/handoff.md` 列出导出接口、所需公共装配变更、迁移建议、mock 与实测边界、测试结果。禁止在自己的分支提交公共文件的“临时修补”。如依赖尚未落地，按 api-contract.md 定义的 Protocol 使用模块私有 fake，不复制第二份正式模型或算法。

整合顺序：公共模型/存储协议 → naming + metadata → subtitle → workbench → download/filebrowser 联动 → 路由与主页面注册 → 数据迁移与完整回归。顺序是依赖整合顺序，不阻止各模块先完成可独立测试的实现。合并由整合会话处理，保留各分支提交，不擅自推送。

五个 worktree 必须从同一 v2-integration 环境准备提交创建；不包含生产 .env、SQLite 或媒体文件。开发凭据各自随机生成于被忽略的 .env.dev，运行目录各自为 .dev/。预留回环测试服务端口依次 18121、18122、18123、18124、18125（启动前再查占用，本次不启动）。必要测试命令见对应任务文档。
