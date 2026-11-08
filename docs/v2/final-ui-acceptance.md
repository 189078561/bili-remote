# 最终界面与交互验收

验收使用隔离工作区、临时 SQLite 与本地生成短视频。登录真实 FastAPI 应用，沿可见按钮执行；没有把静态设计稿或注入的成功状态作为本地工具成功证据。

## 验收矩阵

| 项目 | 证据 | 验证内容 |
| --- | --- | --- |
| 五页 × 五尺寸 × 双主题 | `tests/v2/integration/test_final_product.py::test_five_page_visual_matrix` | 50 张真实页面截图，10 张预览，尺寸与主题、无横向溢出、导航、资源直达与人工编辑保留 |
| 工作台/文件选择/命名/预览细节 | `test_phase_two_layout.py` | 双主题五尺寸、目录抽屉与按需详情、取消切换、同一编辑实例、复制与慢响应 |
| 规则保存与主题 | `test_theme_rules_and_drawer_keyboard` | 写入真实规则仓库、重新读取、主题偏好刷新、抽屉焦点和 Escape |
| 下载到资源 | `test_completed_download_opens_actual_files_and_same_workbench`、`test_stage_one.py` | 临时完成任务→实际文件目录/工作台，来源 job_id 保留，迟到识别不覆盖新资源 |
| 下载交互 | `tests/v2/download/test_browser.py` | 选集、批量画质、路径、取消、重试、日志、移动布局；旧解析响应不能覆盖新链接 |
| 本地工具 | `tests/v2/workbench/test_browser.py`、`test_runtime.py` | 真实截图/拼图、MediaInfo、制种及下载，任务关联，报告与图片进入发布内容 |
| 百分比截取 | `test_product_services.py` | 两个实际不同时长的视频按各自时长采样，非法/不完整范围拒绝 |
| 简介排版安全 | `test_product_services.py`、工作台浏览器测试 | BBCode 基础格式、真实图片链接、HTML 转义、危险链接不成为可执行链接 |
| 文件安全与兼容 | 完整现有测试 | 文件指纹/路径/冲突、预览零写入、执行与撤销、旧版兼容、无草稿/项目写入 |

全站主要操作不依赖悬停；手机导航、工具和预览在实际 390×844 检查。错误、缺项、无凭据、空目录、长文本、失败任务由对应浏览器与接口测试覆盖。深色表单标签对比度在截图审查中修正。

## 素材真实性

设计参考：《Bili Remote 2.0 全功能界面展示》《Bili Remote 2.0全端界面设计展板》。它们是概念图，图中的影视身份、容量、任务、播放器都不能用来证明实现。

现状基线：用户原有 `docs/v2/ui-review-export/`，在隔离工作区保留于 `.dev/baseline-ui-review-export/`，原目录未动。最终截图：`.dev/final-product-visual/`；工具与工作台细节：`.dev/workbench-product-visual/`。最终交付索引将包含截图校验清单。

合成视频不是实际影视作品。无外部凭据时显示未匹配/待确认；测试中的手工文案明确为合成样本。测试目录或产物随临时环境结束而清理，截图与报告作为验收证据保留。

## 离线与外部验证分界

本地命名、媒体读取、截图、报告、种子、预览、复制、设置和文件操作使用真实服务。以下仅做离线契约、transport 或错误场景测试，未进行真实外部认证验收：B站 Web/TV、TMDB、Bangumi、PT-Gen、图床、qBittorrent。完整蓝光 BDInfo 还需要独立合法样本。它们必须保留为部署前的外部核验项，不把替身响应计为真实成功。

## 最终测试记录

最终计数、解释器、耗时、提交与证据链接见 `final-delivery-report.md`。中途失败全部保留在 `.dev/` 日志中；新增测试的夹具错误及实际发现的取消状态刷新问题均修正，最终结果以最后运行的 XML 为准。
