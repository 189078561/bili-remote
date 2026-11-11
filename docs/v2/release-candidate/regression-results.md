# 发布候选回归结果

日期：2026-11-11
候选源码 HEAD：`7659e48c251fb9546b21911cd9832844ae55231e`
分支：`codex/final-ui-v2`

## 工具环境

| 工具 | 版本 |
| --- | --- |
| Python 3.11 | 3.11.2 |
| Python 3.12 | 3.12.15 |
| FFmpeg | 5.1.9 |
| ffprobe | 5.1.9 |
| MediaInfo | 24.18.0 |
| Node.js | 24.18.0 |
| npm | 11.16.0 |

Python 3.12 使用现有隔离测试镜像，容器无网络，仅使用临时存储和源码白名单副本。没有升级宿主 Python、Node、Docker、Nginx 或其他运行环境。

## 全量测试

| 环境 | 结果 | 时间 | JUnit |
| --- | --- | --- | --- |
| Python 3.11.2 | **439 passed, 1 warning** | 519.36 秒 | `.dev/test-results-all.xml` |
| Python 3.12.15 | **439 passed, 1 warning** | 540.46 秒 | `.dev/py312-src/.dev/test-results-all.xml` |

两套 JUnit 均为：

```text
tests=439 failures=0 errors=0 skipped=0
```

唯一警告是 `starlette.testclient` 对 `httpx` 的既有弃用提示，与本次发布候选修复无关。

## 覆盖范围

全量范围实际覆盖：

- 原 Python 单元、服务、API、安全、数据库迁移和旧版浏览器回归。
- V2 下载、FileBrowser、命名、Subtitle、metadata、任务中心和工作台集成。
- 五页导航、空数据、长内容、浅色/深色、多视口响应式回归。
- 真实 H.264/AAC MP4、HEVC 多轨 MKV、外挂字幕、冲突、执行和撤销矩阵。
- 下载完成到文件管理、工作台、真实工具与发布预览的完整链路。
- torrent 源目录改名后的旧产物失效以及新产物重建。

没有通过删除断言、跳过测试或把失败改成预期错误来获得绿灯。

## 工程检查

已通过：

```sh
python3 tests/v2/workbench/build_frontend.py --check
git diff --check
```

源码包生成后还会执行：

```sh
unzip -t <package.zip>
zipinfo -1 <package.zip>
sha256sum <package.zip>
```

包内审计要求：不包含 `.env.dev`、真实 `.env`、Cookie、API Key、数据库、运行日志、媒体、下载资源、缓存、`.dev`、`.git`、`node_modules` 或备份。

## 测试证据

- `.dev/test-results-all.xml`：Python 3.11 全量 JUnit。
- `.dev/py312-src/.dev/test-results-all.xml`：Python 3.12 全量 JUnit。
- `.dev/final-product-visual/`：五页、五视口、浅色/深色及完整预览截图。
- `tests/v2/integration/test_release_media_matrix.py`：真实媒体矩阵。
- `tests/v2/integration/test_final_ui_flow.py`：完整离线链路。
- `tests/test_release_tools.py::test_torrent_download_rejects_build_after_source_rename`：torrent 源变化保护。

## 复现命令

```sh
python3 scripts/dev_test.py all
python3 scripts/dev_test_container.py all
python3 tests/v2/workbench/build_frontend.py --check
git diff --check
python3 scripts/package_release.py
```

## 结论

候选提交在本机双 Python 环境中没有已知失败、错误或跳过。测试通过不等同于外部服务在线通过；TMDB、Bangumi、PT-Gen、Minfo、B站真实下载、图床、qBittorrent 和 BDInfo 仍需独立配置后人工验收。
