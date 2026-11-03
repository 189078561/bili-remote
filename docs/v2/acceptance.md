# 开发与最终验收标准

本阶段只验收环境准备，不把待开发的 V2 功能列为已完成。架构方案请用户审阅；后续业务开发及生产部署分别按授权开展。

## 已实测基线（2026-11-03，Asia/Shanghai）

- 源码基线：main 的 `2d76f1f`（现有功能 + 安全忽略规则）；测试使用改动前源码的独立副本 `/tmp/bili-remote-v2-baseline-h62jah4y`。
- 命令：`/opt/bili-remote/.venv/bin/python -m pytest -q -ra --junitxml=<私有备份目录>/baseline-pytest.xml`，仅在隔离源码副本执行。
- 结果：**210 passed，0 failed，0 skipped，1 warning，120.13 秒**；含浏览器、API、数据库、下载、文件安全、媒体工具等既有测试。
- 警告：Starlette TestClient 对 httpx 的弃用提示，不在初始化阶段升级依赖。
- 解释器：宿主机 venv Python 3.11.2；pyproject 要求 >=3.12、生产 Dockerfile 为 3.12。此结果是当前环境基线，Python 3.12 完整回归仍是后续验收项。
- 生产只读检查：`http://127.0.0.1:8111/healthz` 返回 `{"status":"ok"}`，容器健康；未重启或部署。
- SQLite 快照：22 张表（含 SQLite 系统表），schema_migrations=1..4，`PRAGMA integrity_check=ok`。基线时下载任务 completed=7/cancelled=1，processing completed=22/failed=3/cancelled=1；这些是既有生产快照状态，不是本次测试产生的失败。
- 原始 pytest 日志和 XML 保存在 `/opt/bili-remote-backups/20261103-223644-CST/`，该目录包含敏感配置/数据库，仅 root 访问，禁止分发或复制进开发工作区。

## 环境初始化验收

1. Git 可用，原代码有 main 基线和 v2-integration 准备提交，无远程仓库；Git 身份只设仓库本地。
2. 外部时间戳备份有源码压缩包、私有配置、SQLite 一致性快照、目录清单和校验摘要；不重复备份媒体、缓存、venv 或旧备份，保留至少 2 GiB 磁盘余量。
3. 五个指定 worktree/分支均存在，初始 HEAD 完全相同，并且是 v2-integration 准备提交；main 保持原始代码基线。
4. 每个工作区包含本套契约、AGENTS、任务说明和安全配置示例；本地 .env.dev 权限 600、随机不同凭据、.dev/ 独立，不含生产 .env/数据库/媒体。
5. 每个工作区运行各自 `python3 scripts/dev_test.py <模块>` 成功；它们使用该 worktree 源码和独立临时测试数据，可读取共享依赖解释器但不安装或修改原 venv。
6. 工作树干净；生产源代码、生产配置及业务数据未被初始化工作改写；Git 不跟踪敏感/运行文件，也不误忽略源码、测试、静态资源和文档。

## 模块验收矩阵

| 模块 | 必须通过的行为 | 失败/边界验证 |
| --- | --- | --- |
| Naming + Resolver | 电影/剧集/动画/整季；多语言候选与自定义名；B站 ep_id→TMDB 季集；逐文件真实参数；三层规则与特殊映射；预览、关联同步、执行和撤销 | 歧义身份不覆盖人工；上游不可用可手动；缺少参数不猜；路径/重名/非法字符/季集/大小写冲突；互换文件名临时过渡；中途失败恢复；指纹漂移拒绝覆盖 |
| Subtitle | 四模板逐字样例；多语言去重；所选季真实总集数；字段显隐/顺序、3/5/8/all、排序/标签/分隔符/导演开关/自定义 | 无播出平台字段；缺演员/声优/年份/季集不编造；Bangumi 优先声优；人工值不被刷新覆盖；无文件 I/O/发布保存 |
| Workbench | 左共享文件浏览+右四视图；识别与命名常驻；独立 ReleaseTitleBuilder/SubtitleService/简介；完整 BBCode/HTML/Markdown 即时组合；现有媒体工具正常 | 不创建项目/草稿；不依赖 publish_sessions/release_projects；刷新仅恢复任务；慢响应不能覆盖新修订；HTML 安全；不自动 PT 发布或添加 qBittorrent |
| Download | 既有登录/Web/TV/解析/分P/合集/画质编码/aria2/队列/重试/日志/历史不回退；优化布局；完成来源联动和文件入口 | 联动失败不反改下载成功；默认只预览；缺少 bvid 等字段显式 null；重复完成通知幂等；不重复命名算法/任务中心 |
| FileBrowser | 复用既有后端；四根、树/面包屑/分页搜索/图像/文本/复制移动删除/视频参数；共享选择、命名与移动端 | traversal/绝对路径/根外 symlink/权限拒绝；保护根与活动任务；编辑指纹冲突；覆盖须明确确认；不改物理存储结构 |

## 正式需求二十项追踪

| # | 正式验收点 | 责任与证据 |
| --- | --- | --- |
| 1 | 下载与登录不退化 | download + 整合，现有 API/下载/浏览器回归 |
| 2 | 单视频和整季均可识别 | naming，单文件/嵌套目录合成样本 |
| 3 | TMDB 多语言可选 | naming，alternative/original/localized 候选及手选保留测试 |
| 4 | Bangumi 补充 | naming，脱敏实际结构 fixture+可靠来源缺失降级 |
| 5 | PT-Gen 实际能力验证 | naming，版本/认证/端点/字段/异常脱敏报告，mock 单独标记 |
| 6 | ffprobe 参数真实 | naming，生成的真实短视频和缺失/异常样本；MediaInfo 交叉核验 |
| 7 | 三类文件名模板 | naming，正式模板与给定示例、整季差异参数 |
| 8 | 集数可人工改 | naming，递增/递减/offset/逐项/特殊映射复用 |
| 9 | 字幕关联安全 | naming + filebrowser，chs/cht/eng/外音轨/模糊关系/冲突 |
| 10 | 预览与撤销 | naming，预览零写入、临时名、失败恢复、撤销源漂移 |
| 11 | 四种副标题 | subtitle，正式四样例、所有配置组合的代表性测试 |
| 12 | 无播出平台 | subtitle，内置/自定义变量与规则 schema 拒绝测试 |
| 13 | 三职责分离 | workbench + subtitle + naming，独立服务/接口及导入依赖审查 |
| 14 | 规则跨会话 | 整合 + naming，临时数据库重启和乐观锁/层级覆盖测试 |
| 15 | 无发种项目和草稿 | workbench + 整合，V2 流程写入审计/禁止旧保存端点与本地存储 |
| 16 | 即时内容 | workbench，独立编辑/复制、revision 与三种格式 |
| 17 | 全部工具正常 | workbench + 整合，截图/拼图/图床/报告/制种/手动 qBittorrent |
| 18 | 同一识别结果 | 整合，三入口 context/revision/规则版本一致性测试 |
| 19 | 移动端 | filebrowser + download + workbench，390/768/1440 宽度、触摸/无横向溢出 |
| 20 | 旧核心回归 | 整合，完整 210 项基线加 V2 新测、旧路由保留检查 |

## 测试策略和数据隔离

现有测试放 tests/，新增模块测试仅放独占 tests/v2/<模块>/；合成资源由 tmp_path 创建，禁止使用生产数据库或真实下载文件。外部 HTTP 默认 stub/transport fake；真实服务核验使用独立开发凭据，输出脱敏 fixture，不将 API Key/Cookie/鉴权 URL 写日志。认证、来源可信性和数据库写入白名单必须由真实边界测试覆盖，不能只有字符串快照。

每个模块先运行 `python3 scripts/dev_test.py <模块>`；脚本自动附加存在的本模块 V2 测试目录。初始 subtitle 组只测既有 assistant API 兼容，不能声称 V2 副标题已实现；所有其他初始分组同样仅是旧代码回归。全量命令 `python3 scripts/dev_test.py all`。需要指定解释器时加 `--python /path/to/python`，当前默认只读复用已有依赖，无 pip 安装。

正式集成必须在独立 Python >=3.12 开发环境再跑全部检查；不得为了补此项升级宿主现有 Python 或重建生产容器。开发环境搭建另行在新的 venv/容器内完成。既有浏览器测试有固定 /tmp 截图产物目录，全量浏览器截图验收宜串行避免产物互相覆盖；模块普通测试各自临时目录可并行，不共享应用数据库。

## 最终整合放行条件

公共模型及 OpenAPI 与本文一致；新增 API 认证/CSRF/越权/冲突测试通过；旧 API 无删除/改名/返回结构回退；全部基线及新增单元、服务、API、迁移和浏览器回归通过，跳过或外部未实测项明确列出，不能写成通过。

迁移仅在合成的 v1..4 历史数据库上验收自动备份、升级、重复初始化和数据保留；禁在生产上做验收。V2 工作台完整使用过程须证明未写入 publish_sessions/release_projects/media_metadata 草稿或将文案塞入任务表/localStorage。新任务 project_id=NULL，通知和进度刷新可恢复，取消、重试、执行幂等和文件冲突均有证据。

所有待确认架构选择和真实服务能力写入交付说明，经用户审阅后才进入相应开发/集成步骤；架构确认不等于部署授权。部署、旧表删除、旧数据清理都不属于本次任务。
