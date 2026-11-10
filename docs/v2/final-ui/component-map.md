# 最终 UI 组件与改造边界

基线为 `7ac7ca6`，视觉基线为本目录 reference 中五张已确认图片。运行框架仍为 FastAPI/Jinja2/原生 JavaScript；没有引入第二套文件、下载或命名服务。早期交付文档描述的是旧轮次，不作为本轮验收结论。

## 真正替换的页面结构

| 页面 | 原结构 | 本轮 DOM / 交互 | 负责文件 |
| --- | --- | --- | --- |
| 应用框架 | 侧边全局导航，手机折叠菜单 | 同一个五入口导航节点，桌面横向顶栏、手机安全区底栏；任务抽屉、用户外观菜单 | templates/index.html；templates/v2/shared/navigation.html；static/v2/shared/{shell.js,product.css,icons.svg} |
| 首页 | 宣传说明和入口块、简单资源集合 | 最近文件/下载完成两个列表视图；任务进度与错误重试；真实工具产物；手机任务优先 | templates/v2/shared/home.html；static/v2/shared/home.js |
| 文件 | 浏览树、列表及常驻详情；常驻按钮组 | 轻量目录导航、宽文件列表、选择后上下文操作；行菜单和按需详情；手机目录抽屉 | static/file-browser.js；static/v2/filebrowser/filebrowser.css |
| 工作台 | 身份搜索表单直接堆在主面、命名细节常驻 | 资源摘要 → 身份摘要 → 常用命名/首项差异 → 发布三字段 → 工具结果；匹配、全量差异、逐集规则、工具、预览分别按需打开 | templates/v2/workbench/workbench.html；static/v2/workbench/{workbench.js,workbench.css,index.html} |
| 下载 | 普通解析表单与选集块、队列 | 线路/链接行、阶段反馈、媒体摘要、逐集真实画质、下载摘要与手机底部提交；错误重试 | templates/v2/download/workbench.html；static/v2/download/{workbench.js,workbench.css} |
| 设置 | 多卡片、多列后台表单 | 分类导航/手机选择器 + 单一行内设置面；复杂模板、凭据按需展开；常用下载引擎项直接展示；真实配置搜索；有修改才显示保存栏 | static/{settings.js,settings-fields.js}；templates/index.html；static/v2/shared/product.css |

五页均包含结构与交互变化，不是仅改 CSS。`workspace-theme.css` 主要负责语义颜色与新旧作用域；`product.css` 负责公共布局/组件；两者的变化不单独算作页面重建。`static/v2/workbench/index.html` 是规范模板的生成入口，不是第二套实现。用 `python3 tests/v2/workbench/build_frontend.py --check` 检查一致性。

## 共用组件与数据来源

| 组件 | 状态归属 / 数据 | 交互边界 |
| --- | --- | --- |
| FileBrowser | 原 FileService、根目录、路径、指纹、选择集合 | 重读安全元数据后交接；父目录和内部视频在工作台处理前按规范路径去重；无选中批量条隐藏 |
| AppResources | 原 FileBrowserAdapter、DownloadCompletionAdapter | 文件/下载直接传 ResourceRef；真实已有上下文可延续；刷新后不伪造编辑恢复 |
| Workbench | 当前资源 epoch/context_id/revision、人工字段版本 | 选择、匹配、命名、生成请求各自检查版本；切换资源人工内容确认；不创建发布项目或草稿 |
| 命名差异面板 | NamingService 的令牌、过期时间、源指纹及冲突 | 预览只读，确认后服务再次预检；主面板接受的 batch_id 同步给原高级命名组件，支持原路撤销 |
| 影视匹配面板 | MetadataResolver 的 TMDB/Bangumi/可用资料源 | 候选、来源、年份、译名均来自响应；手填名称与影视身份分开 |
| 工具面板 | 原截图/报告/种子任务及 artifact_refs | 参数、执行、真实产物、下载/复制/上传；后台任务刷新不恢复发布文本 |
| 完整预览 | PublishingService + 安全格式渲染 | HTML/BBCode/Markdown、效果/源码、完整复制；本地缩略图单独标注，上传图床后才加入可发布内容 |
| 下载选集 | 原 resolve/formats/jobs API | 统一画质和逐集选择；已接受批次从选择中移除，部分失败重试不重复；旧解析/画质响应不覆盖新输入 |
| 设置行 | 原 schema、values、目录绑定与 secrets_configured | 真实保存及验证；秘密值不回显；目录待保存显示待校验；规则记忆使用原独立接口 |
| 首页 / 任务抽屉 | files/browse、jobs、统一 tasks 投影 | 显示真实资源、状态、产物；任务更新同步首页；错误与缺数据如实显示 |

## 设计系统

颜色、字号、间距和组件尺度见 design-baseline.md。浅色使用暖白、鼠尾草绿、深绿色主操作，深色使用对应语义变量。按钮、输入、分隔、选中、错误、警告共享 token。本地 Lucide 风格 SVG sprite，不依赖外部图标服务。V2 外壳由 `.final-ui` 限定；旧路由仍可使用原布局，组件安全逻辑不复制。

## 保留的后端与接口

相对于 `7ac7ca6`，本轮未修改 Python 业务文件、数据库结构、下载引擎、配置注册或依赖清单。命名、SubtitleService、任务执行、文件安全、规则记忆、旧路由全部复用。测试夹具、HTTP 故障注入和合成媒体仅存在于 tests 或临时目录；生产页面没有设计图里的影视作品、容量、速度或匹配状态常量。
