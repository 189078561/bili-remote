# Naming / Metadata 模块交付

日期：2026-11-04（Asia/Shanghai）。工作区 `/opt/bili-remote-wt-naming`，分支 `codex/naming-v2`。

本次业务开发依据用户后续明确指令启动，覆盖初始化阶段的“尚不开发”限制；文件归属、生产隔离、公共文件禁改和不得发布等限制继续有效。仅修改 naming 白名单内文件。没有修改顶层任务/架构文档；本文件和 task-status.md 是本模块任务交付记录。

## 实现范围与导出

- `bili_remote.v2.metadata.MetadataResolver`：异步 `search(query, media_type, providers, year=None, page=1)`、`resolve(selection, previous_context=None)`、`capabilities(provider)`；`select_title(context, id)` 为明确的人工选择。返回统一字典契约，不保存资料、不创建项目。
- `TMDBProvider(token, client=None)`：TMDB v3 movie/tv 搜索、四种语言详情、Alternative Titles、Translations、cast/crew、external_ids、所选季真实 episode 列表。动漫使用 tv namespace。语言端点回退原文时不伪装成英文或繁体译名。部分语言/季集失败保留可用资料。
- `BangumiProvider(token='', client=None)`：v0 搜索、条目、别名、人员、角色声优及关联条目。未标语言的别名保持 und，tags 不冒充 genres；不将所有动画原名猜成日文。Bangumi 声优优先，人工编辑仍优先。
- `PTGenProvider(endpoint='', api_key='', client=None)`：适配 `/info`→`/search?q=&source=douban` 及旧 worker `?search=`，详情 `?url=`，可选 `apikey`。结构化字段才进入资料；不从格式化发布文本猜演员/年份。未配置或失败时降级，不阻塞手动命名。
- `NamingService(file_service, probe, owner_session=..., audit=None, tasks=None, resolver=None, memory=None, origins=None)`：异步 analyze、apply、undo_preview、enqueue_probe、probe_resources；同步 preview。服务实例必须按认证会话隔离，不得使用全站共用 owner。
- `MediaProbeAdapter(settings)`：调用旧 media_probe 的真实 ffprobe subprocess 入口，在 V2 边界转换 H264/H265；逐文件参数、精确分数帧率、位深/HDR 与失败空值。`evidence_for(ref)` 只保留有限进程内实测证据，人工技术字段不会清除证据。
- `NamingMemoryService(repository)`：规则白名单、global/series/season 覆盖、人工优先、身份不确定只用全局规则；写入由公共仓储执行原子乐观锁。没有自建存储。
- `create_router(get_service, authenticate=..., csrf=...)`：导出 `/api/v2/resources/*`、`metadata/*`、`naming/*` 及 memory 路由。未注册到主应用。所有请求要求认证，所有变更方法包括预览要求 CSRF；成功/错误均 no-store，统一错误形状。仅接受最多 4 MiB 请求。
- `NamingTaskHandlers(service).handle(task_type, input_data, should_cancel=...)`：可注册到既有 processing manager 的处理函数；没有新队列、worker、任务表或发布会话。
- `static/v2/naming/naming.js` 导出 `mount(container, {request, selectResources, onContextChange, onTask, context})`；返回 setContext / setBatch / destroy。独立 CSS 和模板片段已备好，未修改主页面挂载。

实现使用文档字典形状作为模块边界，未在模块中复制共享正式模型。`bili_remote/v2/contracts/` 在当前工作区尚不存在，公共类型及完整 OpenAPI 模型仍由整合会话提供。

## 规则、确认和修订

内置 movie/series/anime/season 模板使用大写变量和 `:00`，未知变量、属性访问、转换、路径模板均拒绝。默认 Source=WEB-DL、Group=HDVWEB、Season=1 标记为 default；技术参数未知时省略且告警。真实文件扩展名不变。逐文件参数不从首文件借用。

无身份时从已验证下载标题或文件名提取搜索提示，搜索 TMDB 电影/剧集及 Bangumi 动画；唯一精确名称匹配可读取资料，但 identity 为 automatic/confirmed=false，执行前仍需用户确认。多候选不锁定；名称也不固定选择首个英文名。显式 provider/id 选择表示人工确认身份。

名称候选中文、繁体、日文、英文、原始名和别名均可选择；可自定义。`manual_overrides` 支持 selected_title、title_zh、title_zh_hant、title_ja、original_title、year。技术参数/季集/逐文件名称由 preview overrides 明确提交，忽略客户端回传的伪造探测来源。

同一 context 的新规则或人工覆盖会推进 context_revision，使旧 token 失效。客户端收到 preview 后必须使用返回的 context_revision 更新本地 revision。并发识别遇到修订变化返回 409，慢响应不能覆盖更新。

集数依据：已确认来源映射/人工锁定 → B站结构化信息 → 匹配真实 TMDB 季集 → 文件名。B站索引本身不能证明 TMDB 跨季映射，未确认映射会阻止执行。支持开始集号、步长、递增/递减、偏移和逐项修正。文档 NamingRule 未声明 offset 数值字段，本实现不私自增加；偏移值由 `episode_rules.offset` 规则记忆提供，NamingRule.episode_mode=offset 应用它。整合时如需一次性临时 offset，应先统一扩充公共契约，不能另造接口。

整季目录单独预览和确认；不能同批改目录和其内部视频。目录标签只使用整季一致且已确认的公共参数，不一致则省略并提示。

## 安全执行与恢复

复用 FileService 根解析、受保护运行目录、活动下载/处理任务保护、文件名校验和指纹。增加组件级 symlink 拒绝、大小写冲突、目录/子项重叠检查及全量预检。文件必须原地改名；字幕按最长精确视频 stem 和明确语言/用途后缀匹配，保留 chs/cht/eng/forced/SDH 等后缀。模糊前缀不关联。外音轨必须人工明确关联和确认。

目录数量/深度预算在递归指纹前执行，目录指纹、预览校验和执行复查均受限制；展开多个资源时共享扫描预算，避免每个选择重置限制。只对需要返回的视频生成文件引用，不为每个中间目录重复计算整棵子树指纹。

确认后先持久化最小执行计划，再入现有 processing 队列；所有源先改唯一临时名，再无覆盖落最终名。Linux `renameat2(RENAME_NOREPLACE)` 必须可用，不使用先检查再覆盖的可移植 fallback。每次操作通过目录 fd 固定根内所在目录，执行前重读权限/活动保护/源指纹，拒绝源漂移和新占用目标。

执行器在每步写 intent，支持“移动成功、状态尚未写入”窗口的恢复。失败先退回临时名，再恢复原路径；原路径被占用时绝不覆盖，保留未恢复临时文件及逐项错误。取消在安全点执行同样恢复流程。撤销验证写后指纹、原路径占用和所属会话。文件系统与数据库无跨系统事务，不承诺批次原子性。进程内所有会话共用改名锁；整合应保持现有单 worker，若改多进程须提供进程间操作锁。

同 token 只准备一个 batch；队列响应中断后重试使用同 batch 和同稳定队列幂等键。任务适配器必须对已完成任务也提供持久化幂等保证，不能只查询 active 任务。

## 必需的公共装配（未在本分支修改）

1. 公共模型/Protocol：按 data-model.md 落地 ResourceRef/Context、FieldValue、EpisodeMapping、MediaProbe、NamingRule、RenamePreview、TaskRef 等。当前 router 使用受校验的字典边界；正式 OpenAPI 模型仍须整合。
2. NamingMemoryRepository：`get(identity_key_or_null, season_or_null)` 返回 `{layers:[MemoryRecord...]}`；`put(scope, {identity_key,season_key}, rules, expected_revision)` 原子校验版本后返回 MemoryRecord。每层记录可含顶层规则，或 `rules` 对象。get 返回的本模块 revision 是 `{scope:revision}` 版本集合；写入用对应层的整数 revision。层来源在 effective.sources 中。跨重启持久化和 SQLite 迁移由整合完成。
3. 最小审计仓储：同步 `create(batch)`、`get(batch_id)`、`update(batch)`，返回前持久化。batch 含 id、owner（必须为不含 Cookie 的不透明会话摘要）、status、undo_of、items；item 含 source ResourceRef、temporary_path、target_relative_path、source/after fingerprint、ordinal、intent/status/error。仅确认后创建；禁止把 context/资料/发布文案放入审计。迁移/新增表由整合会话实现。启动时枚举未完成批次后调用 executor.recover(batch_id)，不清理失败现场。
4. 任务适配器：`enqueue_naming(batch_id, stable_key, undo=False)->TaskRef`；`enqueue_probe(resources, expected_fingerprints)->TaskRef`。改名 input 只含 batch_id；probe input 只含 resources 和 expected_fingerprints。project_id 必须 NULL。向现有 TASK_TYPES / dispatch 注册 v2_rename_apply、v2_rename_undo、v2_media_probe，委托 NamingTaskHandlers；不能直接传给旧 rename_apply（旧 handler 仍依赖项目）。V2 文件依赖须纳入公共活动任务保护，执行时排除当前任务自身。
5. 来源仓储：`get(job_id, original_resource)->DownloadOrigin/null`，返回服务端已验证事实；original_resource 指纹须与当前文件匹配。未接入时客户端来源只用于定位，不能被当成 B站事实。
6. 根引用：使用服务端 NamingFiles.reference 生成统一 `_path_fingerprint`。旧普通改名的 `_quick_fingerprint` 不是该摘要，不能直接混用。选择器和下载钩子交 ResourceRef，默认只调用 resolve/preview，绝不直接执行。
7. 安全装配：authenticate(Request) 返回会话摘要，csrf(Request) 明确返回 bool；get_service(Request) 返回该会话服务。现有只抛异常、成功返回 None 的 CSRF 校验器需薄包装为 True。所有请求复用现有 Cookie/X-CSRF-Token。
8. UI：主页面加载 naming.css 和 ES module，使用共享 FileBrowser 的选择回调；`request` 注入现有认证/CSRF helper。普通选择回调返回 ResourceRef[]；可接收 `{kind:'sidecar'}` 选项用于外置字幕/音轨。onTask 接公共 TaskCenter。控制器不写 localStorage。
9. 能力状态：显式开发核验工具输出 provider-schemas.json；整合可把其中 capabilities 通过 MetadataResolver(capability_records=...) 注入。GET capabilities 不触发网络核验。凭据从开发/设置安全组件注入，不从生产环境取样。
10. 打包：当前 pyproject.toml 的 package-data 只含旧 templates/*.html 和 static/*。整合发布前须把 V2 嵌套资源纳入构建和装配测试；本模块没有越权修改依赖/打包公共文件。

## 证据和未完成项

测试、文件清单和需求逐项状态见 task-status.md；接口示例见 contract-examples.json；实测边界见 provider-capabilities.md。

本分支提供独立模块实现，**当前主应用仍未注册 V2 路由和入口，公共仓储/迁移/任务注册未落地，不能声称整站功能已可用**。fake 仓储和队列只用于明确标注的模块测试，不是持久化功能替代。缺少 TMDB 独立开发凭据和 PT-Gen 测试地址/凭据，未完成其认证接口实测。当前依赖解释器为 Python 3.11.2，Python >=3.12 的正式整合回归尚无证据。这些限制不能以 mock 通过代替。

## 全量回归发现的公共截图问题

最近一次全量结果 289 passed / 1 failed；目录扫描补强后本模块 102 项全过。失败位于旧 tests/test_screenshots_api.py 的三个截图全部成功断言，实际只成功两个。原 API 用例孤立重跑通过，但已在完全临时的合成媒体上确定性复现根因：

```text
视频：testsrc2=size=320x180:rate=10:duration=3，mpeg4/yuv420p
旧 ScreenshotService._extract_frame_with_retries，duration=3，skip_start/end=0，width=240
target=2.95 → ScreenshotError: 附近画面均为黑帧或提取失败
target=2.90 → success, actual=2.900
```

screenshots.py 的随机采样上界允许 duration-0.05，但 10fps 最后一帧在 2.9 秒；重试偏移 `(0,.005,-.005,.012,-.012)*effective` 的最大回退仅 .036，2.95-.036=2.914，仍越过最后一帧。建议整合会话依据实际 fps/最后有效帧限制采样及重试上界，或加入可靠的向前一帧回退；需覆盖低帧率、时间戳指定和短片，不调整现有成功数量断言。本分支未修改 screenshots.py 或旧测试。该问题须修复并重新全量回归，不能因单次随机重跑通过而放行。
