# 资料接口能力核验

2026-11-04（Asia/Shanghai）。没有读取生产 .env、Cookie、API Key、数据库或私有备份。默认测试全部使用 HTTP transport mock；下表只描述另行执行的真实只读核验。

| 来源 | 当前状态 | 真实证据 | 不应据此声称的能力 |
| --- | --- | --- | --- |
| Bangumi | verified，公开接口无需凭据 | v0 POST /search/subjects；GET /subjects/8、/subjects/8/persons、/subjects/8/characters、/subjects/8/subjects，均 HTTP 200、JSON；保存仅字段类型 | 不代表所有条目有繁体/英文/声优；不代表 TMDB/Bangumi ID 可自动等价；没有核验私有/NSFW/用户接口 |
| TMDB | unconfigured | 未认证 GET /3/configuration 返回 HTTP 401，仅证明可达及需要凭据 | 没有开发 token，不能声称电影/剧集/翻译/演员/季集认证实测通过 |
| PT-Gen | unconfigured | 未提供测试服务地址/开发凭据，没有向任意公共 PT-Gen 实例发请求 | 没有部署版本、认证方式或搜索/详情字段的真实证据；旧代码与 mock 均不算在线核验 |

Bangumi 服务版本未在响应中暴露；只能记录 v0 API 路径，不能杜撰部署版本。认证字段 authentication=unconfigured 表示没有开发 token，不妨碍公开接口 verified。精确 checked_at、schema hash、HTTP method/status/content-type 和脱敏原始/归一化结构保存在 provider-schemas.json。样本不含标题正文、角色名、图像 URL、凭据、认证头和查询参数。

显式复测命令：

```sh
/opt/bili-remote/.venv/bin/python -m bili_remote.v2.metadata.verify --output docs/v2/modules/naming/provider-schemas.json
```

可通过 `--config <独立开发JSON路径>` 提供 tmdb_token、bangumi_token、ptgen_endpoint、ptgen_api_key。不自动读取 .env/.env.dev；不要提交配置文件。工具只保存 schema 与脱敏状态，不打印密钥/鉴权 URL。注入的测试 transport 被拒绝标为真实 verified。HTTP 重定向拒绝，不将凭据转发至其他 origin；4 MB 响应上限、超时和脱敏错误已实现。

离线测试覆盖 TMDB 多语言/Alternative Titles/Translations 回退/演员/所选季真实列表、Bangumi 多语言别名/声优/关联资料、两种 PT-Gen 路径和 apikey、403/429/重定向/超时/非法 JSON/未配置/部分失败。它们证明适配和降级逻辑，不证明上游实际权限。
