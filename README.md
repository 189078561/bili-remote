# Bili Remote

单用户 B 站远程下载与发种辅助工具，以固定版本 yt-dlp 和 aria2c 为下载底层。导航只有首页、文件管理、发种助手、B站下载、设置五个入口。发种助手采用桌面三栏和移动三步骤，共用同一套文件服务、配置、任务与结果状态。下载页保留 Web/TV 扫码登录、链接解析、选集、格式选择、解析历史和任务队列。媒体文件存储在服务器；生成的完整缩略图、媒体报告和种子可直接下载。

本次重构的功能映射、参考许可证与验收记录见 [WebUI 重构验收](docs/webui-refactor.md)。

## Docker 部署

在 Linux x86_64 设备上保存一份 [docker-compose.yml](deploy/docker-compose.yml)，直接使用已发布的私有镜像 `ghcr.io/189078561/bili-remote:v2.0.0-rc.2`。PT-Gen 会随应用启动。

1. 在 YAML 的 `environment` 中填写 `ADMIN_PASSWORD` 和 `APP_SECRET`。后者可用 `openssl rand -hex 32` 生成，升级时保留原值。
2. 第一次在该设备执行 `docker login ghcr.io -u 你的GitHub用户名`，密码处输入有此镜像读取权限的 `read:packages` classic token。
3. 在 YAML 所在目录启动。

```sh
docker compose up -d
```

默认应用端口为 8111。时区、下载连接数和 Cookie 设置可直接改 `environment`，宿主机端口和媒体目录分别改 `ports` 与 `volumes`。改完再次执行同一条启动命令。更多说明见 [安装与升级](deploy/install.md)。

## 能力与边界

- 使用登录账号实际拥有的权限获取普通视频、分 P、番剧和影视内容。
- 番剧 `ep/ss` 链接会读取完整季/集列表，按集创建独立任务；`ep` 默认选中当前集，`ss` 默认不选。
- 单集格式按分辨率分组并显示视频/音频编码、帧率、HDR 和码率，视频选项只保留 1080P 及以上清晰度；多集下载可选择统一画质偏好，缺失时自动就近回退，并可抽样检测所选集数的实际格式覆盖率。
- 支持 Web 与 TV 两条解析线路。TV 线路用于无水印片源，但不会解除地区限制，也不支持多 P 视频。
- 支持 B 站可提供的 8K、4K、HDR、杜比视界、FLAC 和杜比音轨，但画质取决于视频本身和账号权限。
- 默认使用 aria2c 8 路连接、4 MiB 分片和 4 MiB 最小拆分下载；aria2 失败时自动回退到 yt-dlp 原生下载。
- TV 解析只展示接口实际返回的码流挡位；下载前会对 baseUrl/backupUrl 做短测速，Web 与 TV 同画质片源也会参与对比。
- 不绕过会员、付费、地域或 DRM 限制。受 DRM 保护的内容可能无法由 yt-dlp 下载。
- 不转码，仅合流音视频。容器由 yt-dlp/ffmpeg 按实际编码自动选择。
- 下载阶段不下载字幕、弹幕、封面或 NFO。剧集批量重命名只修改原目录内的文件名；文件管理器中的复制、移动和删除是独立的手动操作。
- 旧资源项目工作台的界面入口已从主界面隐藏，后端接口与数据库结构暂时保留，不影响下载、文件浏览和新发布工具。
- 图床上传会把用户明确选择的图片发送到 Catbox、ImgBB 或 Pixhost；种子制作、发布资料和发布辅助默认在本地完成，不会自动向 PT 站点提交。

## 从源码部署

含随附 PT-Gen 的部署要求 Linux x86_64、Docker Engine、Docker Compose 插件，以及可访问 B 站和 PyPI 的网络。

1. 准备配置：

   ```bash
   cp .env.example .env
   chmod 600 .env
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   将生成的随机值写入 `.env` 的 `APP_SECRET`，并设置强密码 `ADMIN_PASSWORD`。生产环境保持 `SECURE_COOKIES=true`，通过 HTTPS 访问。

2. 准备可写目录。容器默认以 UID/GID `1000:1000` 运行：

   ```bash
   sudo mkdir -p /srv/bilibili-downloads ./data ./imports ./staging ./releases ./torrents ./images ./release-info
   sudo chown -R 1000:1000 /srv/bilibili-downloads ./data ./imports ./staging ./releases ./torrents ./images ./release-info
   ```

   如果宿主机用户 UID 不是 1000，需要同步修改 `Dockerfile` 中的 `app` 用户和目录所有者后再构建。

3. 构建并启动：

   ```bash
   docker compose up -d --build
   docker compose ps
   curl http://127.0.0.1:8111/healthz
   ```

4. 按 [deploy/nginx.conf.example](deploy/nginx.conf.example) 将域名转发到 `127.0.0.1:8111`，替换域名和证书路径，然后检查并重载 Nginx：

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

5. 打开站点，先使用 `.env` 中的应用账号登录，再点击“扫码登录”绑定 B 站账号。

## 使用

### 单条下载

粘贴 B 站链接，点击“解析”。格式列表会显示当前登录态可见的分辨率、动态范围、编码和大小。默认选择“最高可用视频 + 最佳音轨”，也可以选择具体格式。

命名模板使用 yt-dlp 输出模板，例如：

```text
Bilibili/%(uploader)s/%(title)s [%(id)s].%(ext)s
```

模板必须是相对于下载根目录的路径，并且包含 `%(ext)s`。中文文件名会保留，非法文件名字符由 yt-dlp 清理。

### 番剧选集与批量任务

粘贴番剧 `ep` 或 `ss` 链接后，控制台会列出正片、特别花絮和预告分组，并支持正序或倒序排列。花絮分组默认折叠，可单独勾选或“全选正片”。每个选中集数会创建一个独立任务，方便逐集查看进度、取消、重试和读取日志。单次最多创建 600 个任务。

选择多集时，格式列表先按解析样本生成批量画质偏好；点击“检测所选集数”会并发抽样最多 8 集，并在每个选项中显示该画质覆盖了多少个已检测集数。检测只影响选项展示，缺失画质仍会按优先级自动就近回退，不会因此中断下载。

集数任务使用“番剧 / 合集默认模板”。服务端会把 `%(playlist_title)s`、`%(playlist_index)02d`、`%(title)s` 和 `%(id)s` 渲染成该集的实际值，并保留 `%(ext)s` 交给 yt-dlp。

### 解析历史

成功解析的链接会自动写入解析历史，最多保留最近 200 条。历史记录保存格式和集数快照，可直接“使用记录”恢复选择，不需要重新请求 B 站；“重新解析”会刷新快照。可删除单条或清空全部历史，不会影响已经创建的下载任务。

### TV 线路

在账号卡片中完成 TV 扫码登录后，可在新建下载时切换 TV 线路。番剧使用 TV playurl，普通单 P 视频也可使用 TV 线路；多 P 视频请切换回 Web 线路。TV token 过期时任务会进入“需要登录”状态，重新扫码即可。

TV 线路只使用 `android_tv_yst` 片源，不会混入 Web PC 流；否则会重新带回 Web 水印。TV 线路不能绕过地区限制、会员权限或 DRM。地区受限内容会明确提示“当前服务器 IP 无法播放，TV 线路不能解除地区限制”。TV 线路返回的实际可用码流会原样展示，不会根据 support_formats 伪造更高画质。

### 下载加速与风控

镜像内置 aria2c，默认 8 路连接、4 MiB 分片和最小拆分，小体积音轨也能并发下载。视频和音频按顺序下载，最后由 ffmpeg 合流。明确限流时在原节点退避并降低并发。节点连续返回服务端错误时尝试同轨备用地址，403 或链接过期时优先尝试备用地址，必要时重新获取一次原轨播放地址。切换来源时重新下载未完成的轨道。

可通过 `.env` 调整：

```text
ARIA2_ENABLED=true
ARIA2_CONNECTIONS=8
ARIA2_RESUME_RETRIES=2
ARIA2_MIN_SPLIT_SIZE=4M
ARIA2_PIECE_SIZE=4M
TV_CDN_PROBE=true
CDN_PROBE_TIMEOUT_SECONDS=3
CDN_PROBE_SAMPLE_BYTES=524288
CDN_PROBE_CONNECTIONS=8
LINE_COMPARE=true
```

`ARIA2_CONNECTIONS` 最大 16。网络较慢或频繁出现 403 时可降到 8 或 4。`ARIA2_RESUME_RETRIES` 控制 aria2 连接中断后的断点续传重试次数，默认 2 次，同节点重试会保留 `.aria2` 断点。`CDN_PROBE_SAMPLE_BYTES` 控制短测速采样量，默认 512KB；`CDN_PROBE_TIMEOUT_SECONDS` 控制单节点测速上限。`LINE_COMPARE` 只允许 Web 线路把 TV 同画质片源纳入测速；TV 线路始终只使用 TV 片源。关闭 `ARIA2_ENABLED` 后完全使用 yt-dlp 原生下载。这些环境变量作为首次启动和“恢复默认值”的默认值；登录后也可在独立设置页在线调整，保存值会持久化并覆盖环境默认值。

### 文件浏览

登录后默认进入文件浏览器。主页面、下载目录选择、复制/移动目标和设置中的目录选择共用同一套 `FileBrowser` 组件与 `/api/files/*` 后端服务。左侧是可展开的真实目录树，右侧是当前目录的文件列表；移动端会切换为上下单列布局。顶部快捷入口对应四类目录：

- 资源下载目录：沿用 `DOWNLOAD_ROOT_HOST`，B 站下载任务仍写入原目录。
- 种子目录：`TORRENTS_ROOT_HOST`，用于保存 `.torrent` 文件。
- 图片目录：`IMAGES_ROOT_HOST`，用于保存视频截图和预览图。
- 发布信息目录：`RELEASE_INFO_ROOT_HOST`，用于保存 MediaInfo、BDInfo 等资料。

接口只接受根标识和相对路径，并拒绝 `../`、绝对路径以及符号链接逃逸。目录树按需加载，同时显示子目录和文件；目录树、文件列表、面包屑与选择状态使用同一路径状态，刷新时会同时更新。目录树和面包屑始终可继续浏览真实子目录。

文件列表支持自然排序、名称排序、修改时间排序、大小排序、方向和分页，并可按需定位到指定项。双击文件可打开预览：JPG、PNG、GIF、WebP 图片按比例只读显示；TXT、MD、JSON、YAML、XML、CSV、LOG、NFO、SRT、ASS、VTT 及常见配置文件支持页面内编辑，单文件上限 2 MiB。文本保存会保留原编码和换行风格，检测外部修改并原子写入；二进制、SVG 和 HTML 不会按文本打开。

完整模式下支持多选复制、剪切、粘贴、移动到、删除以及普通文件/目录重命名。复制和移动目录时禁止移入自身或其子目录；默认不覆盖目标，冲突时可取消、跳过或明确确认覆盖；仅大小写不同的同名目标也会被拦截。删除确认会列出对象及目录下级数量。批量复制、移动和删除先预览逐项目标、冲突和源指纹，再进入现有 `processing_tasks` 后台队列，并逐项报告成功、失败和跳过结果；剪切剪贴板只在任务完整成功且没有跳过项时清空。根入口、当前任务依赖目录、默认下载输出目录以及 `/data` 下的数据库、备份、日志和运行目录均受保护。

### 媒体工具与 PT 发布

发种助手将文件选择、配置操作和生成结果集中在同一页面。手机和平板宽度不超过 900px 时切换为三个步骤，切换步骤保留文件、参数和结果。左栏复用文件管理的 `FileBrowser`，搜索在后端分页前执行。媒体信息自动读取，名称统一使用 TMDB 搜索，用户选择条目后载入中英文名称与简介。主标题、副标题、简介可分别编辑和复制；自动生成保留手动修改，明确确认重新生成后才会替换。

截图采样与最终图片拼接由同一个持久化任务完成。选择单张或多图、时间点/采样范围、数量、列数/行数和画面宽度后直接生成完整 JPG。每次操作的布局不会改变全局设置。结果提供“下载缩略图”和“生成图床链接”，上传成功展示 URL/BBCode，失败保留原因与重试。组合任务按所选步骤依次执行；浏览器刷新后恢复已提交任务及后续步骤。关闭浏览器时已提交的后台任务继续执行，尚未提交的组合步骤在重新打开页面时继续。

非下载工具以页面内状态和右下角通知显示进度。文件复制/移动/删除保留确认和冲突处理，执行后关闭配置对话框并显示内联进度。旧截图会话编辑、MediaInfo/BDInfo、图床记录、完整发布编辑器及 qBittorrent 保留在高级工具中。

- 文件管理的“生成缩略图”将所选资源交给发种助手。主流程默认生成带媒体信息栏和黑色时间条的 JPG（质量 95）；旧高级截图工具仍支持 PNG/JPG、逐张重截、排序、纯画面和重新合成。
- HDR/DV 通过 libplacebo + 软件 Vulkan 转换为 SDR，保留 zscale 兼容路径；不具备兼容基础层的 DV 不会按普通 HDR 静默处理。
- 镜像内置 MediaInfo 和固定版本 BDInfoCLI，支持普通视频、蓝光目录播放列表和 DVD 标题组；ISO 留待后续。报告保留原文，ffprobe 摘要不能代替发布必需报告。
- 图床支持 Catbox、ImgBB、Pixhost，自动上传默认关闭；保留本地原图，支持逐张重试、原图/缩略图链接及 BBCode、HTML、Markdown。
- TMDB 选定作品后自动尝试 PT-Gen，失败保留已有资料。首次直接填充，覆盖已有字段时预览差异，关联条目需核对采用；选片组件复用于双语重命名。
- PT 发布提供英文主标题、中文副标题、影视链接、海报、简介、报告、截图与分类字段。按用户提供的猫站规则检查，不完整可保存、复制草稿，完整后标为就绪。
- 种子继续使用真实 bencode 引擎，默认参数从设置读取，后台计算分片；qBittorrent 添加由用户手动触发。

新页面入口与默认值见 [Web UI 操作说明](docs/ui-rebuild.md)，界面验收见 [UI 验收记录](docs/ui-rebuild-validation.md)。底层工具与色彩实测范围见 [媒体工具说明](docs/media-tools.md) 和 [工具验收记录](docs/media-tools-validation.md)。

### 全局设置

设置是独立二级页面 `/settings`，不在左侧业务工作区中并列；可从导航底部“设置”进入，也可直接刷新或收藏该地址。桌面端使用左侧分类导航和右侧内容区，移动端切换为分类选择器与单列布局。返回按钮优先通过浏览器历史恢复进入前的任意业务工作区及其内存状态；有未保存修改时会先确认。

设置按“常规”“目录与文件管理”“B 站下载”“剧集重命名”“视频截图与拼图”“图床上传”“种子与 qBittorrent”“发布信息与辅助”“日志与系统”分类。底部保存栏统一显示未保存数量、保存/取消状态和错误；每个分类可把当前草稿恢复为环境默认值，仍需主动保存。保存失败会保留草稿，未知键或任一非法值会拒绝整次保存，不会产生部分写入。截图与拼图分类保留原有 `collage_default_*` 兼容键，并新增独立截图宽度、截图输出子目录和拼图间距。

四类目录使用挂载根标识加相对路径保存，可通过同一 `FileBrowser` 目录选择器从八个 Docker 挂载根中选择已存在且可访问的目录；修改只影响后续操作，不会移动已有文件。B 站单条/合集命名模板和默认保存子目录会应用到新任务，已排队任务保留创建时的快照。剧集设置可保存默认季号、默认片源、自定义片源和命名预设；每种媒体类型至少保留一个默认预设，集数递增/递减不会因保存设置而自动开启。设置写入现有 SQLite `settings` 表，重启容器后仍然有效，接口只返回白名单配置，不返回 Cookie、Token 或密码。

后端通过 `bili_remote/settings_registry.py` 声明分组、分组标题、字段类型、默认值、选项、范围、校验器、生效范围和是否高级项；`GET /api/settings/schema` 返回该定义，前端 `settings-fields.js` 统一生成控件。新增可配置功能时应同时补定义、默认值、保存和读取逻辑及测试，不需要再写一套固定表单。运行时下载引擎与 CDN 参数保存后立即更新共享服务配置；若解析或下载正在进行，相关修改返回 `409` 并保持旧配置不变。日志保留期在下一轮维护任务生效，文件浏览分页和排序默认值在下次打开浏览器实例时生效。

按钮不可用与请求进行中是两种状态：普通禁用按钮使用稳定的 `not-allowed` 指针，只有真实请求期间才添加 `.is-busy` 和 `aria-busy="true"` 并使用进度指针。悬停不会触发请求、重试或提示，异步操作在成功、失败和取消路径都会清理忙碌状态。

### 手动批量重命名

在文件列表中勾选一个或多个文件，点击“重命名”打开工作台。工具会逐文件调用 `ffprobe` 读取分辨率、视频编码、音频编码和 HDR，并按 `pt-rename.sh` 的标签习惯输出 `H264`、`H265`、`1080p`、`2160p`、`AAC`、`DTS-HD`、`TrueHD` 等值。每个文件独立探测，不会把首文件参数套用到整批。ffprobe 不可用、探测失败或字段缺失时保持空值，可在预览表中逐文件手动填写，不会猜测或伪造结果。

集数默认直接采用逐文件识别结果，支持 `S01E02`、`S01 - 02`、`E02`、`EP02`、`第02集`、`第2话`、`2x03`，不会取文件名中的任意首个数字。多个格式给出冲突集数时显示“需确认”并留空，由用户手动选择。关闭集数修正时仍可逐文件填写集数；只有主动开启“启用集数修正”后，才会显示方向、起始集数、步长、作用范围和排序方式，并允许按顺序递增/递减。作用范围包括全部所选、仅已识别集数、仅未识别集数；排序支持自然排序、文件名升降序、修改时间升降序和当前选择顺序。

季号最终字段默认 `01`，可在公共参数或每个文件行中修改；文件名识别到的季号与集数会单独显示供确认，不会自动覆盖最终值。片源类型提供 `WEB-DL`、`WEBRip`、`BluRay`、`HDTV`、`DVD`，也允许自定义输入；自定义值会保存在浏览器本地，B 站下载来源不会被自动推断为某种片源。每个文件的分辨率、编码、HDR、片源、季号和集数均可独立修改，预览会实时重新生成新文件名。

模板变量与 `pt-rename.sh` 对齐：`{title}`、`{year}`、`{season}`、`{episode}`、`{resolution}`、`{source}`、`{hdr}`、`{video_codec}`、`{audio_codec}`、`{group}`、`{ext}`、`{original_name}`，季号和集数支持 `:02d`。工作台提供剧集和电影快捷模板；标题、年份、制作组和 HDR 留空时会自动移除对应字段及多余分隔符，仍可生成不完整但有效的文件名。片源和媒体参数如被模板引用则仍需填写；工具不会从文件名猜测这些信息。

确认后只对原目录中的文件执行 `rename`，不会移动、复制或删除文件。执行前会重新检查源文件状态、非法字符、批内重名、大小写冲突和目标占用；目标已存在时拒绝执行，不使用覆盖式改名；执行成功后刷新目录并展示实际改名结果。批内任何文件校验失败都会阻止整批提交，部分执行失败时保留现有自动回滚逻辑。

文件浏览与重命名旧接口保持兼容：`GET /api/files/roots`（可用 `scope=mounts` 获取挂载根）、`GET /api/files/browse`（支持 `sort`、`direction`、`focus`）、`POST /api/files/rename/analyze`、`POST /api/files/rename/preview` 和 `POST /api/files/rename/apply`。新增 `GET /api/files/meta`、`GET/PUT /api/files/text`、`GET /api/files/image`、`POST /api/files/rename`、`POST /api/files/operations/preview` 和 `POST /api/files/operations/apply`；批量操作通过 `/api/tasks?task_type=file_operation` 及现有任务状态、取消和日志接口轮询。`/api/workspace/*`、B 站登录/下载接口和数据库结构未被删除或改名。

## 数据与备份

- `DATA_ROOT_HOST`：SQLite 数据库、加密后的 Web/TV 登录信息、解析历史和日志。
- `DOWNLOAD_ROOT_HOST`：最终音视频文件。
- `TORRENTS_ROOT_HOST`、`IMAGES_ROOT_HOST`、`RELEASE_INFO_ROOT_HOST`：种子、截图和发布资料目录。
- `IMPORTS_ROOT_HOST`、`STAGING_ROOT_HOST`、`RELEASES_ROOT_HOST`：旧资源工作台后端仍使用的导入、暂存和发布目录。
- 建议备份整个数据目录；下载目录可按需处理。
- 数据库 schema 升级前会通过 SQLite backup API 写入 `data/backups/`，每个待应用版本各保留最近 5 份备份。
- 日志默认保留 30 天，可通过 `LOG_RETENTION_DAYS` 调整。
- Cookie 使用 `APP_SECRET` 派生密钥加密。更换 `APP_SECRET` 后需要重新扫码登录。

## 更新 yt-dlp

`.env` 中的 `YTDLP_VERSION` 仅用于界面显示。实际版本由 [requirements.txt](requirements.txt) 固定。B 站接口变化时：

1. 修改 `requirements.txt` 和 `.env.example` 中的 yt-dlp 版本。
2. 同步修改 `docker-compose.yml` 的 `YTDLP_VERSION` 显示值。
3. 重新构建并启动：

   ```bash
   docker compose up -d --build
   ```

不要在宿主机和容器内混用不同版本。

## 生成分享包

使用 Python 3.11 或更高版本生成源码分享包：

```bash
python3 scripts/package_release.py
```

压缩包会写入 `dist/`，文件名包含项目版本和时间戳；同一目录下会同时生成同名 `.sha256` 校验文件。脚本只打包源码、测试、文档和部署文件，不会包含 `.env`、SQLite 数据库、日志、缓存、下载内容或其他运行数据。分享前仍应确认压缩包清单，不要将私有修改或凭据提交到公开渠道。

可通过 `--output-dir` 指定其他输出目录：

```bash
python3 scripts/package_release.py --output-dir /tmp/bili-remote-release
```

接收方可使用以下命令校验压缩包：

```bash
cd dist
sha256sum -c bili-remote-*.zip.sha256
```

## 参考项目与许可证核查

以下项目用于核对接口行为和交互流程，应用页面及服务按现有 Bili Remote 架构实现。作为运行依赖分发的 BDInfoCLI 单独注明：

- [Upload-Assistant](https://github.com/Audionut/Upload-Assistant)：核查提交 `c3ec850`，仓库采用 UAPL v1.0，源码文件带版权头。参考 PT 发布辅助流程、媒体信息与截图/图床上传的数据组织方式。
- [GG-BOT Upload Assistant](https://github.com/neoxcrazy/gg-bot-upload-assistant)：核查提交 `ff39f50`，未发现仓库级 LICENSE 文件。仅参考图床适配、批量截图和种子流程的交互边界，不采用其代码。
- [minfo](https://github.com/mirrorb/minfo)：核查提交 `a8b66c9`，采用 MIT 许可证。参考媒体探测、分段随机采样和缩略图布局思路。
- [qBittorrent](https://github.com/qbittorrent/qBittorrent)：核查提交 `a87f8f9`。主体为 GPLv2+，WebUI scripts 为 MIT；本项目只调用其公开 Web API 并参考任务状态展示，不链接或分发 qBittorrent 源码。
- `pt-slt.sh`：已读取用户本次提供的附件，按黑色时间栏、白字、阴影、白边及信息栏布局校准现有 Pillow 样式层。
- [BDInfoCLI](https://github.com/tetrahydroc/BDInfoCLI/tree/764f3a1d591e492ad13aa884e770f9b447bdde0b)：镜像从固定提交构建可执行工具，许可证保存在镜像 `/usr/share/licenses/bdinfo/`。

引用这些项目不改变 Bili Remote 自身的依赖和许可证；部署时仍需遵守各外部服务及参考项目各自的许可条款。

## 安全说明

- Nginx 只应把服务代理到本机回环地址，不必向公网直接开放 `8080`。
- 应用 Cookie 为 HttpOnly、SameSite=Lax；所有写操作要求 CSRF 令牌。生产环境必须启用 HTTPS。
- B 站 Web Cookie 与 TV token 都不会返回给浏览器，也不会出现在任务日志中。任务期间生成的临时 Cookie 文件权限为 `0600`，任务结束即删除。
- 不要分享 `.env`、数据目录或扫码登录后的账户会话。
- 仅下载自己有权访问的内容，并遵守 B 站服务条款及著作权要求。

## 本机开发与测试

Python 3.12 环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

Linux 或 macOS 下设置必要环境变量后运行：

```bash
export APP_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export ADMIN_PASSWORD="development-password"
export DATA_DIR="./data"
export DOWNLOAD_ROOT_CONTAINER="./downloads"
export IMPORTS_ROOT="./imports"
export STAGING_ROOT="./staging"
export RELEASES_ROOT="./releases"
export TORRENTS_ROOT="./torrents"
export IMAGES_ROOT="./images"
export RELEASE_INFO_ROOT="./release-info"
export SECURE_COOKIES=false
uvicorn bili_remote.main:app --host 127.0.0.1 --port 8080
```

本机开发可直接安装 `aria2` 以启用多连接下载；未安装时程序会自动使用 yt-dlp 原生下载。

运行测试：

```bash
pytest
```
