# NamingService + MetadataResolver 并行开发任务

工作区 `/opt/bili-remote-wt-naming`；分支 `codex/naming-v2`。本次初始化不执行本任务，后续按用户业务开发指令启动。先读 AGENTS 和五份顶层契约，正式产品补充优先于旧项目文档。

## 功能范围

实现相互独立的 MetadataResolver 与 NamingService：B站来源、TMDB 身份/多语言与 Alternative Titles/季集/演员、Bangumi 多语言/声优/关联资料、可选 PT-Gen。身份不确定不覆盖手选。服务只管理结构化识别、逐文件媒体探测、PT 文件命名、映射、预览、安全执行与撤销、字幕/外音轨关联、规则记忆，不生成发布主标题或副标题。

支持电影、剧集、动画和整季独立模板，正式大写变量与 :00 格式；默认 Source=WEB-DL、Group=HDVWEB、Season=1，明确默认来源而非探测事实。技术参数真探测，不把 H265 当 x265、不从分辨率猜片源。名称候选及自定义、递增/递减/起始/offset/逐项修正、B站 ep_id 与 TMDB 季集映射均须支持。

流程为扫描→识别→预览→冲突检查→用户确认→执行。默认下载完成仅预览；高置信度自动改名默认关闭。原地改名，字幕语言后缀保留，关联外音轨须确认；批量使用唯一临时名过渡，最小操作审计支持恢复与撤销。不创建项目目录。

## 修改文件范围

仅 `bili_remote/v2/naming/`、`bili_remote/v2/metadata/`、`bili_remote/static/v2/naming/`、`bili_remote/templates/v2/naming/`、`tests/v2/naming/`、`tests/v2/metadata/`、`docs/v2/modules/naming/`。

NamingService 与 MetadataResolver 使用不同包/服务，不能因同一会话负责而合并。共用旧 media_probe/FileService 能力只读调用，需要底层修改时在 handoff.md 提建议。

## API 依赖与数据结构

按 api-contract.md 导出 resources resolve/probe、metadata search/capabilities、naming preview/apply/undo 的 router；不注册到主应用。使用 ResourceRef、ResourceContext、FieldValue、EpisodeMapping、MediaProbe、NamingRule、RenamePreview、TaskRef；命名记忆通过公共 NamingMemoryRepository Protocol，执行通过既有 processing 适配接口。

依赖尚未落地时用私有 fake 测试，不复制数据库迁移或正式模型。三层规则与 subtitle_rule 的存储仅通过允许字段仓储，不能保存生成文件名历史或副标题文本。已执行目标路径可在最小改名审计中保留，这是撤销所需，不是记忆库。

## 真实服务核验

先核实 TMDB/Bangumi/PT-Gen 实际 API、认证方式和返回结构，再实现对应 provider 映射。现有 metadata.py 仅是本地适配器实现证据，不能保证线上结构。用独立开发凭据、只读小样本，保存脱敏 schema/fixture 和 `docs/v2/modules/naming/provider-capabilities.md`；分别写明实测、mock、未配置、不可用。禁止用生产 Cookie/Key。PT-Gen 缺失/失败不能阻塞命名。

## 测试要求

初始回归 `python3 scripts/dev_test.py naming`；新增测试目录自动加入该命令。覆盖正式模板例子、候选选择、人工锁定、三层覆盖、跨季映射、常见集数语法、整季总集数与混合参数、逐文件真实 ffprobe、失败空值。测试字幕 chs/cht/eng/forced 与明确音轨关联、目标交换、大小写、越根 symlink、非法字符、指纹漂移、重复提交、失败恢复、撤销漂移。

预览前后文件和数据库生成内容零写入；执行任务 project_id=NULL，不存发布文案。所有媒体合成于 tmp_path。最小审计仓储用 fake 和后续整合临时 SQLite 测试，不操作生产库。

## 交付标准及公共文件禁改

交独立服务/router、契约样例、mock/实测说明、模块测试和 handoff.md，列需整合的仓储、迁移、任务 handler 和 UI 挂载。不得修改 web.py、database.py（含迁移入口）、services.py、main.py、config.py、index.html、app.js、全局 CSS/API 注册、旧 naming.py/filebrowser.py/media_probe.py/metadata.py、设置注册、依赖或公共测试夹具；完整禁改清单见 file-ownership.md。不自动合并或部署。
