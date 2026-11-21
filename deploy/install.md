# Bili Remote 私有镜像安装

## 单文件安装

在 Linux x86_64 设备上将[对应版本发布页](https://github.com/189078561/bili-remote/releases) 中的附件 `docker-compose.yml` 保存到独立目录。也可直接使用[仓库内的同名模板](https://github.com/189078561/bili-remote/blob/main/deploy/docker-compose.yml)。

1. 在 `bili-remote.environment` 中填入管理员密码 `ADMIN_PASSWORD` 以及用 `openssl rand -hex 32` 生成的 `APP_SECRET`。YAML 中含有字面量美元符号的密码需将每个 `$` 写为 `$$`。请妥善保存填过凭据的文件。
2. 私有镜像首次拉取需要执行 `docker login ghcr.io -u 你的GitHub用户名`。密码输入有包读取权限的 classic PAT，范围为 `read:packages`。
3. 执行 `docker compose up -d`，Docker 自动拉取 Bili Remote 和固定版本的 PT-Gen。用 `docker compose ps` 查看状态，或执行 `curl -fsS http://127.0.0.1:8111/healthz` 检查服务。

**访问与存储**　通过 HTTPS 反代到设备的 8111 端口时，保留 `SECURE_COOKIES: "true"`。可信局域网通过 `http://设备IP:8111` 登录时，改为 `"false"`。默认使用 Docker 命名卷，挂载宿主媒体目录时将 `downloads:/downloads` 改为例如 `/srv/media:/downloads`，并确保容器 UID 1000 对该目录可写。PT-Gen 默认在容器网络的 23333 端口提供服务。

**改参数与升级**　在原目录编辑 YAML 的 `environment`、`ports` 或 `volumes` 后执行 `docker compose up -d`。升级前备份配置和数据库，在原文件里更新镜像引用，保留项目名、挂载及 `APP_SECRET`，然后执行 `docker compose pull` 和 `docker compose up -d`。已有安装继续使用原有数据目录；从绑定挂载切换到新的命名卷会打开空数据目录。

## 使用已有的安装包

适用于 Linux x86_64、Docker Engine 和带 `--wait` 的 Docker Compose v2 或更高版本。下载私有 GitHub Release 中的 `bili-remote-compose.tar.gz` 并解压到独立目录。发布包中的应用和 PT-Gen 均固定到镜像摘要。

### 首次安装

登录 GHCR。使用获准读取此私有镜像的 GitHub 账号，密码处输入具有 `read:packages` 权限的 classic PAT。

```sh
docker login ghcr.io
cp .env.example .env
chmod 600 .env
openssl rand -hex 32
```

把上一步随机值填到 `.env` 的 `APP_SECRET`，为 `ADMIN_PASSWORD` 设置独立强密码。保存好 `APP_SECRET`，已有加密数据依赖它。按本机情况选择数据与媒体目录。下面以全新安装和示例的默认目录为例。

```sh
sudo install -d -o 1000 -g 1000 ./data /srv/bilibili-downloads ./imports ./staging ./releases ./torrents ./images ./release-info
docker compose pull
docker compose up -d --wait
docker compose ps
curl -fsS http://127.0.0.1:8111/healthz
```

默认应用端口为 8111。通过同机 HTTPS 反代访问时，可在 `.env` 写入 `BILI_REMOTE_BIND=127.0.0.1` 并将反代指向该端口。正常使用 HTTPS 和安全 Cookie。PT-Gen 仅在容器网络监听 23333，工作台设置里的 PT-Gen 地址可留空。电影或剧集的资料完整度取决于上游提供的内容和网络。若资料源需要凭据，可分别配置 `PTGEN_DOUBAN_COOKIE` 或 `PTGEN_TMDB_API_KEY`。工作台的 TMDB 匹配沿用设置页中的 TMDB 凭据。

### 更新

保留原项目目录、Compose 项目名、数据挂载和 `.env`。先备份配置、密钥及一致的数据库副本，再解压新发布包到临时目录。以新包的 `compose.yaml` 更新原文件，将新包 `.env.example` 中的 `BILI_REMOTE_IMAGE` 值更新到原 `.env`，然后执行 `docker compose pull` 和 `docker compose up -d --wait`。需要回到上一版时使用相应版本的 Compose、镜像摘要以及与该版本兼容的数据副本。
