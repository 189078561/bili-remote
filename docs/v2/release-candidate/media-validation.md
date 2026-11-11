# 真实媒体处理验收

日期：2026-11-11
测试入口：`tests/v2/integration/test_release_media_matrix.py`
执行环境：Python 3.11、FFmpeg 5.1.9、ffprobe 5.1.9、MediaInfo 24.18.0

## 测试原则

本轮不再使用几字节占位文件表示媒体。测试在 pytest 临时目录中由 FFmpeg 实时生成带真实视频流、音频流、字幕轨和容器结构的短媒体，并调用项目实际使用的 ffprobe、MediaInfo 和命名服务。除资源探测工具不可用时测试会明确失败/跳过外，正式全量运行没有跳过项。

## 媒体矩阵

| 样本 | 实际结构 | 验证内容 |
| --- | --- | --- |
| `H264.AAC.Source.mp4` | H.264 视频、AAC 音频、320×180、1.2 秒 | 视频/音频编码、分辨率、位深、ffprobe 来源和版本 |
| `HEVC.MultiTrack.Source.mkv` | H.265 视频、2 条 AAC 音轨、1 条 SRT、1 条 ASS、640×360 | HEVC 识别、eng/jpn 音轨、eng/chi 字幕轨和 MediaInfo 报告 |
| 连续三集 MP4 | `S01E01`、`S01E02`、`S01E09` | 整季识别、显式 9→3 集数覆盖、逐集命名 |
| 外挂字幕 | `.en.srt`、`.chs.ass` | 字幕关联、目标名称扩展名和内容指纹 |
| 长文件名样本 | 文件名长度超过 150 字符 | 长文件名的预览、生成与安全处理 |
| 电影回退样本 | 无法解析季集的真实 MP4 | 明确告警与电影命名模板回退 |
| 无法识别集数样本 | 真实 MP4，文件名为 `series_unknown_episode.mp4` | 不猜测集号；人工选择剧集模板并输入集号 7 后才可预览 |

## 真实探测结果

### H.264 + AAC MP4

- 视频编码识别为 `H.264`。
- 音频编码识别为 `AAC`。
- 分辨率识别为 `180p`。
- 位深识别为 `8`。
- `probe_tool` 为 `ffprobe`，同时记录实际探测版本。

### HEVC 多轨 MKV

- 视频编码识别为 `H.265`/`HEVC`。
- 分辨率识别为 `360p`，音频编码为 `AAC`。
- 流级探测器确认 1 条视频、2 条音频和 2 条字幕轨。
- 音轨语言为 `eng`、`jpn`；字幕轨语言为 `eng`、`chi`。
- MediaInfo 报告 `valid=true`，包含 `HEVC`、`Audio #1` 和 `Text #2`。
- 生成报告包含“媒体信息”正文，证明内容来自实际分析结果。

## 文件与命名结果

`test_real_naming_matrix_preview_conflict_apply_and_undo` 对整季目录和其中一个子视频同时选择：

1. 规范化后得到 3 个视频，没有因父子同时选择而重复。
2. 所有视频编码参数均标记来源为 `ffprobe`。
3. 命名规则来源为 `filename_episode`。
4. 预览包含 5 个操作，即 3 个视频和 2 个外挂字幕。
5. 文件名 `S01E09` 经显式集数覆盖后生成 `S01E03`，没有继续保留 `S01E09`。
6. 预览阶段目标冲突会使 `can_apply=false`，且真实源文件尚未改变。
7. 删除冲突后重新预览，执行前保存的所有源文件字节与执行后目标文件逐项一致。
8. 撤销后所有原始路径重新出现，文件字节与执行前完全一致。

电影和未知集数用例进一步确认：

- 无季集证据时使用可解释的电影回退，并明确返回 `missing_episode` 告警。
- 用户显式选择剧集模板并输入集数后，目标名包含 `.S01E07.`。
- 预览不会因回退或人工覆盖静默改变源文件。

## 媒体报告与缩略图

完整离线 E2E 还实际调用：

- FFmpeg 从真实视频生成 4 张截图和拼接图，并确认浏览器渲染出实际视频画面。
- MediaInfo 生成当前资源报告，结果进入发布完整预览。
- 截图、报告和 torrent 均作为当前资源上下文中的真实产物出现。

## 已知边界

- 合成样本用于确定性验证，不代表所有容器、异常流、损坏媒体或超大文件均已穷举。
- HEVC、多音轨和多字幕已在本机支持范围内验证；HDR、Dolby Vision、TrueHD、DTS、外挂字体和复杂 ASS 渲染仍需合法真实片源补验。
- BDInfo 需要合法完整蓝光目录结构。本轮没有该样本，只有普通 MediaInfo 真实通过和 BDInfo 不伪造结果契约通过。

## 复现

```sh
python3 scripts/dev_test.py integration --filter release_media_matrix
python3 scripts/dev_test_container.py integration --filter release_media_matrix
```

全量回归中该测试文件随 439 项测试一同通过，没有失败、错误或跳过。
