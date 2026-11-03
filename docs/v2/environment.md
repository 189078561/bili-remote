# 初始化环境与操作记录

日期：2026-11-03（Asia/Shanghai）。本次仅完成环境、文档和工作区准备，无 V2 业务实现、服务启动/重启或生产部署。

## 服务器和备份

实际系统 Debian GNU/Linux 12，用户 root；Git 已安装 2.39.5，无需 apt 操作，未升级系统。检查时根盘约 99 GiB、剩余约 63 GiB，项目约 496 MiB（含 312 MiB venv、122 MiB 图片和旧备份）。备份预算远小于可用空间，并保留至少 2 GiB 余量。

项目根 `/opt/bili-remote` 原无 Git。生产 Docker 容器 `bili-remote-bili-remote-1` 使用 `bili-remote:1.0.0`，宿主 8111→容器 8080；仅只读检查健康，未重启或修改其配置。实际下载根为 `/srv/bilibili-downloads`，未复制或搬动其内容。

备份目录：`/opt/bili-remote-backups/20261103-223644-CST`（目录 700，文件 600，仅 root）。内容：source.tar.gz、private-config/、databases/data/bili-remote.sqlite3、private-runtime/screenshot-sessions/、inventory.json、SHA256SUMS、测试与核验日志。数据库使用源只读连接和 SQLite online backup，不用简单 cp 活跃数据库，完整性检查通过。

源码包含 Python/前端/测试/文档/必要静态资源，配置包含原 .env、.env.example、Compose/Docker/Nginx 示例（后几项在源码包）。不重复打包媒体、下载、缓存、node_modules、venv、历史备份和 dist；实际产物原位保留，库存清单不等于备份这些大文件。备份包含真实凭据和认证数据库，严禁提交、上传或用作开发数据。恢复生产数据不属于当前授权。

## Git 状态与文件保护

main 初始基线 `2d76f1f`，包含既有稳定代码与安全 .gitignore；v2-integration 承载环境准备/契约文档提交。五模块 worktree 从同一个准备提交创建，main 不切换到 V2 业务版本。精确准备提交及 worktree 验证以私有备份中的 init-verification.json 和 `git worktree list --porcelain` 为准，避免在提交内写入自引用 hash。

仓库本地身份为 `Bili Remote Local Development <bili-remote-dev@localhost>`；未修改全局 Git 配置。未连接、fetch 或 push 远程仓库。提交前检查跟踪路径、私钥/Token 字面量和真实 .env 敏感值；发现的凭据字面量仅为已审阅的 README 开发示例、测试夹具。实际 .env、SQLite、下载/媒体和旧备份全部排除。静态截图文档保留，不用全局媒体扩展名规则误排源资源。

新增 .dockerignore 规则排除 .env*、.dev 与 backups，防止将后续开发凭据送入构建上下文；没有启动构建。除 .gitignore/.dockerignore 外，原业务源码、既有测试和生产配置未改变。

## 五工作区

| 路径 | 分支 | 预留开发端口（本次未启动） |
| --- | --- | --- |
| /opt/bili-remote-wt-naming | codex/naming-v2 | 18121 |
| /opt/bili-remote-wt-subtitle | codex/subtitle-v2 | 18122 |
| /opt/bili-remote-wt-workbench | codex/workbench-v2 | 18123 |
| /opt/bili-remote-wt-download | codex/download-v2 | 18124 |
| /opt/bili-remote-wt-filebrowser | codex/filebrowser-v2 | 18125 |

采用标准 git worktree 命令，以满足用户指定的精确 /opt 路径；创建前全量检查分支/路径存在情况，绝不覆盖已有工作。每个 worktree 仅检出安全跟踪文件，再独立运行 dev_init.py；不复制 .env/SQLite/生产下载目录。

## 开发配置和测试

在每个 worktree 根目录，`python3 scripts/dev_init.py` 从 .env.dev.example 生成本地随机凭据及空 .dev/ 根，已有配置不覆盖，不启动服务、不创建或复制数据库。初始化脚本拒绝在带根级生产数据库的 checkout 运行。.env.dev 权限 600；不打印生成密码。

测试分别运行 `python3 scripts/dev_test.py naming|subtitle|workbench|download|filebrowser`（选择一个参数，不含竖线），全量为 `python3 scripts/dev_test.py all`。脚本以当前 worktree 为 cwd，清除应用环境污染，并为该次运行分配独立临时存储，不加载 .env 或 .env.dev；新增对应 tests/v2/ 目录会自动加入。默认优先本地 .venv，否则只读使用 `/opt/bili-remote/.venv/bin/python` 共享已装依赖，不修改它。共享的是解释器/依赖，不是应用源码/测试库/运行数据；移动到其他机器需在独立 Python >=3.12 venv 安装 requirements-dev.txt 后传 --python。

当前宿主解释器为 3.11.2，基线 210 项通过，详见 acceptance.md。正式 Python >=3.12 兼容回归为后续待完成项，当前未安装/升级任何 Python/Node/Docker/Nginx 组件。真实上游能力与本次离线/本地回归区分，PT-Gen/Bangumi 验收尚未实测，不使用生产凭据补测。

后续若单独授权启动开发服务，须在当前 worktree 设置 `.env.dev` 的变量，绑定 127.0.0.1 与本模块端口；所有根均为本 worktree 的 .dev/。不得使用生产 docker-compose.yml 或生产 8111 端口。测试自身的回环临时服务器随用随关，不属于生产部署。

## Codex 启动命令

本机 codex-cli 0.163.0 已存在。`-C` 指定工作目录并接受初始提示的用法按 [OpenAI 官方 CLI 文档](https://developers.openai.com/codex/cli/reference) 及本机 `codex --help` 核对。下列命令仅列出，初始化过程没有启动五个业务会话。

```bash
codex -C /opt/bili-remote-wt-naming '先阅读 AGENTS.md 和 docs/v2/tasks/naming.md，核对架构与文件边界，等待业务开发指令。'
codex -C /opt/bili-remote-wt-subtitle '先阅读 AGENTS.md 和 docs/v2/tasks/subtitle.md，核对架构与文件边界，等待业务开发指令。'
codex -C /opt/bili-remote-wt-workbench '先阅读 AGENTS.md 和 docs/v2/tasks/workbench.md，核对架构与文件边界，等待业务开发指令。'
codex -C /opt/bili-remote-wt-download '先阅读 AGENTS.md 和 docs/v2/tasks/download.md，核对架构与文件边界，等待业务开发指令。'
codex -C /opt/bili-remote-wt-filebrowser '先阅读 AGENTS.md 和 docs/v2/tasks/filebrowser.md，核对架构与文件边界，等待业务开发指令。'
```

## 待审阅项

请审阅本次统一契约、旧持久化模式退出/兼容方式，以及建议的整季目录模板与 V2 编码标签（H.264/H.265）适配。PT-Gen 与 Bangumi 的实际开发凭据/能力核验，以及独立 Python 3.12 完整回归在后续开发阶段完成。本次不需要也未尝试读取生产配置来补齐这些项。
