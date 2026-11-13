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
