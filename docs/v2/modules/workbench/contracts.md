# 前端契约样例与失败语义

依据 `docs/v2/api-contract.md` 和 `data-model.md`；本文件不是新增 API 规范。样例为合成测试引用，不表示服务已上线或改名已执行。完整可执行替身见 `tests/v2/workbench/test_browser.py` 的 `context()` / `v2_fixture()`；替身仅存在测试目录。

## 一次识别，多方复用

FileBrowser 交接（真实 fingerprint 来自 browse，不在前端计算）：

```json
{"root_key":"downloads","relative_path":"Media/episode01.mp4","kind":"file","fingerprint":"synthetic-fingerprint-for-contract-test"}
```

识别请求：

```json
{"resources":[{"root_key":"downloads","relative_path":"Media/episode01.mp4","kind":"file","fingerprint":"synthetic-fingerprint-for-contract-test"}],"origins":[],"selection":null,"context":null,"manual_overrides":{}}
```

整季选择传 `kind=directory`，不由前端扫描/生成文件列表。后续请求复用响应的同一 `context_id/revision` 和 `effective_rules`；修改规则仅改变规则及上下文修订，不生成文件名。

- 主标题：`POST /api/v2/release-title/render`，`{context,rule}`。
- 副标题：`POST /api/v2/subtitles/render`，`{context,rule}`。
- 命名预览：`POST /api/v2/naming/preview`，`{context,overrides:{file_values:{},episode_overrides:{},manual_names:{}},rules}`。
- 合成：`POST /api/v2/publishing/preview`，传 context、三个当前编辑字符串、真实 ArtifactRef/null、links 及格式；不传 project_id/session_id。

确认改名仅传已预览 token：

```json
{"preview_token":"synthetic-session-bound-token","expected_revision":3,"confirm":true,"idempotency_key":"synthetic-request-uuid"}
```

422/409 不进入成功状态。命名预览过期、context revision 改变、规则输入改变或资源切换后旧 token 不可点击。任务提交失败时保留幂等键，重试同一执行不会由前端再生成新键。

## 既有真实工具

截图（不自动上传）：

```json
{"sources":[{"root_key":"downloads","relative_path":"Media/episode01.mp4"}],"total":4,"columns":2,"output_mode":"collage","content_type":"custom","skip_start_seconds":0,"skip_end_seconds":0,"auto_upload":false}
```

`POST /api/files/screenshots` 返回 task 和截图工具自身的 session_id。它只标识真实截图产物，不是发布项目/发布草稿。下载使用 `/api/files/image?root_key=images&path=...&download=true`。

`POST /api/uploads/jobs` 仅传产物路径 items；读取任务中成功上传的真实 URL，不拼造图床链接。

本地制种：

```json
{"source_root_key":"downloads","source_relative_path":"Media","private":true,"overwrite":false}
```

`POST /api/torrents/jobs` 返回的真实 record 通过 `/api/torrents/{id}/file` 下载。另一次明确点击才调用 `/api/qbittorrent/torrents`，只传 `torrent_build_id`。该接口是添加已有种子，**不是 qBittorrent 制种**。

`POST /api/release-info/analyze` 仅传 root_key、relative_path、format=text，使用 reports 中的真实 MediaInfo/BDInfo；不调用保存/创建报告接口，不凭空分配 report ArtifactRef。

## 错误与竞态

- 所有写方法含 `X-CSRF-Token`、同源会话 Cookie，所有请求 no-store。
- 409 示例：`{"detail":{"code":"stale_resource","message":"源文件已变化，请重新预览","fields":[]}}`，原样显示错误。
- V2 404：明确提示能力尚未接入；不填示例资料，不跳回旧草稿流程。
- 401：提示登录失效；403：权限/CSRF 错误。两者均不降级为无任务或成功。
- 自动重取保护人工字段；生成和组合分别检查资源 epoch、请求序号、context revision 和编辑版本。
- 编辑后即刻禁用完整内容复制，直到最新组合返回。缺项 ready=false 允许复制真实预览，但不标作就绪。
- 任务身份使用 `kind:id`；未知 progress 使用不确定进度条；终态完成通知只发一次。
