# SubtitleService：PT 副标题并行开发任务

工作区 `/opt/bili-remote-wt-subtitle`；分支 `codex/subtitle-v2`。本次只准备任务；后续收到业务开发指令后执行，遵守 AGENTS 及架构/API/模型/归属/验收文档。

## 功能范围

SubtitleService 只生成 PT 副标题；不得生成文件名、发布主标题、简介、MediaInfo、BBCode/HTML，亦无项目或草稿保存功能。消费统一 Naming/Resolver 结构化数据，所有字段允许人工调整，不自行访问另一套 TMDB/Bangumi 或解析文件名。

四种内置模板必须与 data-model.md 一致：电视剧单集“中文名 第XX集”；整季“中文名 第X季 全XX集”；日本动画“中文名 繁体名 日文原名 第XX季 第XX集”；电影“中文名 原始名 (年份)”。后接对应类型、主演/声优、可关闭导演；整季必须用选中季实际总集数，声优优先 Bangumi，多语言名去重。

支持字段显隐、字段顺序、演员数量 3/5/8/全部、人工演员排序、名称顺序、中文标签、冒号、空格/斜杠和自定义模板。彻底排除播出平台变量及自动生成流程。缺失字段不编造，不输出多余空标签或分隔符；人工选值和锁定不能被重取资料覆盖。

## 修改文件范围

仅 `bili_remote/v2/subtitle/`、`bili_remote/static/v2/subtitle/`、`bili_remote/templates/v2/subtitle/`、`tests/v2/subtitle/`、`docs/v2/modules/subtitle/`。建议 renderer.py/rules.py/router.py 清晰分离纯生成和传输。

## API 依赖与数据结构

实现 `render(context:ResourceContext, rule:SubtitleRule) -> SubtitleResult`；导出 POST `/api/v2/subtitles/render` 的独立 router。使用 FieldValue/PersonCredit/EpisodeSelection/来源标记与 context_revision，不改变共享 schema。

规则保存由 NamingMemoryRepository/整合会话负责；只交 SubtitleRule（模板和偏好），无保存 text、生成历史或发布会话 API。规则需要跨会话复用，实际输出只在当前页面中。依赖缺失用私有 fake context/Protocol，不复制 NamingService、主标题 builder 或持久化仓储。

## 测试要求

运行 `python3 scripts/dev_test.py subtitle`；初始化只有既有 assistant API 兼容测试，新增 tests/v2/subtitle/ 自动加入。四正式样例逐字断言；测试中日繁体相同名称去重、整季与全剧集数不同、全部 cast 限制与排序、导演开关、空值/手动值/缺少声优、标签/分隔符/字段序、自定义变量白名单、平台变量拒绝、未知字段拒绝及 context_revision 传播。

测试应证明 renderer 无网络/文件写入，无主标题/完整内容生成，无数据库 text 写入；不将字符串快照作为唯一证据，加入配置行为和缺失资料边界。

## 交付标准及公共文件禁改

交可独立测试的纯服务、router、四模板与代表性配置 fixture、测试结果和 `docs/v2/modules/subtitle/handoff.md`，列整合挂载和规则保存需求。

不得修改 web.py、database.py、services.py、main.py、config.py、index.html、app.js、全部既有全局 CSS、全局 API 注册、迁移入口、publishing.py、metadata.py、naming.py、settings_registry.py、公共模型/夹具/依赖清单或其他模块文件。只通过工作台消费本模块输出，不能自行替换其主标题或简介。
