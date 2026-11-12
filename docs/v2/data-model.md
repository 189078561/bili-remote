# 统一数据模型（V2 契约版本 1）

本文件为后续实现规范，不代表已建表或已上线接口。JSON 使用 snake_case，时间使用 UTC ISO-8601，界面按 Asia/Shanghai 展示；SQLite ID 保持整数，外部 B站/TMDB/Bangumi ID 在 V2 边界用十进制字符串避免精度/类型冲突。未知值为 null 或空列表，不能填造假内容。旧接口保持原数据类型。

## 基础对象

| 对象 | 字段与约束 |
| --- | --- |
| ResourceRef | root_key: downloads/torrents/images/release_info；relative_path: 正规化 POSIX 相对路径；kind: file/directory；fingerprint: 服务端生成的源状态摘要。禁止绝对路径、..、NUL 和根外符号链接 |
| FieldValue<T> | value:T/null；source: bili/tmdb/bangumi/ptgen/ffprobe/mediainfo/filename/manual/default/memory；source_ref: 不含凭据的 ID/记录引用或 null；manual:bool；confirmed:bool；confidence: 0..1/null；updated_at:UTC。仅人工选定的字段 manual=true；default 不得标作已探测事实 |
| Identity | media_type: movie/series/anime；tmdb_id:string/null；bangumi_id:string/null；selected_by: automatic/manual；confidence；confirmed:bool。TMDB adapter 中 tv 映射为 series，动漫为类型分类而非另一 TMDB namespace |
| TitleCandidate | id:上下文内稳定键；text；language: BCP47/und；kind: localized/english/original/alternative/custom；source/source_ref；selected:bool。NFKC+空白归一化去重但保留显示原文，不按固定首条英文名自动锁定 |
| PersonCredit | person_id:string/null；name:FieldValue<string>；role: actor/voice_actor/director；character:string/null；provider_order:int/null。同人可多角色，展示去重与排序单独配置 |
| DownloadOrigin | job_id:int；bvid/season_id/ep_id/cid:string/null；original_title:string/null；episode_index:int/null；original_resource:ResourceRef；api_mode:web/tv。只读来源事实，不带 Cookie/签名下载 URL |
| EpisodeSelection | season:FieldValue<int>；episode:FieldValue<int>；season_episode_count:FieldValue<int>；scope:single/season；mapping_key:string/null。季号可为 0（特别篇），默认 1；集号至少两位，超过两位不截断 |
| EpisodeMapping | bili_season_id/bili_ep_id/bili_cid；tmdb_id；tmdb_season:int；tmdb_episode:int；method:structured/tmdb/filename/manual/memory；confirmed:bool。series identity 与 season 必须匹配；不只以文件列表顺序为键 |

自动证据顺序为 B站结构化 > TMDB 季集 > 文件名 > 提示人工。用户显式 override 独立存储并锁定，后续重新识别只给出候选差异。支持 S01E02、S01 EP02、S01 - 02、第02集、第02话、EP02、01；有歧义或跨季冲突必须提示。offset/start/step/direction 和单集 override 是规则，不是替换来源事实。

## ResourceContext：一次识别，多方消费

| 字段 | 类型/用途 |
| --- | --- |
| schema_version | 固定 1 |
| context_id / revision | 进程内或页面内不透明 ID / 单调整数；不作为数据库项目 ID |
| resources | ResourceRef[]，文件或目录；目录展开受既有扫描数量/深度限制 |
| origins | DownloadOrigin[]，可为空；跨入口使用同一来源映射 |
| identity | Identity/null，尚未确认时不得覆盖已手选身份 |
| identity_candidates / titles | 候选身份摘要[] / TitleCandidate[] |
| selected_title | FieldValue<string>；系列记忆可回填选择，允许自定义 |
| metadata | title_zh/title_zh_hant/title_ja/original_title/year/overview/poster_url 为 FieldValue；genres:FieldValue<string[]>；cast/voice_cast/directors:PersonCredit[]；外部 links 白名单。overview 仅临时使用 |
| episode | EpisodeSelection |
| episode_mappings | EpisodeMapping[] |
| files | MediaFile[]，每文件独立 probe、季集与 sidecar 信息 |
| effective_rules | NamingRule + SubtitleRule 及其来源层级/版本；不含密钥 |
| warnings / conflicts | 结构化 code、field/ref、message，不含认证信息 |

客户端所有入口持有同一个 revision；切换身份、名称、季集、媒体参数或规则时 revision 增长，旧命名预览作废。重新获取资料只返回 proposal/change-set，保留 manual=true 的字段，用户采纳后更新 context。上下文和完整发布预览不写 SQLite/localStorage/任务表；刷新可重新选文件、读取规则并重建。

## 媒体参数与关联文件

MediaFile = {ref:ResourceRef, episode:EpisodeSelection, probe:MediaProbe, sidecars:SidecarRef[]}。

MediaProbe 包含逐文件的 width/height、resolution、video_codec、audio_codec、audio_channels、frame_rate（精确分子/分母）、bit_depth、hdr（SDR/HDR10/HLG/DV 等实测标签[]）、duration_seconds、container、probe_tool/version、probe_error。技术字段均为 FieldValue，可人工修正但须标 manual，原实测值作为当前上下文证据保留；失败不沿用首文件参数。source 和 group 属命名规则，不是探测字段。

V2 显示命名标签明确规范为 H264/H265、AAC 等；旧探测值 H264/H265 在边界转换，不能反向改旧接口。codec 与 encoder 独立，未经证实不能输出 x264/x265；HDR、DV、声道、帧率、位深仅在模板开启且有可靠值时输出。

SidecarRef = {ref, video_ref, kind:subtitle/external_audio, language:string/null, suffix_tokens:string[], association:exact_stem/manual, confirmed:bool}。只同步同 stem 明确关系或人工确认关系；模糊前缀不匹配。保留 chs/cht/eng、forced/SDH 等语言/用途尾缀，不改字幕内容、不烧录。

## NamingRule 与模板

```text
movie:
{Title}.{Year}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}
series / anime_single:
{Title}.{Year}.S{Season:00}E{Episode:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}.{Extension}
season directory:
{Title}.{Year}.S{Season:00}.{Resolution}.{Source}.{VideoCodec}.{AudioCodec}-{Group}
```

整季目录模板为本架构建议默认，整季内视频仍按单集规则。混合技术参数的整季不从首文件复制：目录级参数只取所有文件一致且已确认的公共值，否则省略并警告。目录改名也需独立预览及确认，不能自动创建或移动到新目录。

NamingRule = {template_id, templates:{movie,series,anime,season}, selected_title_id/custom_title, title_separator:'.', source:FieldValue<string>, group:FieldValue<string>, season_default:1, episode_width:2, include_hdr:false, include_channels:false, include_frame_rate:false, include_bit_depth:false, extra_tags:[], episode_mode:detected/start/offset, start_episode, episode_step, episode_direction:increment/decrement, episode_overrides:{mapping_key:int}, auto_rename_high_confidence:false}。

默认 Source=WEB-DL、Group=HDVWEB，来源为 default；允许 BluRay/WEBRip/Remux 及自定义。Title 为用户选择名称，Extension 无前导点且保持实际容器后缀；默认只调整文件名，不修改容器内容。可选变量 HDR、AudioChannels、FrameRate、BitDepth、OriginalName；未知变量拒绝，不使用 eval。

模板对外保持正式方案大写变量和 `:00`。V2 专用解析器将 :00 映射为至少两位十进制，不要求用户改为 Python 语法；旧接口 `{title}`、`{season:02d}` 按兼容 adapter 继续有效。省略缺失字段时合并多余分隔符；不得去掉导致媒体身份歧义的信息后静默执行。缺失 Source/Group 可使用明确标注的规则默认，技术参数未知则省略并提示。

示例（参数来自探测，片源/组为已显示规则值）：
`The.Last.of.Us.2023.S01E01.1080p.WEB-DL.H265.AAC-HDVWEB.mkv`。

## SubtitleRule（不含实际副标题文本）

template_id: tv_episode/tv_season/anime/movie/custom；field_order:string[]；visible_fields:string[]；cast_limit:3/5/8/all；cast_order:string[]（person ID/人工排序键）；name_order:[zh,zh_hant,ja,original,en]；labels:{genres:'类型',cast:'主演',voice_cast:'声优',director:'导演'}；label_separator:'：'；section_separator:' | '；list_separator:' ' 或 ' / '；show_director:bool；custom_template:string/null。

| 内置模板 | 规范输出 |
| --- | --- |
| tv_episode | 中文名称 第XX集 \| 类型：类型1 类型2 \| 主演：演员1 演员2 演员3 |
| tv_season | 中文名称 第X季 全XX集 \| 类型：类型1 类型2 \| 主演：演员1 演员2 演员3；季号默认中文数字（第一季），集数为所选季实际条目数 |
| anime | 中文名称 繁体名称 日文原名 第XX季 第XX集 \| 类型：类型1 / 类型2 \| 声优：声优1 / 声优2 / 声优3；季/集至少两位 |
| movie | 中文名称 原始名称 (年份) \| 类型：类型1 类型2 \| 导演：导演名称 \| 主演：演员1 演员2 演员3；导演可关闭 |

名称按归一化结果去重，保留用户配置顺序。无值字段隐藏并给 warnings，不生成空标签、假演员或假季集数。人工填写及排序优先于重新获取；声优优先 Bangumi。播出平台字段在规则、模板变量及生成器白名单中全部禁止；不能借自定义模板变量重新引入。人工自由编辑生成后的文本只作用于当前页面，不写规则。SubtitleResult={text,missing_fields:string[],warnings:Issue[],context_revision}，无保存历史接口。

## 主标题和发布内容

ReleaseTitleRule={site_profile:string,template:string,title_candidate_id:string/null,separator:string,include_hdr:bool,include_channels:bool,include_frame_rate:bool,include_bit_depth:bool,overrides:允许的结构化字段字典}。模板变量使用与命名模板一致的结构化字段白名单，额外站点标签只接受已确认的规则值；格式配置与最终文本分开，不存主标题生成历史。它使用独立模板与站点配置，不与 NamingRule 共用最终字符串，接收 MediaFile/Identity/规则字段而非把 basename 当主标题。ReleaseTitleResult={text,warnings,context_revision}。

PublishPreviewRequest={context,release_title:string,subtitle:string,overview:string,poster:ArtifactRef/null,reports:ArtifactRef[],screenshots:ArtifactRef[],links:string[],format:bbcode/html/markdown}。响应 {content,validation:{ready,errors,warnings},context_revision}；不含 session_id/project_id，不写存储。HTML 及 BBCode 按输出格式转义，不直接信任上游 HTML；缺项可复制预览但 ready=false，不能自动发布。

ArtifactRef={kind:screenshot/collage/report/torrent/upload,id:string,resource:ResourceRef/null,url:string/null}。实际报告、图片和种子允许保存；字段不能装入“完整发布内容”以规避禁存规则。

## 持久化设计（未来迁移，由整合会话执行）

现有 schema_migrations 为版本 1..4。下一迁移建议 v5，实际落地前再次读取最高版本分配；本阶段不调用 Database.initialize 操作生产库。所有新增表非破坏式，旧表保留，不搬迁旧项目和草稿。

| 表/存储 | 建议字段 | 限制 |
| --- | --- | --- |
| settings（现有） | 全局默认规则、默认副标题配置、目录、下载、截图、服务凭据 | 设置 schema 白名单原子校验；密钥延用加密存储和脱敏返回 |
| naming_memory（新增） | id INTEGER PK；scope global/series/season；identity_key NOT NULL（tmdb:movie:ID / tmdb:tv:ID / bangumi:subject:ID 或 global）；season_key NOT NULL（无季用 -1）；provider_ids_json；selected_title/aliases_json；naming_rule_json；subtitle_rule_json；episode_rules_json；revision；created_at/updated_at | UNIQUE(scope,identity_key,season_key)；scope 与季键互相校验；用 API 乐观锁；只含规则，禁生成结果/简介/演员传记 |
| download_resource_origins（新增） | job_id；root_key；relative_path；fingerprint；bvid/season_id/ep_id/cid；original_title；episode_index；created_at | UNIQUE(job_id,root_key,relative_path)；最小下载来源，不复制 auth/cookies；文件移动后由安全操作更新或标记 stale |
| v2_rename_batches（新增） | id 不透明主键；status prepared/running/completed/failed/rolled_back；task_id 可空；created_at/finished_at | 不引用 project_id；仅实际确认执行的批次，不保存所有未执行预览 |
| v2_rename_items（新增） | batch_id FK；ordinal；source_root/path；temporary_path；target_root/path；before/after_fingerprint；status prepared/staged/applied/rolled_back/failed；error | 最小可恢复/撤销日志；路径信息是文件操作审计，不是生成文件名历史；失败时严禁清理未恢复临时文件 |
| processing_tasks（现有） | project_id=NULL；task_type；status；input_json/result_json；error；retry/cancel；idempotency_key；timestamps | 仅执行必要引用、参数、真实产物 ID；不存标题/副标题/简介/完整内容/context 快照 |
| jobs/parse_history（现有） | 下载队列、来源解析与历史字段保留 | 不改旧 status 枚举，不把 V2 自动识别变为下载任务完成的前置条件 |

规则覆盖：当前人工选择 > 季集记忆 > 系列记忆 > 全局规则 > 内置默认。身份不确定时不套另一影视规则；TMDB 与 Bangumi 关联由确认后 provider_ids 解析，不猜等价 ID。规则 API 对 revision 不匹配返回 409。

改名两阶段：预检全量（含大小写/权限/保护目录/符号链接/集数和 sidecar 冲突）→ 持久化最小执行计划 → 同目录唯一临时名过渡 → 最终名无覆盖落位 → 写后指纹；失败按实际阶段尽力恢复并报告逐项结果。文件系统和 SQLite 无跨系统事务，必须把这一事实反映在恢复测试中，不能承诺原子批量改名。撤销前核对写后指纹和源路径是否空闲，发生漂移返回冲突，绝不覆盖新文件。

## TaskRef 与统一任务中心

TaskRef={kind:download/processing,id:int,task_type:string,status:queued/running/completed/failed/cancelled/auth_required,progress:number/null,created_at,finished_at,artifact_refs:[],error:string/null,retry_allowed:bool,cancel_allowed:bool}。download:id 与 processing:id 是不同身份；auth_required 仅下载有效。进度百分比为 0..100；未知为 null，不把未完成显示 100%。

TaskCenter 是既有两个队列的投影，不是新执行引擎。下载状态及日志仍由 /api/jobs 提供，工具由 /api/tasks 提供。取消 queued 立即终止，running 设置请求并在安全点停止；文件变更不支持通用 retry，必须重新预览。完成通知以 task kind/id+终态版本去重，刷新恢复查询，不持久化发布文案。
