# Naming 开发任务状态与验收证据

日期：2026-11-04（Asia/Shanghai）。当前分支 codex/naming-v2。**模块可独立测试；整站入口、持久化整合和两个上游认证实测尚未完成，不报告完整目标成功。**

## 逐项对照用户要求

| # | 要求 | 当前证据与状态 |
| --- | --- | --- |
| 1 | 阅读架构、模型、API、归属、验收与 naming 任务 | 已读 AGENTS、docs/v2 全部顶层文档及五模块任务；后续开发授权明确，不越过白名单 |
| 2 | Resolver + TMDB/Bangumi/PT-Gen，验证实际能力 | 三适配器和降级已实现，HTTP mock 覆盖结构/错误；Bangumi 真实只读核验通过。TMDB 无开发 token、PT-Gen 无测试 endpoint/凭据，真实认证能力未完成，见 provider-capabilities.md |
| 3 | 中文/英文/原始/日文/繁体及自定义名 | 多语言/别名 TitleCandidate、NFKC 去重、人工选择锁定、模板覆盖和独立 UI；tests/v2/metadata/test_resolver.py、naming/test_templates.py |
| 4 | 真实 ffprobe 参数 | 复用旧 subprocess，逐文件探测分辨率/编码/精确帧率/位深/HDR；tmp_path 合成两种尺寸视频、失败样本和人工证据保护，test_probe.py。没有伪造 x264/x265 或失败参数 |
| 5 | 电影/剧集/动画模板 | 正式大写和 :00，默认 WEB-DL/HDVWEB/01；缺项告警、可选技术标签、整季公共值、模板安全，test_templates.py |
| 6 | B站/TMDB 季集映射及人工修正 | 来源仓储校验与指纹绑定、跨季/系列键校验、文件名语法、真实所选季计数、递增/递减/开始/offset/逐项，test_episodes.py 和 test_service_api.py。来源仓储公共装配待接 |
| 7 | 全局/影视系列/季集三级规则记忆 | 白名单、三层与人工覆盖、身份不确定隔离、乐观锁透传、规则 API 已实现；test_memory.py/API 测试。**跨重启 SQLite 持久化依赖公共仓储/迁移，未实现整合，不以 fake 冒充** |
| 8 | 批量预览/冲突/安全执行/撤销 | session/revision/TTL/token、稳定幂等 batch、两阶段无覆盖、目标交换、失败恢复、进程中断窗口恢复、取消、撤销漂移与新占用保留，test_execution.py/test_service_api.py。实际执行器/审计注册待整合 |
| 9 | 字幕和关联文件同步 | 精确 stem + 最长视频归属，chs/cht/eng/forced/SDH 保留，外音轨显式确认，.sub/.idx 可同步；冲突走同一预检与撤销；服务测试包含实际文件重命名/还原 |
| 10 | API/模型/测试 | 独立 APIRouter、统一错误/认证/CSRF/no-store、最小任务 handlers、字典契约样例和模块测试已实现；**共享正式模型、完整 OpenAPI 与主应用注册仍属于整合会话，不复制公共模型绕过限制** |

## 验证结果

- 初轮旧命名兼容及新安全执行：27 passed。
- 最新模块测试 `python3 scripts/dev_test.py naming`：100 passed，0 failed，0 skipped，1 warning；含实际 Chromium 交互、390/768/1440 宽度、预览无写入、明确确认、后台执行与撤销、localStorage 空。
- 最终全量回归 `python3 scripts/dev_test.py all`：**289 passed，1 failed，0 skipped，1 warning，113.47 秒**。失败为旧 `tests/test_screenshots_api.py::test_screenshot_api_creates_manageable_persistent_session`：image_succeeded=2 而预期 3。此前一轮为 289 passed（当时模块用例少一项）；不以重试偶然变绿代替修复。
- 旧截图问题已确定性定位：临时合成 3 秒、10fps、MPEG-4 视频，直接调用旧 ScreenshotService._extract_frame_with_retries：target=2.95 固定失败，target=2.90 成功。原因是采样允许接近 duration-0.05，而最后帧为 2.9；现有负偏移最大仅 0.036 秒，无法到达最后帧。孤立重跑原 API 用例 1 passed，说明随机边界会间歇触发，不代表修复。公共截图文件禁止本模块修改，修复建议见 handoff.md。
- 浏览器和媒体样本在临时目录生成；HTTP 默认 mock，实际接口核验另行显式执行。没有请求真实 B站下载、图床、qBittorrent。
- 唯一既有警告：Starlette TestClient 使用 httpx 的弃用提示，未为消警告升级环境。
- 解释器为共享依赖 venv 的 Python 3.11.2；项目要求 >=3.12，尚未得到 Python 3.12 正式回归证据。未升级宿主 Python/Node/Nginx/Docker，未读取生产配置，未部署、启动/重启生产服务。

## 修改文件清单

仅以下白名单内新增文件；没有修改既有公共文件或其他模块。

- `bili_remote/v2/metadata/`：__init__.py、providers.py、resolver.py、verify.py。
- `bili_remote/v2/naming/`：__init__.py、errors.py、filesystem.py、execution.py、service.py、router.py、memory.py、probe.py、episodes.py、templates.py、recognition.py、tasks.py。
- `bili_remote/static/v2/naming/`：naming.js、naming.css。
- `bili_remote/templates/v2/naming/`：panel.html。
- `tests/v2/metadata/`：test_resolver.py。
- `tests/v2/naming/`：test_execution.py、test_templates.py、test_episodes.py、test_probe.py、test_memory.py、test_service_api.py、test_browser.py。
- `docs/v2/modules/naming/`：handoff.md、task-status.md、provider-capabilities.md、provider-schemas.json、contract-examples.json。

## 剩余真实阻塞

1. TMDB 独立开发凭据与 PT-Gen 测试 endpoint/开发凭据尚未提供；不得取生产配置补测。代码已提供显式复测工具，不能把 mock 写成实测。
2. 本分支无公共 contracts、NamingMemory/Audit/Origin 持久仓储和 V2 processing 注册。按文件归属不得修改这些文件；需整合会话提供装配、迁移和任务依赖保护，才可验证跨重启规则记忆及真实下载/文件管理入口。
3. Python >=3.12 和整合后的完整 OpenAPI/跨重启数据库/主页面/端到端验收仍需整合环境证据。
4. 全量旧截图随机边界问题已复现且未修复，涉及公共 screenshots.py。不得擅自改公共模块、降低断言或只报告一次重跑通过。

提交只保存模块代码、测试和文档，不包含配置、数据库、媒体、运行产物、缓存、日志或凭据；不推送、不合并、不部署。最终提交 hash 由交付消息给出，避免文档自引用 hash。
