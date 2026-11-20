FROM mcr.microsoft.com/dotnet/sdk:8.0-bookworm-slim AS bdinfo-build
ARG TARGETARCH
ARG BDINFO_REF=764f3a1d591e492ad13aa884e770f9b447bdde0b
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/tetrahydroc/BDInfoCLI.git /src/bdinfo && cd /src/bdinfo && git checkout "$BDINFO_REF"
WORKDIR /src/bdinfo
RUN case "$TARGETARCH" in arm64) bdinfo_rid=linux-arm64 ;; *) bdinfo_rid=linux-x64 ;; esac \
    && dotnet publish BDInfo/BDInfo.csproj -c Release -r "$bdinfo_rid" --self-contained true -o /out \
    && mkdir -p /licenses && find . -maxdepth 2 -iname '*license*' -exec cp '{}' /licenses/ \;

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        aria2 ca-certificates ffmpeg fonts-wqy-microhei fonts-arphic-uming tini mediainfo \
        mesa-vulkan-drivers libvulkan1 libssl3t64 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=bdinfo-build /out/BDInfo /usr/local/bin/bdinfo
COPY --from=bdinfo-build /licenses/ /usr/share/licenses/bdinfo/
ENV DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /app/requirements.txt

COPY bili_remote /app/bili_remote
COPY pyproject.toml /app/pyproject.toml

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data /downloads /imports /staging /releases /torrents /images /release-info \
    && chown -R app:app /app /data /downloads /imports /staging /releases /torrents /images /release-info

USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "bili_remote.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"]
