# 统一 FileBrowser 并行开发任务

工作区 `/opt/bili-remote-wt-filebrowser`；分支 `codex/filebrowser-v2`。本次只准备环境；后续按用户业务开发指令执行，遵守 AGENTS、API、模型、归属与验收规范。

## 功能范围

复用既有 FileBrowser/FileService 后端和共享文件选择器，优化目录树、面包屑、列表、搜索分页、图片预览、文本在线编辑、复制粘贴/移动/删除/普通重命名、视频媒体信息和手机布局。不建立第二套文件管理系统，不改变实际存储结构，不擅自移动生产文件。

主界面仅四类：downloads、torrents、images、release_info。配置选择器现有八挂载 `scope=mounts` 可保留，但不扩充为八类导航。选文件或整季目录可直接进入同一智能命名和发种工作台，使用 ResourceRef/ResourceContext，无重复上传或资源项目。

智能命名由 NamingService 完成，不在 JS/filebrowser adapter 再解析季集、拼模板或改名关联文件。既有普通 rename/text/file operations 的安全语义不退化，根限制、路径正规化、权限、活动任务保护、覆盖确认和逐项错误均须保留。

## 修改文件范围

仅 `bili_remote/v2/filebrowser/`、`bili_remote/static/file-browser.js`、`bili_remote/static/v2/filebrowser/`、`bili_remote/templates/v2/filebrowser/`、`tests/v2/filebrowser/`、`docs/v2/modules/filebrowser/`。

static/file-browser.js 是本会话唯一获准修改的既有跨视图组件文件，需保留供现有页面使用的导出/调用方式。底层 file_service.py、filebrowser.py 含共用安全和旧智能改名能力，由整合会话统一桥接，本分支只读复用。

## API 依赖与数据结构

沿用 `/api/files/roots|browse|meta|text|image|rename|operations/*`，保留搜索先于分页及排序/focus 行为。智能命名调用 `/api/v2/resources/resolve` 与 `/api/v2/naming/*`。选择器输出 ResourceRef[]，页面复用已有 context_id/revision；工具进度来自 TaskRef，无独立队列/表。

显示真实服务端错误与目录权限状态，禁止用客户端路径拼接绕过校验。文本保存用已有 fingerprint，批量变更用既有 preview token；不要混用旧 fingerprint 和 V2 rename preview_token。

## 测试要求

`python3 scripts/dev_test.py filebrowser`，新增 tests/v2/filebrowser/ 自动加入；既有 tests/test_file_service.py 与 test_filebrowser.py 不退化。覆盖四根、树/面包屑、后端搜索/分页、图片/文本、剪切粘贴、复制/移动/删除和冲突。

负面测试覆盖 ../、绝对路径、NUL、根外 symlink、只读目录、保护根/数据库/运行目录、活动任务依赖目录、大小写同名、文件源指纹变化、目录移入自身、禁止静默覆盖、部分失败的剪贴板保留。用 tmp_path，不接触生产目录。

浏览器覆盖共享组件在文件管理/工作台嵌套弹窗的兼容、单/多选/整季、智能命名同 context 交接、390/768/1440 布局与无控制台错误。验证不复制命名逻辑、没有上传步骤或创建项目请求。

## 交付标准及公共文件禁改

交共享组件兼容改进、V2 adapter/视图片段、测试和 handoff.md；说明需要整合会话修改的挂载、底层 API/安全修正，不跨边界修补。改名/副标题算法只能消费命名/subtitle 服务。

禁改 web.py、database.py、services.py、main.py、config.py、index.html、app.js、全部全局 CSS/API 注册、迁移入口、file_service.py/filebrowser.py/naming.py、公共模型、全局设置、依赖和测试夹具、其他模块。不要因为旧智能改名代码位于 filebrowser.py 就将其视为本会话独占。
