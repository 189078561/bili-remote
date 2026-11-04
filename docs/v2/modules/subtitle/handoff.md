# SubtitleService 交付说明

状态：模块实现与离线测试完成；等待整合会话注册公共路由并接入 NamingMemoryRepository。

## 模块职责与文件

- bili_remote/v2/subtitle/renderer.py 提供纯 render(context, rule) -> SubtitleResult 和 SubtitleService.render。只读取同一份 ResourceContext，不请求元数据、不访问文件或数据库，也不生成主标题、简介、BBCode 或 HTML。
- bili_remote/v2/subtitle/rules.py 校验 SubtitleRule。允许的模板为 tv_episode、tv_season、anime、movie、custom；规则只包含模板及格式偏好，未知字段（含 platform）全部拒绝。
- bili_remote/v2/subtitle/router.py 导出 create_router(service=..., require_authenticated=..., require_csrf=...)，提供 POST /api/v2/subtitles/render。认证和 CSRF 必须由调用方显式注入；接口限制 4 MiB 请求体，成功/模块校验错误均为 no-store。
- bili_remote/v2/subtitle/memory.py 导出 SubtitleRuleMemory，只适配共享 NamingMemoryRepository 的 get(identity, season) / put(scope, key, rules, expected_revision)。保存载荷只有 subtitle_rule 规则字段；不传入 context、生成文本或发布草稿。
- tests/v2/subtitle/ 覆盖四种内置模板、自定义变量安全、字段配置、来源优先级、人工字段保护、API 边界和规则仓储适配。

## 格式和数据语义

默认输出与 data-model.md 一致：

电视剧单集：长夜 第02集 | 类型：剧情 历史 | 主演：演员甲 演员乙 演员丙
电视剧整季：迷雾档案 第一季 全52集 | 类型：剧情 悬疑 | 主演：演员甲 演员乙
日本动画：魔法少女育成计划 魔法少女育成計畫 魔法少女育成計画 第01季 第05集 | 类型：动画 / 奇幻 | 声优：声优甲 / 声优乙
电影：星际回声 Echoes Beyond (2025) | 类型：剧情 科幻 | 导演：导演甲 | 主演：演员甲 演员乙

标题映射读取统一上下文中的 selected_title、titles 候选及 metadata.title_zh/title_zh_hant/title_ja/original_title/title_en。支持 zh、zh_hant、ja、original、en 配置顺序并按 NFKC/空白归一化去重。人工选名保留在 shared context 并优先显示。

季、集、季集总数只读取 episode.season、episode.episode、episode.season_episode_count。整季模板不读取全剧 episode_count；缺少真实选中季总集数时省略该段并返回 missing_fields。

人物只读取共享的 PersonCredit；人工 name.manual=true 的声优名单优先保持。否则动画声优列表优先选择 name.source=bangumi 的条目。演员数量支持 3/5/8/all；排序支持 cast_order 的 person ID / name:<姓名> 和上下文人工 manual_order。FieldValue 的 source/manual/confirmed 标记不在 renderer 内重写或落库。

内置规则支持字段显隐、顺序、名称顺序、数量、人物顺序、标签、冒号、列表分隔符、字段分隔符和导演开关。自定义模板最多 500 字符，只允许 Title、TitleZh、TitleZhHant、TitleJa、OriginalTitle、TitleEn、Year、Season、Episode、SeasonEpisodeCount、Genres、Cast、VoiceCast、Director；季/集数可用 :00。属性读取、索引、格式转换和未知变量均拒绝。值缺失时删除所在模板段，不生成空标签或悬空分隔符。

## API 契约样例

请求：

    {"context":{"revision":7,"identity":{"media_type":"series"},"metadata":{"title_zh":{"value":"长夜","source":"tmdb"},"genres":["剧情"]},"episode":{"episode":{"value":2,"source":"manual","manual":true}},"cast":[{"role":"actor","name":{"value":"演员甲","source":"tmdb"}}]},"rule":{"template_id":"tv_episode"}}

响应字段严格为契约的 text、missing_fields、warnings、context_revision。模块在独立 FastAPI 测试应用中挂载 router；正式 /api/v2/* 注册仍由整合会话修改 web.py，当前分支没有触碰全局注册或公共安全文件。

## 规则记忆接线

整合时将 NamingMemoryRepository 注入 SubtitleRuleMemory(repository)。resolve(identity, season, overrides) 仅把已确认 identity/season 交给仓储；未确认身份只读全局规则，避免套用其他影视系列偏好。仓储的 EffectiveRules/layers 按 global → series → season 合并，当前页面 overrides 最后覆盖；输出完整规则交给 SubtitleService.render。

save(scope="global" | "series" | "season", identity=..., season=..., rule=..., expected_revision=...) 先严格校验，只把用户明确设定的格式字段封装为 {"subtitle_rule": {...}} 交给共享仓储。系列键按契约形成 tmdb:movie:<id>、tmdb:tv:<id> 或 bangumi:subject:<id>；季键加 #season:<number>。乐观锁和冲突响应由 NamingMemoryRepository 负责。

当前 integration 分支尚未向本工作区提供 NamingMemoryRepository 实现或公共 contracts，因此 memory 测试使用本地仓储替身验证适配载荷、覆盖顺序及 revision 边界；这不是 SQLite 持久化实测。整合会话须用真实仓储再做跨进程/重启验收，并确认 settings 全局规则 schema 白名单已登记。

## 验证和外部依赖

- 命令：python3 scripts/dev_test.py subtitle
- 结果：36 passed，1 条既有 Starlette/httpx 弃用告警；全仓库回归为 244 passed，1 条同一既有告警。
- 纯生成器没有外部接口依赖。它不伪造 TMDB、Bangumi 或 PT-Gen 返回值；实时资料由共享 Resolver 填充 ResourceContext。当前分支没有生产或开发 API 凭据，也没有发起网络请求。Bangumi 优先只依据 context 中明确的 source=bangumi 标记。
- 本 worktree 复用的宿主解释器为 Python 3.11.2；架构要求的 Python 3.12 完整回归尚未执行，遵从环境约束没有升级或改动宿主运行时。
- compileall 和 git diff --check 已执行。全仓库回归命令为 python3 scripts/dev_test.py all。

## 整合会话待办

1. 在公共 contracts 定型后，核对实际 ResourceContext / SubtitleRule Pydantic 字段是否与模块消费字段一致。
2. 将 create_router 挂载到已存在的认证、CSRF 和 no-store API 组；不为模块建立独立身份或存储体系。
3. 注入已实现的 NamingMemoryRepository，并在共享存储做层级合并、乐观锁、重启保留及“仅规则字段”白名单测试。
4. 工作台调用 render 接口后用返回的 context_revision 丢弃过期响应；手动编辑仅留在当前页面，不调用规则保存接口，也不提交 subtitle_text。
