# Bili Remote 2.0 开发约束

1. 开始前阅读 `docs/v2/architecture.md`、`api-contract.md`、`data-model.md`、`file-ownership.md`、`acceptance.md` 及本分支任务。产品方案未定稿时，只做不依赖方案的准备，不自行补成既定需求。
2. 当前阶段只初始化环境和文档；禁止开展五模块业务开发、部署、重启服务、删除旧功能、清理或迁移生产数据库。
3. 后续开发必须在对应 `codex/*-v2` 分支和指定 worktree 内完成；整合会话独占 `/opt/bili-remote` 的 `v2-integration`。不要切换、重置、清理其他会话的分支或工作区。
4. 严格遵守 `docs/v2/file-ownership.md` 的白名单；每个文件只允许一个所有者。跨模块变更写入自己的交付说明，交给整合会话处理。
5. 公共文件 `bili_remote/web.py`、`database.py`、`services.py`、`main.py`、`config.py`、`templates/index.html`、`static/app.js`、全局 CSS、全局 API 注册及数据库迁移入口默认只由整合会话修改；其他会话须获得明确授权。
6. 公共模型、依赖清单、设置注册、共用测试夹具及架构契约亦由整合会话维护。模块不得通过修改其他公共文件绕过此限制。
7. 不读取、复制或使用生产 `.env`、Cookie、API Key、生产数据库或私有备份做开发；只用 `.env.dev.example`、`scripts/dev_init.py` 生成的本地配置和合成测试数据。
8. 禁止将真实密钥、Cookie、运行数据库、数据库备份、下载资源、日志、缓存、虚拟环境和 node_modules 提交到 Git。提交前检查 `git status` 和暂存差异。
9. 测试运行 `python3 scripts/dev_test.py <模块>`，数据仅用临时目录或本 worktree 的 `.dev/`；默认不请求真实 B 站、TMDB、PT-Gen、图床或 qBittorrent。不得使用生产 compose 文件启动开发服务。
10. 开发服务仅按后续授权启动，绑定 `127.0.0.1`，使用各自端口与存储目录；不得占用生产 8111 端口。不得升级或改动现有 Python、Node、Nginx、Docker 环境。
11. 保留旧 API、数据语义和功能。命名预览无写入；文件变更须校验路径、冲突和源指纹，禁止隐式覆盖、自动移动或自动发布。
12. 不把未知资料或媒体参数猜成事实，不自动覆盖用户编辑。字幕文件处理与发种副标题不是同一功能，按定稿契约实现。
13. 任务交付应包含模块测试、契约样例、修改文件清单、运行结果及限制；不擅自合并、推送、创建远程仓库或启动生产部署。
