# 设置设计与现有能力映射

本表只描述未来接线位置。本轮没有修改 schema、注册设置、保存数据或调用接口。示例值不是当前生产配置。

| 设计分组/项目 | 既有字段或能力 | 交互处理 |
| --- | --- | --- |
| 默认打开页面 | `app_default_view` | 同行下拉 |
| 外观 | 现有 `bili-theme` 浏览器偏好 | 跟随系统/浅色/深色，不写服务端 |
| 四类目录入口 | `directories.downloads / torrents / images / release_info` | 摘要路径 + 目录选择；禁止移动文件 |
| 文件默认排序/分页 | `file_browser_default_sort`、`file_browser_page_size` | 行内控件，只影响新列表 |
| 下载位置 | `download_default_output_dir` | 显示相对位置，按需展开选择 |
| 单视频/合集模板 | `single_output_template`、`playlist_output_template` | 默认显示可读摘要，编辑时展开实际模板 |
| 下载引擎 | `aria2_enabled`、`aria2_connections`、`aria2_resume_retries` | 开关/数值/枚举；活动任务不受影响 |
| 解析/线路 | `resolve_timeout_seconds`、`tv_cdn_probe_enabled` 等现有高级项 | 放入对应高级折叠区 |
| V2 命名规则 | 规则记忆的 `naming_rule.source / group / templates` | 区分全局规则和当前资源人工值；不冒充探测事实 |
| 副标题 | `subtitle_rule.template_id / cast_limit / show_director / section_separator` | 四内置类型和自定义；只保存规则，不保存生成文本 |
| 截图与拼图 | `collage_default_total / columns`、`screenshot_default_format` | 同行数量/布局/格式；现有其他参数归同组高级项 |
| 报告 | `release_info_include_mediainfo`、`release_info_default_format` | 开关/格式 |
| 图床 | 既有 image host 配置能力 | 未配置/已配置摘要，按需进入连接详情，不显示凭据 |
| 种子 | `torrent_default_private / piece_length / trackers` | 开关/分块/可展开列表 |
| qBittorrent | `qbittorrent_url / username / password / category / paused` 等 | 独立连接详情，不暗示保存等于连接已验证 |

本次详细绘制常用设置与关键交互状态，不删除当前产品其他配置。尚未逐项展开的旧命名预设、截图片头片尾/色彩/尺寸、完整下载线路参数、图床提供方特有字段、日志与维护等，应在设计确认后按同一行式检查器和高级分组继续映射；不能在实现时擅自减少能力。配置验证失败、版本冲突与需空闲后生效，应保留输入并就地解释，不以整页 toast 替代错误位置。
