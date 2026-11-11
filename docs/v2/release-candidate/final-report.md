# Bili Remote 2.0 发布候选最终报告

日期：2026-11-11
源码候选提交：`7659e48c251fb9546b21911cd9832844ae55231e`
分支：`codex/final-ui-v2`
工作区：`/opt/bili-remote-wt-final-ui`

## 结论

当前源码在**本地与离线可独立验证范围内**满足进入隔离预发布人工验收的条件。Python 3.11 和 Python 3.12 全量均为 439 passed，真实 FFprobe、MediaInfo、FFmpeg、命名执行/撤销、torrent 生成和完整 WebUI 链路均通过。

这还不是完整在线验收。B站 Web/TV、TMDB、Bangumi、PT-Gen、Minfo、图床上传、qBittorrent 和完整 BDInfo 缺少合法测试配置或样本，必须保持“外部阻塞”。因此当前建议是：**批准进入隔离预发布验收，暂不进入生产部署。**

## 候选范围

本轮从已验收的五页正式设计继续，没有重新设计 WebUI。后续主要工作集中在：

- 真实媒体治理和智能命名核查。
- 完整离线发布流程执行。
- 外部服务缺口盘点。
- torrent 源资源失效保护。
- 双 Python 全量回归与发布材料整理。

关键提交：

```text
f269c47 fix: select naming template from filename evidence
9b3ee6b fix(naming): preserve explicit source and group provenance
1ae9e78 test(media): validate real release media matrix
7659e48 fix(torrent): reject downloads after source rename
```

最终文档提交紧随源码候选，不改变业务代码。

## 实际通过

- 五页在 1440×900、1280×800、1024×768、768×1024、390×844，以及浅色/深色状态完成真实 Chromium 截图。
- 下载完成真实视频进入文件管理，再以同一资源进入工作台。
- 人工标题、副标题、简介、资源切换和异步响应保护通过。
- 真实 H.264/AAC MP4 与 HEVC 多音轨/多字幕 MKV 探测通过。
- `Motion.Study.S01E01.mp4` 的目标名称包含季集；季集规则来源可解释。
- 整季父子选择去重、逐集集数覆盖、外挂字幕、冲突拒绝、零写入预览、真实改名和撤销通过。
- FFmpeg 缩略图/拼图、MediaInfo 报告、本地私有 torrent 和完整发布预览通过。
- 人工简介在预览中只出现一次，一键复制和撤销后的编辑保护通过。
- 旧版兼容、API 安全、路径校验、任务状态和既有迁移回归通过。
- 测试实际结果：Python 3.11 `439 passed`，Python 3.12 `439 passed`，均无 failure/error/skip。
- 前端构建一致性和 `git diff --check` 通过。

## 仅离线通过

- B站解析、选集、画质、任务状态、失败重试与完成后交接。
- TMDB/Bangumi 候选选择和工作台资源上下文。
- 图床任务契约和失败重试。
- qBittorrent API 形态与错误提示。
- 下载失败、取消、重试和部分批次成功状态。

离线替身均在测试中明确标识；真实队列、SQLite、文件系统、FFmpeg、MediaInfo 和 torrent 服务没有被替身替代。

## 外部配置阻塞

| 项目 | 阻塞原因 | 不能宣称 |
| --- | --- | --- |
| B站 Web/TV 登录与真实下载 | 无授权测试账号/资源 | 在线登录和真实线路通过 |
| TMDB | 无合法测试凭据 | 真实检索和图片通过 |
| Bangumi | 未启用合法在线配置 | 在线候选通过 |
| PT-Gen | 无合法端点/API Key | 在线简介补充通过 |
| Minfo | 无合法测试配置 | 在线接口通过 |
| 图床 | 无独立测试配置 | 公网图片链接通过 |
| qBittorrent | 无独立测试实例 | 真实导入做种通过 |
| BDInfo | 无合法完整蓝光样本 | 蓝光报告通过 |

## 数据安全

- 未读取或复制生产 `.env`、Cookie、API Key、数据库、私有备份或媒体。
- 未修改生产数据库、真实媒体或任务。
- 无新增数据库 schema 迁移需求。
- 源码包脚本排除 `.env.dev`、真实环境文件、`.dev`、数据库、日志、媒体、缓存和运行产物；生成后需执行 ZIP 解包测试与文件清单审计。
- 发现并修复了 torrent 源目录改名后仍可下载旧产物的问题。现在下载前验证源存在、布局、路径、文件数、总大小和 info hash，不匹配返回 422。
- 已知限制：若文件路径、数量和总大小不变，仅内容变化，当前下载前校验不会重新哈希全部媒体负载；内容级检测仍需显式重新制种或后续增强。

## 交付材料

- `functional-audit.md`
- `media-validation.md`
- `naming-validation.md`
- `external-integration.md`
- `e2e-acceptance.md`
- `regression-results.md`
- `deployment-plan.md`
- `rollback-plan.md`
- `final-report.md`

截图证据位于 `/opt/bili-remote-wt-final-ui/.dev/final-product-visual/`，包括五页 50 张主题/视口截图、10 张预览截图及真实工具流程截图。最终汇总图另存于 `.dev/contact-sheet-desktop.png` 和 `.dev/contact-sheet-mobile.png`。

源码包由 `scripts/package_release.py` 生成，最终路径、字节数、文件数、SHA-256、`unzip -t` 和 `zipinfo -1` 安全审计结果随交付回复一并记录。

## 发布候选判定

| 检查问题 | 结论 |
| --- | --- |
| 真实本地功能是否通过 | 是，且覆盖真实媒体和工具产物 |
| 是否只有离线证据 | 外部服务是；本地文件、媒体、命名、报告和 torrent 有真实执行证据 |
| 是否仍有外部配置阻塞 | 是，不能省略 |
| 是否发现数据安全问题 | 发现旧 torrent 源变化问题并已修复；详见已知限制 |
| 是否需要修复额外后端接口 | 本轮已做最小 torrent 安全修复，没有已知未处置本地阻塞 |
| 是否需要数据库迁移 | 否 |
| 是否具备本地发布候选条件 | 是，可进入隔离预发布人工验收 |
| 是否建议立即生产部署 | 否，外部在线服务和人工验收尚未完成 |

## 操作声明

本轮没有部署生产、重启生产服务、修改生产数据库或媒体、推送远程、合并 `v2-integration`。受保护工作区 `/opt/bili-remote` 的既有未提交修改未被动用。
