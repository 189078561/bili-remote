# 媒体工具与 PT 发布

媒体工具已收拢为文件管理的功能按钮，参数集中在独立设置页；发布信息与发布辅助合并为发布资料，只汇总已有结果。入口说明见 [Web UI 操作说明](ui-rebuild.md)。报告、种子和上传在后台处理，刷新页面可恢复任务、日志和结果；上传任务重试会跳过已成功项目。

## 建议先设置

- **视频截图与拼图**：数量、随机或均匀取样、跳过片头片尾、宽度（0 为源画面尺寸）、PNG/JPG、脚本风格/纯画面、HDR/DV 转换、自动上传及上传版本。
- **图床上传**：Catbox、ImgBB 或 Pixhost；ImgBB 需要 API Key。自动上传默认关闭，已有默认图床保持不变。
- **种子与 qBittorrent**：Tracker、private、source、piece 大小、输出目录；加入 qBittorrent 仍需手动点击。
- **发布信息与辅助**：TMDB API Key 或 Read Access Token、PT-Gen 服务地址及可选密钥、报告格式、BDInfo 超时、发布复制格式和 hide/quote 排版。

密钥只保存在服务端，普通设置接口不返回密钥。PT-Gen 采用 pt-gen-cfworker 兼容 JSON 接口（`?url=...`、可选 `apikey`），默认不绑定任何公共实例。没有凭据或服务不可用时仍可手动录入影视资料。

## 截图与拼图

“截图与拼图”有独立页面，文件管理器中的“视频截图”会带入选中资源。也可以直接选择视频或光盘目录。独立截图默认使用 PNG 和脚本风格：黑色时间栏、白色时间字、50% 阴影与白边；另存 `-raw` 纯画面文件，不影响源视频。宽度设置只针对画面，风格图片会增加时间栏与边缘。

可以逐张预览、重截、排序、删除、上传，或下载纯画面。拼图从纯画面生成一次装饰，支持 5×4、1×4、自定义行列或自动排布，并显示当前视频的媒体信息。拼图沿用 JPG 输出。旧会话只有一个版本时继续读取原有截图。

色彩默认自动：先检测 SDR、HDR10/10+、HLG 或 DV，再将截图映射至 SDR。libplacebo 使用软件 Vulkan 的 llvmpipe，无需显卡或 privileged 容器；可选 zscale 兼容路径。DV Profile 5 不允许按 HDR10 回退；Profile 7/8 仅在元数据确认有兼容基础层且色彩标签有效时允许回退。无法正确处理时返回错误。实际处理器保存到会话媒体信息中。

本次不包含字幕烧录、ISO 挂载与原视频转码。

## MediaInfo、BDInfo 与光盘目录

镜像内置 MediaInfo 和固定提交的 BDInfoCLI。普通视频保存完整 MediaInfo 原文，缺少工具或失败时 ffprobe 摘要只作为诊断信息，不能满足发布检查。报告不会因大小限制被静默截断。

- 蓝光目录：读取 MPLS，默认最长有效播放列表，允许手动切换；截图使用完整蓝光播放列表，不把最长 m2ts 当成整片。保存完整 BDInfo，并按要求截取 DISC INFO、PLAYLIST REPORT、VIDEO、AUDIO、SUBTITLES 区段用于发布。
- DVD 目录：默认选择 VOB 合计体积最大的 VTS 组，允许手动切换；报告读取该组 IFO 与首个正片 VOB，截图读取整组 VOB 时间线。
- 剧集：默认逐视频报告，可填写代表文件的相对路径。发布时可以选择报告；不同分辨率/编码/音频组缺少代表 MediaInfo 时保持草稿状态。

## TMDB 与命名

输入电影或剧集名，可按年份筛选；明确选中候选作品后，预览中英文名、年份、海报、简介和 IMDb 标识。首次选片直接填充，覆盖已有不同内容时才预览差异；发布工作区自动尝试 PT-Gen，失败保留 TMDB 资料；关联豆瓣链接需要核对，匹配失败可手动填写。资料可单独保存，也会随媒体报告和发布草稿保存。

批量重命名提供同一个选片器及 `{title_zh}`、`{title_en}`，选定后生成双语模板。电影模板不带季集字段，剧集模板使用季集编号。所有改名仍须先通过原有预览和冲突检查，再点击确认。

## 猫站发布规则预设

英文 0day 主标题与中文副标题分别保存。主标题不会把探测到的 H264 自动写成 x264；采用编码器名时需要人工确认。蓝光来源动画使用 BluRay。类型、质量、地区和字幕/配音标签须人工核对；原创与禁转不自动生成。

简介默认顺序为海报、影视简介、完整报告、截图。BBCode 支持 `[hide=MediaInfo]` / `[hide=BDInfo]` 或 quote，完整报告的内容不被编辑。缺少必需标题、海报、简介、报告或截图时可继续保存、复制草稿；通过当前检查后才标为就绪。IMDb/豆瓣均无对应页面时，可用海报及人工简介补充。就绪仅表示通过该预设的检查，实际发布仍需核对站点要求。

## 新增与兼容接口

- `GET /api/media/search`、`GET /api/media/details/{media_type}/{tmdb_id}`、`POST /api/media/ptgen`：获取待采用的资料。
- `GET/POST /api/media/metadata`：保存及读取共享影视资料。
- `GET /api/media/optical`：获取可选播放列表或 DVD 标题组。
- `POST /api/release-info/jobs`、`POST /api/torrents/jobs`、`POST /api/uploads/jobs`：返回 202 与现有 processing task；继续通过 `/api/tasks` 查看、取消、重试和读取日志。
- 截图请求新增 `format`、`style`、`sampling_mode`、`hdr_processor`、`auto_upload`、`upload_variant`；来源支持 `disc_selection`。省略参数时由服务端读取设置。
- 发布会话通过 `metadata` 保存影视字段、报告选择、分类确认及检查结果；原有标题、描述、关联记录字段保留。
- `/api/uploads/formats?variant=original|thumbnail` 区分原图与缩略图链接，按请求 IDs 的顺序生成。

此前媒体工具升级的 SQLite schema v4 新增了共享影视资料表；本次 UI 重构保持 schema v4 不变，无新增表或列。报告原文和新增发布字段使用原有 JSON 列。升级前自动备份数据库。原同步接口、旧截图会话和历史记录继续兼容。
