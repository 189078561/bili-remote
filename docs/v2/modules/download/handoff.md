# 下载模块交付说明

分支：`codex/download-v2`。本交付只新增下载模块独占目录文件；没有修改旧下载引擎、B站登录、公共 API 注册、主页面或全局脚本。

## 模块输出

- `bili_remote/templates/v2/download/workbench.html`：独立页面片段，涵盖 Web/TV 状态展示入口、链接解析、剧集批量选择、格式与保存设置、现有队列、通知、日志、失败重试和历史。
- `bili_remote/static/v2/download/workbench.js` 与 `.css`：`window.BiliDownloadV2.mount(root, options)` 挂载接口。通过既有 `/api/bili/status`、`/api/resolve`、`/api/episodes/formats*`、`/api/jobs*`、`/api/history*` 工作；POST 自动附现有 CSRF meta token。没有新建任务 API、任务存储或下载引擎。
- `bili_remote/v2/download/completion.py`：`DownloadCompletionAdapter.on_completed(job_id, origin_refs)` 校验 `ResourceRef` 路径和指纹，保留未知身份字段为 null，调用 `NamingService.analyze(resources, origins, context=None, rules=None)`。Naming 失败只返回不含异常细节的 warning，不反改下载终态；同一进程内相同 job_id 共用一个分析任务。
- `tests/v2/download/`：合成来源适配器测试和独立片段浏览器测试。

## 整合接入

整合会话需要在主页面导航/视图中加载该片段、模块 CSS/JS，并调用 `BiliDownloadV2.mount`。配置项建议传入 `singleTemplate`、`playlistTemplate`、`pollInterval`，以及既有扫码登录入口的 `onLogin("web"|"tv")`、共享文件浏览器入口的 `onOpenFile(job)`、保存目录选择器 `onChooseDirectory(currentPath)` 和完成提示的 `onCompleted(job)`。模板设置必须来自已有下载设置，不在此模块生成新默认命名算法。

下载完成 hook 由既有任务完成路径调用 `adapter.on_completed(job_id, origin_refs)`。整合会话需先从来源关联仓储和安全文件服务取得服务端生成的 `ResourceRef`（root_key、相对路径、kind、fingerprint），不可从任务 URL/文件名推断 bvid、剧集 ID 或 CID。`origin_refs` 的每项形状遵循 `DownloadOrigin`：`bvid/season_id/ep_id/cid/original_title/episode_index/api_mode/original_resource`。完成回调不改文件；RecognitionProposal.context 交给 NamingService/统一工作台提供预览。持久来源关联、跨重启完成通知幂等和全局 TaskCenter 投影仍由整合会话实现。

本分支没有访问或改写 `web.py`、`index.html`、`app.js`、`downloader.py`、`bili.py`。因此新片段尚未挂入产品导航，旧页面在本 worktree 内仍是实际入口。

## 契约样例

```json
{
  "job_id": 71,
  "bvid": "BVsynthetic123",
  "season_id": "42",
  "ep_id": "8",
  "cid": "9001",
  "original_title": "合成剧集标题",
  "episode_index": 3,
  "api_mode": "tv",
  "original_resource": {
    "root_key": "downloads",
    "relative_path": "Synthetic/episode.mkv",
    "kind": "file",
    "fingerprint": "server-generated-fingerprint"
  }
}
```

没有来源事实时字段为 null；上例仅为合成契约样例，不表示真实 B 站下载或真实文件能力已验证。

## 验证边界

验证结果：`python3 scripts/dev_test.py download` **40 passed**；`python3 scripts/dev_test.py all` **217 passed, 0 failed, 1 existing Starlette/httpx deprecation warning**；`node --check bili_remote/static/v2/download/workbench.js` 与 `git diff --check` 通过。新增浏览器测试使用本地合成响应检查控件与请求映射，不运行或伪报下载成功。隔离回归只证明当前 fake_ytdlp/HTTP 测试覆盖的逻辑；未使用真实 B站 Web/TV 登录、Cookie、网络资源或 aria2c 下载。浏览器当前没有真实登录会话或真实下载完成联动验收。Python 3.12 全量回归、主页面挂载、真实来源仓储、TaskCenter 接入和 NamingService 整合仍待整合阶段验收。

## 下载速度优化评估（2026-11-14）

本阶段只对可确认项直接实施；需要线上吞吐基准才能证明的改动记录为待部署后验证，不硬编码未经确认的 CDN。

已确认可用（无需改动）：

- aria2 多连接下载：默认 16 连接、`--split=16`、`--min-split-size=20M`、`--piece-length=5M`，另配 `--max-tries=5 --retry-wait=1 --connect-timeout=15 --timeout=30`。
- 断点续传：aria2 支持 `--continue`，`aria2_resume_retries` 默认 2 次。
- CDN 探测排序：`tv_cdn_probe_enabled` 默认开启，8 路探测、512 KiB 采样，对 Web/TV 候选 CDN 测速后排序。
- 解析列表折叠：已提交并测试，收起保留已选集数、画质、编码与当前解析资源，展开不重新解析。

未直接实施（需线上验证或超出本次范围）：

- BBDown 复用：仓库内无 BBDown 实现，接入需新增可执行文件、Docker 构建与下载器分支，本次不引入。
- 更细的并发/分片参数与 CDN 白名单：现有默认值已较激进，继续调优需真实 B 站线路吞吐与稳定性对比，交给用户部署后实际验证。

## 格子选集与历史折叠（2026-11-18）

下载页按解析结果显示正片、花絮和预告。仅渲染当前分类的所选范围，每页最多 50 项。勾选和单项画质按条目 ID 关联。选集浏览时切换分类或排序不会重算命名或画质。

复用记录仍使用 `GET /api/history/{id}`，直接读取 `source_url`、`api_mode`、`episodes`、`default_episode_id` 和 `formats`。例如历史仅包含 `episodes: [{id: "51", group: "正片", group_index: 51}]` 时只显示正片入口。记录未保存首次解析的命名事实，提交时使用已有识别流程。下载历史和进行中队列共用原任务卡和轮询。

`python3 scripts/dev_test.py download` 通过 57 项。测试使用本地数据库和合成 B 站响应。共享集成测试仍有旧选集列表和任务卡位置的断言。整合时将全量条目数断言改为当前页数量，按集数格聚焦单项画质，并展开下载历史后再操作终态任务。

在临时检出按上述新交互调整三项集成用例后，单集画质、部分提交重试和终态任务重试均通过。共享集成用例由整合会话更新。

「全选／取消全选」与正序、倒序同排。点击只更新当前分类所显示范围的条目 ID。例如 201–220 范围会切换 20 项。合成测试覆盖跨页及跨分类保留勾选，下载模块 57 项通过。

## B 站传输优化（2026-11-18）

下载仍通过现有 `/api/jobs`、队列、yt-dlp 和 aria2 执行。新默认值为 8 路连接，最小拆分和 piece 均为 4 MiB。显式配置的 1–16 路及分片大小继续生效。已持久化的设置和环境覆盖值优先。

aria2 在标准输出和标准错误中都可能报告节点错误。单次 567 在原节点降低并发续传，连续两次节点错误或同一尝试出现 567 与 403 时可使用同轨备用地址。单独识别到 429 时，等待后在原节点降低并发。没有备用地址且返回 403 或 410 时，可以通过原线路重新获取一次同媒体、同格式和编码的播放地址。跨来源重新开始未完成轨道；同节点的正常续传继续使用 aria2 控制文件。Web 下载显式指定轨道时仅为选定轨道排序，有单个候选的 Web 轨道跳过测速。

涉及文件为 `bili_remote/downloader.py`、`bili_remote/config.py`、`bili_remote/settings_registry.py`、`.env.example`、`docker-compose.yml`、`tests/fake_ytdlp.py`、`tests/test_downloader.py`、README 和本说明。下载模块 64 项、下载 API 相关 6 项和设置相关 2 项通过。

在独立容器的回环 HTTP Range 服务中，每连接上限 4 MiB/s，正反顺序各测试一次。26 MiB 文件使用 8 路时，原 20/5 MiB 参数两轮均耗时 6.59 秒，新 4/4 MiB 参数均耗时 1.07 秒。128 MiB 文件分别为 8.89 秒和 4.18 秒；同文件 12 路、4/4 MiB 用时 3.17 秒。每次均校验 SHA-256。另用实际 yt-dlp、aria2 和本模块的下载入口验证连续 567、过期 403、已写入部分数据后切源三个场景，均在多连接引擎内完成并通过文件校验。测试使用合成内容，真实 B 站吞吐待线上验证。

## 海外 VPS 的 CDN 对照与定长分片（2026-11-18）

在与生产应用共用 Docker 网络的隔离容器中，使用公开 Web 线路测试《凡人修仙传》。未使用生产登录态。相同视频轨的 Akamai 与 COS 备用地址都能返回有效的 206 字节区间。短测速结果与实际吞吐随条目和请求方式变化。部分地址对普通起始 GET 返回 403 或 567，原 aria2 会在尚未收到媒体数据时失败并降档。

多连接 DASH 下载现在保留节点探测实际验证过的总字节数，按完整 URL 分开记录。只有服务端返回准确的 Content-Range 且采样字节数完整才启用。若轨道已有明确的 filesize，两个长度必须相等。选择节点时优先考虑支持分片的节点。yt-dlp 调用原 aria2 时，以单 URL Metalink 传入轨道的真实长度和原输出名，同一次下载只用一个源。未知长度、特殊文件名、非独立 DASH 轨道以及关闭节点探测等情况继续使用既有下载流程。任务 API、并发上限、音视频合并、取消及失败回退沿用现有实现。

在相同正式镜像、相同 VPS 出口、相同公开签名地址下，新旧下载器接收已解析轨道，包含实际线路排序、下载进程、重试和收尾。两集按相反版本顺序测试：

| 视频轨 | 大小 | 9013b93 | 新版 | 每组 SHA-256 前 20 位（两版本一致） |
| --- | ---: | ---: | ---: | --- |
| 第 1 集 AVC 480p | 92.71 MiB | 24.44 秒 | 6.68 秒 | 3e4665f270a303596261 |
| 第 2 集 AVC 480p | 67.18 MiB | 27.32 秒 | 6.19 秒 | 7f667ac3f602a6f6ae70 |

另从第 2 集的官方番剧 URL 正常解析、下载默认音视频并合并：旧版 26.55 秒，新版两次分别为 14.91 秒和 21.79 秒，成品均为 54.41 MiB，ffprobe 确认含视频及音频流。旧版回退到原生下载，新版通过首次 aria2 多连接尝试完成。普通投稿接口在此出口返回 HTTP 412，未取得可验证的投稿 CDN 样本。上述为当时的公开番剧样本。

改动涉及 downloader.py、新增 v2/download/aria2c_range.py 和对应下载测试。下载模块 72 项、下载 API 针对性检查 6 项通过。

## 单 CDN 的 2160p 视频轨

yt-dlp 最终选定 DASH 轨道时，如测速阶段未验证其长度，下载器仅对该轨道请求一个字节。只有响应区间、长度和实际字节均匹配才启动定长分片。遇到连续的节点错误时，可更新一次同轨播放地址。若在原生回退前才取得可用备用地址，优先用 aria2 实际尝试，额外切源最多两次。

同一海外服务器、同一个 6,917,295,620 字节的视频轨、同一 CDN，按分片／原生／原生／分片顺序测得 22.25／3.51／3.64／22.42 MiB/s。四轮独立从头下载，包含启动时间，分片两轮实际连接数均为 8。这是短时视频轨实测。隔离容器另用合成内容验证 403 地址更新、连续 567 后切源、部分传输后切源及单节点连续 567 后更新，四次均经 aria2 完成并核对 SHA-256。

## 下载历史与解析记录

下载历史的终态记录使用现有的 `DELETE /api/jobs/{id}`，清理任务记录及其日志，保留实际下载文件。解析记录复用 `GET /api/history`、`GET /api/history/{id}` 与 `DELETE /api/history/{id}`。列表按线路和资源归并，番剧使用季链接及已保存的正片/选集 ID 辨认，保留对最新解析快照的复用。删除归并行会移除该行对应的解析快照。解析时间以前端的 Asia/Shanghai 时区显示。无需 API 装配或数据库迁移。

聚焦浏览器测试用临时媒体文件及合成番剧覆盖删除确认、下载文件保留、分享链接重复、 Web/TV 分离、同名不同季、季记录之后新增的剧集、最新快照复用与东八区跨日。下载模块 79 项与已有删除及复用 API 测试 3 项通过。

## 复用解析与非正片命名

复用解析记录时，从现有选集快照恢复明确、唯一的正片集号，给 `POST /api/download/naming/preview` 使用。新选中的剧集成为编号编辑对象。花絮、预告和 PV 的命名依据带可选的 `extra_title`，例如 `兢慎篇.预热PV.mp4`。提交的命名计划也使用该字段，默认无编号，并保留平台 ID 用于同名冲突。人工编号继续优先。无数据库迁移或新路由。

隔离测试覆盖缓存的《盗妖行》正片与 PV 同时入选、预览、手动集号、批量命名计划与同名后缀。下载模块 80 项通过。

旧解析快照可能只有「1 来世，你可以找我报仇」这种合并标题，没有 `display_episode_number`。复用时只对缺少字段的正片提取标题开头的集号，并继续检查同一季已识别的集号是否唯一。测试同时覆盖新旧快照、第 200 集与篇章数字并存，以及缺少可靠编号时保留平台 ID。下载模块 81 项通过。

## 含中文标点的轨道临时文件

四集批量任务串行运行，首集的 1080p 视频和音轨都有已验证的 Range，但临时文件名中的中文逗号触发旧 Metalink 文件名正则，导致 aria2 发出普通请求，连续 403／567 后回退。修复允许可原样保存的可打印中文标点，并继续拒绝路径分隔符、编码路径、控制字符和实测会被 Metalink 改写的字符。发生回退时记录具体原因。

使用镜像中的 aria2 在隔离回环服务验证中文标点可原样保存。对同一台 VPS、同一条 1080p 轨道及同一 CDN 短测，按新版／旧版／新版测得 23.19／3.32／27.86 MiB/s。两轮新版实际建立 8 个连接，旧版收到 567 后走原生。下载模块 81 项通过。
