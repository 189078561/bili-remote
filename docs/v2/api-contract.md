# API 与服务契约（V2 / schema_version=1）

状态：后续开发的共同规范，所有 `/api/v2/*` 均为待实现接口，本次不注册、不宣称已可调用。整合会话负责公共模型、依赖注入、认证/CSRF 与路由注册。不得为适配本文改动既有接口形状。

## 通用约束

所有资源都用 data-model.md 的 ResourceRef 和 ResourceContext，不接受任意服务器绝对路径。V2 字段 snake_case，模板变量按正式方案大写；ID/空值/来源语义遵从 data-model.md。同一当前上下文在下载、文件浏览、命名和工作台间传递，前端不得实现命名、副标题或主标题业务算法。

身份认证复用当前会话 Cookie；V2 所有 POST/PUT/PATCH/DELETE（包括仅预览的 POST）复用 `X-CSRF-Token` 和现有校验。GET 不能触发文件变更。请求与响应设置 no-store；日志不记录密钥、Cookie、查询鉴权参数或完整发布内容。

成功返回具体对象，不给旧接口额外加 envelope。V2 错误统一为 `{"detail":{"code":"stale_resource","message":"源文件已变化，请重新预览","fields":[]}}`。422=参数/模板/缺失执行条件；401=未登录；403=CSRF/越权；404=资源/任务不存在；409=冲突、预览过期、revision/fingerprint 失效；502=上游失败且无法用人工/部分结果继续；504=超时。上游可降级时返回 200+warnings，不清除用户字段。旧接口原错误形状保留。

资源数组上限 1000 个显式文件，目录扫描遵守既有最大文件数/深度设置；普通文件操作延用 200 项，分页上限 1000，模板上限 500 字符，元数据搜索上限 200 字符。V2 context 请求总量上限建议 4 MiB；执行前总是由服务端重读权限和指纹，不能信任前端伪造的 confirmed/来源事实。自定义模板仅白名单变量插值，禁止 eval、路径输出或任意对象属性访问。

## 模块内部接口

以下为结构化协议的签名规范，不是要求模块各自复制公共模型定义。纯生成函数同步，网络/探测/文件 I/O 异步或通过既有线程封装。公共 Protocol 由整合会话在 v2/contracts/ 落地，各模块先使用私有 fake 实现接口测试。

| 提供者 | 接口 | 消费者及保证 |
| --- | --- | --- |
| MetadataResolver | async search(query, media_type, providers, year=None) → CandidatePage；async resolve(selection, previous_context=None) → ResolveResult | naming/workbench；返回候选和 proposal，保护人工字段，不调用 metadata.save |
| MetadataResolver | async capabilities(provider) → ProviderCapabilities | 设置/工作台；区别 verified/unsupported/unconfigured/failed，未经实测不标 verified |
| NamingService | async analyze(resources, origins, context, rules) → ResourceContext | 下载/文件管理/工作台；共用识别与逐文件探测，不自动改名 |
| NamingService | preview(context, overrides) → RenamePreview；async apply(token, expected_revision, idempotency_key) → TaskRef；async undo_preview(batch_id) → RenamePreview | 工作台/文件管理；检查文件和 sidecar；执行/撤销共用 FileService 安全边界 |
| NamingMemoryRepository | get(identity, season) → EffectiveRules；put(scope, key, rules, expected_revision) → MemoryRecord | naming/subtitle/workbench；后两者只交规则白名单，不保存生成文本 |
| SubtitleService | render(context, subtitle_rule) → SubtitleResult | workbench；只返回副标题和缺项，四模板，不返回主标题/HTML/简介 |
| ReleaseTitleBuilder | build(context, release_title_rule) → ReleaseTitleResult | workbench；独立模板及站点规范，不直接复制 basename |
| PublishingService（V2） | compose(context, release_title, subtitle, overview, artifacts, format) → PublishPreviewResult | workbench；即时返回格式化结果，无 repository/session/project 参数 |
| FileBrowserAdapter | select() → ResourceRef[]；open_naming(selection, optional_context) | workbench/naming；复用现有 FileBrowser，不能重复上传资源或构建第二套后端 |
| DownloadCompletionAdapter | async on_completed(job_id, origin_refs) → RecognitionProposal | naming/task center；命名失败仅给 warning，不改变已完成下载状态；输出预览，不自行 render |
| TaskCenter | list/get(TaskRef)；cancel/retry(TaskRef) | 所有模块；内部路由既有 jobs/processing 执行器，带 kind 避免 ID 碰撞 |

## V2 资源识别接口（naming 会话导出 router，整合会话注册）

| 方法/路径 | 请求 | 返回 |
| --- | --- | --- |
| POST /api/v2/resources/resolve | {resources:ResourceRef[],origins:DownloadOrigin[],selection:{provider,id,media_type}/null,context:ResourceContext/null,manual_overrides:object} | {context:ResourceContext,changes:FieldChange[],capabilities:ProviderCapabilities[]}；无身份则候选/告警，不自动替换手选 |
| POST /api/v2/resources/probe | {resources:ResourceRef[],expected_fingerprints:object} | 202 {task:TaskRef}；结果只含逐文件 MediaProbe 与产物引用，不把完整 context/简介存入任务 |
| GET /api/v2/metadata/search | query,media_type=movie/series/anime,providers=tmdb,bangumi,year,page | {items:IdentityCandidate[],page,total_pages:null/int,warnings:Issue[]}；可选 PT-Gen 不作为强依赖 |
| GET /api/v2/metadata/capabilities | provider=tmdb/bangumi/ptgen | ProviderCapabilities；读取已有核验结果，不靠 GET 隐式执行带凭据探测 |

Resolve 中客户端回传的来源值是建议，provider/ref 与新探测证据由服务端验证；manual 值可接受但来源必须标人工。资源文件变化会使旧 probe 失效。ProviderCapabilities={provider,state,checked_at,authentication:configured/unconfigured,supported_features:string[],sample_schema_hash:null/string,warnings}；不返回密钥或认证头。PT-Gen 开发核验由 naming 模块的显式开发工具/测试执行，不在本次初始化发起。

## NamingService 接口

| 方法/路径 | 请求 | 返回/副作用 |
| --- | --- | --- |
| POST /api/v2/naming/preview | {context,overrides:{file_values,episode_overrides,manual_names},rules:NamingRule} | 200 RenamePreview；无文件写入、无命名历史写入 |
| POST /api/v2/naming/apply | {preview_token,expected_revision,confirm:true,idempotency_key} | 202 {task:TaskRef,batch_id}；只有校验成功才入既有 processing 队列 |
| POST /api/v2/naming/undo/preview | {batch_id} | 200 RenamePreview；核对执行后指纹、原路径占用及权限 |
| POST /api/v2/naming/undo/apply | {preview_token,expected_revision,confirm:true,idempotency_key} | 202 {task:TaskRef,batch_id}；同样禁止覆盖 |

RenamePreview={preview_token,expires_at,context_revision,operations:[{source:ResourceRef,target_relative_path,temporary_required,sidecar_of,source_fingerprint,warnings}],conflicts:Issue[],can_apply:bool}。token 随机、会话绑定、默认 30 分钟有效；只存进程内短期预览状态。token 覆盖完整规则、人工覆盖、映射和源指纹；客户端不能在 apply 时另外提交新目标名。每次 apply/undo 重验，不只检查 token 是否存在。

同 token/idempotency_key 重复请求返回同一任务；相同 key 与不同 payload 返回 409。目标冲突、非法名、越根、集数冲突、关联模糊或过期时拒绝入队。改名不提供 overwrite=true。批次内部源目标相互交换可在唯一临时名管线处理，不能当作外部同名目标覆盖。

## 规则记忆接口（由整合会话提供存储，naming 定义语义）

| 方法/路径 | 请求 | 返回 |
| --- | --- | --- |
| GET /api/v2/naming/memory | identity_key,season_key 可选 | {layers:MemoryRecord[],effective:{naming_rule,subtitle_rule,episode_rules},revision} |
| PUT /api/v2/naming/memory | {scope,identity_key,season_key,expected_revision,selected_title,aliases,naming_rule,subtitle_rule,episode_rules} | MemoryRecord；首次 expected_revision=0，冲突 409；整次原子校验，不部分写入 |

请求使用允许字段的严格 schema。出现 subtitle_text/release_title/overview/bbcode/html/generated_names/project_id/session_id 等生成内容或项目字段返回 422；不能用任意扩展字典绕过白名单。SubtitleService 自身无保存文本端点。全局设置仍通过原 `/api/settings` schema 白名单，整合会话增加新键。

## 发布工作台接口

| 方法/路径 | 请求 | 返回 |
| --- | --- | --- |
| POST /api/v2/subtitles/render | {context,rule:SubtitleRule} | SubtitleResult；副标题专属，禁止 platform 变量 |
| POST /api/v2/release-title/render | {context,rule:ReleaseTitleRule} | ReleaseTitleResult；不调用 SubtitleService 生成主标题 |
| POST /api/v2/publishing/preview | PublishPreviewRequest（见 data-model.md） | PublishPreviewResult；不含会话 ID、不写发布内容 |

发布请求不需要 project_id、session_id、release_document_id 或先创建任何记录。媒体报告引用真实工具结果，组合前端传入的主标题/副标题/简介文本；手动编辑不会触发自动覆盖。客户端对 context_revision 不匹配的慢响应直接丢弃，再按最新上下文请求；debounce 只调度请求，不实现生成逻辑。

副标题示例（仅示例数据）：

```json
{"text":"魔法少女育成计划restart 魔法少女育成計畫restart 魔法少女育成計画restart 第01季 第05集 | 类型：动画 / 奇幻 | 声优：南条爱乃 / 近藤玲奈 / 井上穗乃花","missing_fields":[],"warnings":[],"context_revision":3}
```

## 下载、文件管理和任务联动

下载 WebUI 继续用 `/api/bili/*`、`/api/resolve`、`/api/episodes/formats*`、`/api/jobs*` 和 `/api/history*`；登录二维码、Cookie 加密及 Web/TV 路径保持。完成适配器将 bvid/season_id/ep_id/cid、原始标题、episode_index、原路径 ResourceRef 传给统一资源识别。默认仅给预览/工作台跳转；自动改名设置必须显式开启且受同一执行安全规则约束。

文件管理继续用 `/api/files/roots`、browse、meta、text、image、rename、operations/preview、operations/apply；智能命名入口改为调用统一 V2 NamingService。四类逻辑根为 downloads/torrents/images/release_info；`scope=mounts` 的八挂载配置选择兼容保留，但不作为八类主导航目录。旧 `/api/files/rename/analyze|preview|apply` 的 fingerprint 契约保留，不把 V2 preview_token 强塞给旧接口。

| 方法/路径 | 请求 | 返回/职责 |
| --- | --- | --- |
| GET /api/v2/tasks | kind=download/processing 可选，status,limit<=500,offset | {items:TaskRef[],limit,offset}；只读聚合，按 created_at 降序+kind/id 稳定排序 |
| GET /api/v2/tasks/{kind}/{id} | kind 必填 | TaskRef；下载 progress 沿用百分比，工具无进度时 null |
| POST /api/v2/tasks/{kind}/{id}/cancel | {} | {task:TaskRef}；委托旧取消机制 |
| POST /api/v2/tasks/{kind}/{id}/retry | {} | {task:TaskRef}；只允许可重试任务，文件操作/改名必须重新预览 |

日志仍委托原 `/api/jobs/{id}/log`、`/api/tasks/{id}/logs`，TaskRef 提供 kind 决定路径。工具任务用现有 `/api/files/screenshots*`、`/api/torrents/jobs`、`/api/release-info/jobs`、`/api/uploads/jobs` 等，需由整合会话审计 payload 白名单及新无草稿报告适配，严禁把组合文案塞进这些任务。任务成功是实际产物可读取，不是假成功或只返回一个 UI 状态。

## 现有资料适配器已知事实与待核验

`metadata.py` 当前 TMDB 实现使用 api.themoviedb.org/3 的 search/movie|tv 及 movie|tv/{id}，支持 API Key 或 Bearer，详情只读中英文并追加 external_ids。当前不具备完整 Alternative Titles、cast/crew、季集与 Bangumi 适配，必须新增/验证，不能假设旧字段已经存在。

PT-Gen 当前本地代码使用配置的 ptgen_url；若末段是 /info，则搜索改同级 /search?q=...&source=douban；否则在配置路径传 search=...；详情传 url=...，可选查询 apikey。当前解析预期搜索 data 为列表、条目含 title/year/link；详情解析字段来自现有 adapter 的提议，不能当作所有部署版本保证。实际开发核验须记录服务版本、认证方式、状态码、真实字段结构、超时/403/429/非法 JSON 等；只保存脱敏 fixture，禁止包含鉴权参数。未核验字段一律按未知/缺失降级。

## 兼容与注册约束

现有所有路由及旧 SQLite 表保留。新工作台不得调用旧 `/api/releases*`、`/api/publish/sessions*`、`/api/media/metadata` 保存端点来维持状态，不能恢复旧草稿 UI。旧兼容接口的存在不代表它们是 V2 的依赖。

各模块交付独立 APIRouter 和无全局副作用服务工厂，测试可以在自建 FastAPI 应用中注册 router；不得修改 web.py/services.py 绕过公共文件归属。整合会话一次性落实模型、存储、任务 handler、导航和注册；新增接口应以机器可检的 schema 和失败样例做最终契约测试。
