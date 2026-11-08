# 最终用户流程与契约样例

## 从文件到发布

1. 文件管理勾选视频或整季目录，点击进入发种工作台。共享适配器重读权限、指纹并生成 ResourceContext；无需重复选资源。
2. 工作台显示实测媒体参数、身份状态和命名预览。中文/英文/原始名称可搜索，或输入 `tmdb:123`、`tmdb:movie:123`、`tmdb:tv:123`；候选包含可用的年份、原名、来源和海报。
3. 选择影视候选或手填名称、季集、片源、制作组。片源/制作组属于规则，不当作探测事实。自动刷新命名预览，不改真实文件。
4. 需要改名时检查全部目标后明确确认；原有令牌、指纹、冲突、权限、撤销流程保持。复杂逐集调整、字幕关联、系列/季集记忆在命名详情中操作。
5. 直接编辑主标题、副标题和简介，生成请求不覆盖人工字段。简介支持常用安全 BBCode；海报与真实工具产物参与组合。
6. 按需打开缩略图、报告或制种工具，使用当前资源后台执行。完成后显示真实图片、报告、种子下载或可选外部操作。
7. 打开预览与复制，切换 BBCode/HTML/Markdown、效果/源码，检查缺项后复制。无项目创建、草稿保存或自动发种操作。

切换资源会覆盖人工内容时明确确认；取消保留原资源和文本。普通工具切换、预览和导航不重新挂载编辑器。刷新不恢复发布文本；后台任务和真实产物仍可从任务中心读取。

## 下载联动

输入链接 → 解析 → 选集与画质 → 可选高级下载参数 → 加入原下载队列。修改链接或线路后，先前解析结果失效，迟到响应不能恢复旧内容。下载完成后“打开文件管理”定位真实产物所在目录，“进入工作台”传入实际 ResourceRef 和 DownloadOrigin。未知来源元数据保留 null，不从标题猜成来源事实。B站真实网络下载需要独立凭据，离线测试不算在线验收。

## 规则保存

设置中分别编辑 V2 全局命名和副标题规则，用“保存全局规则”提交到既有 NamingMemory API。修改时只保存规则字段；版本冲突保留输入并提示重新读取。系列/季集规则仍由命名详情保存，优先级为当前人工调整 > 季集 > 系列 > 全局 > 内置。

全局设置、规则记忆、当前资源临时值各有独立范围。旧版预设只作用于对应兼容功能。未保存规则在切换分类时保留，刷新前有离开提醒。

## 最小新增/扩展契约

以下均为合成示例，生产数据不得用于验收。

```json
{"scope":"global","expected_revision":0,"naming_rule":{"source":{"value":"WEB-DL","source":"manual","manual":true,"confirmed":true},"group":{"value":"HDVWEB","source":"manual","manual":true,"confirmed":true}}}
```

发送至 `PUT /api/v2/naming/memory`；字段仍使用现有白名单，成功 revision 增长，冲突返回 409。副标题对应 `subtitle_rule`；没有文本保存字段。

```json
{"sources":[{"root_key":"downloads","relative_path":"synthetic.mp4"}],"output_mode":"single","total":2,"start_percent":25,"end_percent":75,"sampling_mode":"uniform","auto_upload":false}
```

发送至原 `POST /api/files/screenshots`。新增可选 start_percent/end_percent 必须同时满足 0 ≤ start < end ≤ 100；百分比不能和指定时间点同时使用。后台对每个视频独立使用实测时长换算；不提供百分比时保持旧秒数语义。权限、路径和原任务执行器不变。

`GET /api/v2/tasks` 仅扩展可空 title 字段，使下载名称可读。原任务 kind/id、状态、允许操作及 artifact_refs 语义不变。完成后产物链接来自服务返回，不由客户端猜测。

本地制种继续使用原 `POST /api/torrents/jobs`，按需传 `piece_length: "auto" | "256k" | "1m" | "4m" | "16m"`；overwrite 固定 false，qBittorrent 导入仍需人工触发。
