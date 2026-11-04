# B站下载 WebUI 与联动并行开发任务

工作区 `/opt/bili-remote-wt-download`；分支 `codex/download-v2`。本分支已按用户后续明确开发指令实现独立 UI 片段与来源联动适配器。所有写入均限制在下载模块文件白名单内。

## 功能范围

下载引擎、B站登录和旧接口继续沿用。独立 UI 以简化的链接解析、Web/TV 线路说明、正片/全选/反选/清空、画质/编码/音轨格式选择、保存设置、队列进度、任务操作、日志和解析历史构成下载路径。它复用既有 `/api/bili/*`、`/api/resolve`、`/api/episodes/formats*`、`/api/jobs*` 和 `/api/history*`，不另建任务中心。

下载成功后适配器传递 bvid、season_id、ep_id、cid、原始标题、剧集索引及带指纹的 ResourceRef；空值保持 null。NamingService 负责识别、探测与命名预览。此模块不直接重命名文件、不生成副标题。识别超时/失败只产生 warning，不改变下载终态。

## 修改文件范围

仅 `bili_remote/v2/download/`、`bili_remote/static/v2/download/`、`bili_remote/templates/v2/download/`、`tests/v2/download/`、`docs/v2/modules/download/`。不修改 downloader.py、bili.py 或公共挂载文件。整合所需事项记录在 `docs/v2/modules/download/handoff.md`。

## 验证命令和限制

运行 `python3 scripts/dev_test.py download`。测试使用开发脚本建立的临时目录和合成数据。回归覆盖现有下载器、验证、B站客户端及新增来源适配和浏览器交互。现阶段不得将 fake_ytdlp 或浏览器模拟响应描述为真实下载成功。Web/TV 真实登录与 Cookie、普通视频/番剧/合集真实解析、分P真实格式、aria2c、真实任务进度/重试/取消、真实文件管理联动均需分别标记为未在线验证。
