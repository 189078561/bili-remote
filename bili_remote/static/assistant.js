/* One state and one durable task workflow across desktop and mobile. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const root = $("publish-view");
  const storageKey = "biliRemote.assistant.v1";
  const defaults = {
    sources: [],
    metadata: {},
    analysis: null,
    report: null,
    images: [],
    uploads: [],
    torrents: [],
    jobs: [],
    fields: {},
    dirty: {},
    config: {},
    step: "files",
    tab: "preview",
    pipeline: null,
  };
  let restored;
  try {
    restored = JSON.parse(localStorage.getItem(storageKey));
  } catch {}
  const state = { ...defaults, ...(restored || {}) };
  let browser,
    initializing,
    analysisSeq = 0,
    selectionTimer,
    searchSeq = 0,
    pipelineRunning = false;
  const watchers = new Map();
  const labels = {
    thumbnail: "缩略图",
    report: "媒体报告",
    upload: "图床上传",
    torrent: "种子制作",
  };
  const statuses = {
    queued: "排队中",
    running: "处理中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  const terminal = (status) =>
    ["completed", "failed", "cancelled"].includes(status);
  const node = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const post = (url, data) =>
    api(url, { method: "POST", body: JSON.stringify(data) });
  const persist = () => {
    try {
      localStorage.setItem(storageKey, JSON.stringify(state));
    } catch {}
  };
  const bytes = (value) => {
    let n = Number(value) || 0;
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    while (n >= 1024 && i < 4) {
      n /= 1024;
      i++;
    }
    return `${n.toFixed(i ? 2 : 0)} ${units[i]}`;
  };
  const time = (seconds) => {
    const n = Math.max(0, Math.floor(Number(seconds) || 0));
    return [Math.floor(n / 3600), Math.floor(n / 60) % 60, n % 60]
      .map((x) => String(x).padStart(2, "0"))
      .join(":");
  };
  const sourceKey = (s) => (s ? `${s.root_key}/${s.relative_path}` : "");
  const imageUrl = (path, download = false) =>
    "/api/files/image?" +
    new URLSearchParams({
      root_key: "images",
      path,
      ...(download ? { download: "true" } : {}),
    });
  function notify(text, error = false) {
    window.AppNotice.show(text, error);
  }
  function action(text, fn, cls = "button secondary small") {
    const b = node("button", text, cls);
    b.type = "button";
    b.onclick = () => guard(b, fn);
    return b;
  }
  async function guard(button, fn) {
    button.disabled = true;
    $("assistant-error").textContent = "";
    try {
      return await fn();
    } catch (e) {
      $("assistant-error").textContent = e.message;
      notify(e.message, true);
    } finally {
      button.disabled = false;
    }
  }
  async function copy(text) {
    if (!text) throw Error("暂无可复制的内容");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const input = node("textarea");
      input.value = text;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.append(input);
      input.select();
      const ok = document.execCommand("copy");
      input.remove();
      if (!ok) throw Error("浏览器未允许复制，请长按文本复制");
    }
    notify("已复制");
  }
  function setStep(step) {
    state.step = step;
    root.querySelector(".assistant-grid").dataset.step = step;
    root.querySelectorAll(".assistant-steps [data-step]").forEach((b) => {
      b.classList.toggle("active", b.dataset.step === step);
      b.setAttribute(
        "aria-current",
        b.dataset.step === step ? "step" : "false",
      );
    });
    persist();
  }
  function setTab(tab) {
    if (tab === "links") tab = "thumbnail";
    state.tab = tab;
    root.querySelectorAll("[data-result]").forEach((b) => {
      b.classList.toggle("active", b.dataset.result === tab);
      b.setAttribute("aria-selected", String(b.dataset.result === tab));
    });
    root
      .querySelectorAll("[data-result-pane]")
      .forEach((p) => (p.hidden = p.dataset.resultPane !== tab));
    persist();
  }
  function details() {
    return (
      state.analysis?.videos?.find((v) => v.status === "completed")?.details ||
      state.report?.media?.videos?.find((v) => v.status === "completed")
        ?.details ||
      {}
    );
  }
  function renderMedia() {
    const host = $("assistant-media");
    host.replaceChildren();
    const source = state.sources[0];
    if (!source) {
      host.append(node("p", "尚未选择视频或目录", "muted"));
      return;
    }
    if (state.metadata.poster_url) {
      const img = node("img");
      img.src = state.metadata.poster_url;
      img.alt = "作品海报";
      img.onerror = () => img.remove();
      host.append(img);
    }
    const body = node("div"),
      d = details();
    body.append(node("strong", source.name || source.relative_path));
    const chips = node("div", null, "media-chips");
    [
      bytes(d.size_bytes || source.size_bytes),
      d.width && d.height ? `${d.width} × ${d.height}` : null,
      d.aspect_ratio,
      d.duration_seconds != null ? time(d.duration_seconds) : null,
    ]
      .filter(Boolean)
      .forEach((t) => chips.append(node("span", t)));
    body.append(
      chips,
      node(
        "p",
        d.video_codec
          ? `视频：${d.video_codec}　音频：${d.audio_codec || (d.audio_codecs || []).join(" / ") || "未知"}　帧率：${d.fps ? Number(d.fps).toFixed(3) : "未知"}`
          : state.analysis?.warnings?.join("；") ||
              "选择视频后读取可用的媒体信息",
      ),
    );
    host.append(body);
    $("assistant-selection").textContent =
      `已选择 ${state.sources.length} 项 · ${state.sources.map((s) => s.name || s.relative_path).join("、")}`;
  }
  function renderReport() {
    const host = $("assistant-report-output");
    host.replaceChildren();
    const record = state.report;
    if (!record) return;
    const panel = node("details", null, "assistant-advanced");
    panel.append(node("summary", "完整媒体报告"));
    for (const report of record.media?.reports || []) {
      panel.append(
        node("h3", report.kind + " · " + (report.source || "")),
        node("pre", report.text || "报告不可用", "assistant-report-text"),
      );
    }
    const text = record.generated_text || "";
    panel.append(
      action("复制完整报告", () => copy(text)),
      action("下载报告", () => {
        const url = URL.createObjectURL(
          new Blob([text], { type: "text/plain;charset=utf-8" }),
        );
        const link = node("a");
        link.href = url;
        link.download = "mediainfo.txt";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }),
    );
    host.append(panel);
  }
  function renderMetadata() {
    $("assistant-metadata").replaceChildren();
    const m = state.metadata;
    if (!m.tmdb_id) return;
    const card = metadataCard(m);
    card.disabled = true;
    $("assistant-metadata").append(card);
    renderMedia();
  }
  function metadataCard(m) {
    const b = node("button", null, "tmdb-match");
    b.type = "button";
    if (m.poster_url) {
      const img = node("img");
      img.src = m.poster_url;
      img.alt = "";
      img.loading = "lazy";
      img.onerror = () => img.remove();
      b.append(img);
    }
    const body = node("div");
    body.append(
      node(
        "strong",
        `${m.title_zh || m.original_title || m.title_en} (${m.year || "年份未知"})`,
      ),
      node(
        "small",
        `${m.media_type === "tv" ? "剧集" : "电影"} · ${(m.genres || []).join(" / ") || "TMDB #" + m.tmdb_id}`,
      ),
    );
    b.append(body);
    return b;
  }
  async function chooseSources(sources) {
    if (!sources.length) {
      ++analysisSeq;
      state.sources = [];
      state.analysis = null;
      state.report = null;
      state.metadata = {};
      $("assistant-selection").textContent = "尚未选择文件";
      renderMedia();
      renderMetadata();
      persist();
      return;
    }
    const old = sourceKey(state.sources[0]),
      next = sourceKey(sources[0]);
    state.sources = sources;
    if (old !== next) {
      state.analysis = null;
      state.report = null;
      state.metadata = {};
      state.publishId = null;
      renderMetadata();
    }
    renderMedia();
    persist();
    if (old === next && state.analysis) return;
    const seq = ++analysisSeq;
    const source = sources[0];
    if (source.kind !== "video" && !source.is_dir) return;
    ++searchSeq;
    $("assistant-media").append(node("p", "正在读取媒体信息…", "muted"));
    try {
      const analysis = await post("/api/release-info/analyze", {
        root_key: source.root_key,
        relative_path: source.relative_path,
        format: "text",
      });
      if (seq !== analysisSeq) return;
      state.analysis = analysis;
      renderMedia();
      compose();
      persist();
    } catch (e) {
      if (seq === analysisSeq) {
        renderMedia();
        $("assistant-media").append(node("p", e.message, "form-error"));
      }
    }
  }
  function compose(force = false) {
    const d = details(),
      m = state.metadata,
      s = state.sources[0];
    if (!s) throw Error("请先选择视频或目录");
    const technical = [
      d.height ? `${d.height}p` : null,
      d.video_codec,
      d.audio_codec || (d.audio_codecs || []).join("."),
    ].filter(Boolean);
    const title = m.title_en || m.original_title;
    const season = (s.name || s.relative_path).match(
      /S\d{1,2}(?:E\d{1,3})?/i,
    )?.[0];
    const generated = {
      title: title
        ? [title, m.year, season, ...technical].filter(Boolean).join(" ")
        : (s.name || s.relative_path).replace(/\.[^.]+$/, ""),
      subtitle: [m.title_zh, m.year, (m.genres || []).join(" / ")]
        .filter(Boolean)
        .join(" · "),
      description: [
        m.overview,
        d.video_codec
          ? `文件：${s.name || s.relative_path}\n大小：${bytes(d.size_bytes)}\n分辨率：${d.width} × ${d.height}　画面比例：${d.aspect_ratio || "未知"}\n视频：${d.video_codec}　音频：${d.audio_codec || (d.audio_codecs || []).join(" / ") || "未知"}\n时长：${time(d.duration_seconds)}　帧率：${d.fps || "未知"}`
          : null,
      ]
        .filter(Boolean)
        .join("\n\n"),
    };
    let kept = false;
    for (const [key, value] of Object.entries(generated)) {
      if (state.dirty[key] && !force) {
        kept = true;
        continue;
      }
      state.fields[key] = value;
      state.dirty[key] = false;
      $("assistant-" + key).value = value;
    }
    $("assistant-edit-note").textContent = kept
      ? "已保留手动编辑的内容。选择“重新生成”并确认后才会替换。"
      : "内容已根据当前媒体与 TMDB 更新，可直接编辑和分别复制。";
    livePreview();
    persist();
  }
  function livePreview() {
    $("assistant-live-preview").textContent = [
      state.fields.title,
      state.fields.subtitle,
      state.fields.description,
    ]
      .filter(Boolean)
      .join("\n\n");
  }
  function linkRows(record) {
    const box = node("div", null, "upload-links");
    for (const [label, value] of [
      ["URL", record.remote_url],
      ["BBCode", `[img]${record.remote_url}[/img]`],
    ]) {
      const row = node("div", null, "upload-link"),
        input = node("input");
      input.readOnly = true;
      input.value = value || "";
      input.setAttribute("aria-label", label);
      input.onclick = () => input.select();
      row.append(
        input,
        action("复制 " + label, () => copy(value)),
      );
      box.append(row);
    }
    return box;
  }
  function renderImages() {
    if (!state.images.length) return;
    const host = $("assistant-images");
    host.replaceChildren();
    for (const item of state.images) {
      const card = node("article", null, "thumbnail-result"),
        img = node("img");
      img.src = imageUrl(item.output_relative_path);
      img.alt = item.source_name || "生成的完整缩略图";
      const zoom = () => {
        const a = node("a");
        a.href = img.src;
        a.target = "_blank";
        a.rel = "noopener";
        a.click();
      };
      img.tabIndex = 0;
      img.setAttribute("role", "button");
      img.setAttribute("aria-label", "查看缩略图大图");
      img.onclick = zoom;
      img.onkeydown = (e) => {
        if (e.key === "Enter") zoom();
      };
      const caption = node(
        "p",
        `${item.source_name} · ${item.output_width || "?"} × ${item.output_height || "?"} · ${bytes(item.output_size_bytes)} · JPG（质量 95）`,
        "thumbnail-caption",
      );
      const actions = node("div", null, "thumbnail-actions"),
        download = node("a", "↓ 下载缩略图", "button secondary");
      download.href = imageUrl(item.output_relative_path, true);
      download.download = "";
      actions.append(
        download,
        action("♧ 生成图床链接", () => uploadImages([item]), "button primary"),
      );
      card.append(img, caption, actions);
      const uploaded = state.uploads.find(
        (u) =>
          u.status === "completed" &&
          u.source_relative_path === item.output_relative_path,
      );
      if (uploaded) card.append(linkRows(uploaded));
      host.append(card);
    }
  }
  function renderUploads() {
    if (!state.uploads.length) return;
    const host = $("assistant-links");
    host.replaceChildren();
    for (const record of state.uploads) {
      const card = node("article", null, "thumbnail-result");
      card.append(node("p", record.source_relative_path, "thumbnail-caption"));
      if (record.status === "completed") card.append(linkRows(record));
      else
        card.append(
          node("p", record.error || "上传失败，可重试", "form-error"),
          action("重试上传", () =>
            startJob(
              "upload",
              {
                items: [
                  {
                    root_key: record.source_root_key,
                    relative_path: record.source_relative_path,
                  },
                ],
                provider: record.provider,
              },
              {},
            ),
          ),
        );
      host.append(card);
    }
  }
  function renderTorrents() {
    if (!state.torrents.length) return;
    const host = $("assistant-torrents");
    host.replaceChildren();
    for (const record of state.torrents) {
      const card = node("article", null, "thumbnail-result");
      card.append(
        node("strong", record.name || record.filename || "种子 #" + record.id),
        node(
          "p",
          record.info_hash || record.infohash || "制作完成",
          "thumbnail-caption",
        ),
      );
      const link = node("a", "下载种子", "button primary");
      link.href = `/api/torrents/${record.id}/file`;
      link.download = "";
      card.append(
        link,
        action("添加到 qBittorrent", async () => {
          await post("/api/qbittorrent/torrents", {
            torrent_build_id: record.id,
          });
          notify("已添加到 qBittorrent");
        }),
      );
      host.append(card);
    }
  }
  function jobError(task) {
    return (
      task.result?.uploads
        ?.filter((u) => u.status === "failed")
        .map((u) => u.error)
        .filter(Boolean)
        .join("；") ||
      task.result?.items
        ?.filter((i) => i.status === "failed")
        .map((i) => i.error)
        .filter(Boolean)
        .join("；") ||
      task.error ||
      "任务未能完成，请检查参数后重试"
    );
  }
  function renderJobs() {
    const host = $("assistant-jobs");
    host.replaceChildren();
    for (const job of state.jobs.slice(-5).reverse()) {
      const card = node(
        "div",
        null,
        "assistant-job " + (job.status === "failed" ? "failed" : ""),
      );
      card.append(
        node(
          "strong",
          `${labels[job.kind]} #${job.id} · ${statuses[job.status] || job.status}`,
        ),
      );
      if (!terminal(job.status)) {
        const progress = node("progress");
        const r = job.progress || {};
        if (r.total) {
          progress.max = r.total;
          progress.value = (r.succeeded || 0) + (r.failed || 0);
        }
        card.append(
          progress,
          node(
            "span",
            job.networkError || r.current || "后台处理，可切换页面或刷新",
          ),
        );
        card.append(
          action("取消", () => post(`/api/tasks/${job.id}/cancel`, {})),
        );
      }
      if (job.error) card.append(node("p", job.error, "form-error"));
      if (["failed", "cancelled"].includes(job.status))
        card.append(
          action("重试", async () => {
            await post(`/api/tasks/${job.id}/retry`, {});
            job.status = "queued";
            job.error = "";
            job.applied = false;
            persist();
            await watchJob(job);
          }),
        );
      if (terminal(job.status))
        card.append(
          action("收起", () => {
            state.jobs = state.jobs.filter((j) => j !== job);
            persist();
            renderJobs();
          }),
        );
      host.append(card);
    }
    if (state.pipeline?.error)
      host.append(
        node("p", state.pipeline.error, "form-error"),
        action("继续组合任务", () => runPipeline()),
      );
  }
  async function applyResult(job, task) {
    if (job.applied) return;
    const result = task.result || {};
    if (job.kind === "thumbnail") {
      const session = await api(`/api/files/screenshots/${job.sessionId}`);
      const outputs = Object.values(session.final_outputs || {});
      const successful = outputs.filter((o) => o.status === "completed");
      if (successful.length) {
        state.images = successful.map((o) => ({
          ...o,
          sourceKey: sourceKey(job.source),
        }));
        if (job.pipeline && state.pipeline?.id === job.pipeline)
          state.pipeline.images = state.images;
        renderImages();
        setTab("thumbnail");
      }
      const failed = outputs.filter((o) => o.status !== "completed");
      const captureErrors = (session.videos || [])
        .flatMap((v) => [
          v.error,
          ...(v.items || [])
            .filter((i) => i.status === "failed")
            .map((i) => i.error),
        ])
        .filter(Boolean);
      if (failed.length || captureErrors.length)
        job.error = [...captureErrors, ...failed.map((o) => o.error)]
          .filter(Boolean)
          .join("；");
    } else if (job.kind === "upload") {
      for (const record of result.uploads || []) {
        state.uploads = state.uploads.filter(
          (u) =>
            u.source_relative_path !== record.source_relative_path ||
            u.source_root_key !== record.source_root_key,
        );
        state.uploads.unshift(record);
      }
      renderUploads();
      renderImages();
      setTab("links");
    } else if (job.kind === "torrent" && result.record) {
      state.torrents = [
        result.record,
        ...state.torrents.filter((r) => r.id !== result.record.id),
      ];
      renderTorrents();
      setTab("torrent");
    } else if (job.kind === "report" && result.record) {
      if (sourceKey(job.source) === sourceKey(state.sources[0])) {
        state.report = result.record;
        renderMedia();
        renderReport();
        compose();
        setTab("preview");
      }
      notify("完整媒体报告已生成");
    }
    job.applied = true;
    persist();
  }
  function watchJob(job) {
    if (watchers.has(job.id)) return watchers.get(job.id);
    const promise = (async () => {
      while (true) {
        let task;
        try {
          task = await window.AppTaskCenter.readProcessing(job.id);
          job.networkError = "";
        } catch (e) {
          if (e.status === 404) {
            job.status = "failed";
            job.error = "任务记录不存在，请重新生成";
            persist();
            renderJobs();
            throw Error(job.error);
          }
          job.networkError = `连接中断：${e.message}；正在重连`;
          renderJobs();
          await new Promise((r) => setTimeout(r, 2500));
          continue;
        }
        job.status = task.status;
        job.progress = task.result;
        job.error = task.error || "";
        if (terminal(task.status)) {
          await applyResult(job, task);
          if (
            task.status !== "completed" ||
            Number(task.result?.failed) > 0 ||
            job.error
          ) {
            job.error =
              job.error && job.error !== task.error
                ? job.error
                : jobError(task);
            job.status = "failed";
            persist();
            renderJobs();
            throw Error(job.error);
          }
          persist();
          renderJobs();
          // Completion notification is emitted once by the shared task projection.
          return task;
        }
        persist();
        renderJobs();
        await new Promise((r) => setTimeout(r, 1000));
      }
    })().finally(() => watchers.delete(job.id));
    watchers.set(job.id, promise);
    return promise;
  }
  async function startJob(kind, payload, context = {}) {
    const routes = {
      thumbnail: "/api/files/screenshots",
      report: "/api/release-info/jobs",
      torrent: "/api/torrents/jobs",
      upload: "/api/uploads/jobs",
    };
    const response = await post(routes[kind], payload);
    const job = {
      id: response.task.id,
      kind,
      status: response.task.status,
      sessionId: response.session_id,
      source: context.source || state.sources[0],
      pipeline: context.pipeline || null,
      payload,
    };
    state.jobs.push(job);
    if (context.pipeline && state.pipeline) state.pipeline.active = job.id;
    persist();
    renderJobs();
    return watchJob(job);
  }
  function selectedMedia(sources = state.sources) {
    const items = sources.filter((s) => s.kind === "video" || s.is_dir);
    if (!items.length) throw Error("请选择视频文件或目录");
    return items;
  }
  function config() {
    return Object.fromEntries(
      [
        ...root.querySelectorAll(
          ".assistant-config input[id],.assistant-config select[id]",
        ),
      ].map((n) => [n.id, n.type === "checkbox" ? n.checked : n.value]),
    );
  }
  function thumbnailPayload(sources = state.sources, c = config()) {
    const single = c["assistant-mode"] === "single",
      total = single ? 1 : Number(c["assistant-count"]),
      columns = single ? 1 : Number(c["assistant-layout"]),
      rows = Math.ceil(total / columns);
    if (!Number.isInteger(total) || total < 1 || total > 120 || rows > 12)
      throw Error(
        "截图数量须为 1–120，布局最多 12 行；请增加列数或减少截图数量",
      );
    const width = Number(c["assistant-width"]);
    return {
      sources: selectedMedia(sources).map((s) => ({
        root_key: s.root_key,
        relative_path: s.relative_path,
      })),
      output_mode: "collage",
      format: "jpg",
      style: "script",
      total,
      columns,
      rows,
      thumbnail_width: width,
      width,
      sampling_mode: c["assistant-sampling"],
      timestamps:
        single && c["assistant-time"].trim()
          ? [c["assistant-time"].trim()]
          : [],
      content_type: "custom",
      skip_start_seconds: Number(c["assistant-start"]),
      skip_end_seconds: Number(c["assistant-end"]),
      auto_upload: false,
    };
  }
  async function uploadImages(images = state.images, context = {}) {
    if (!images.length) throw Error("请先生成完整缩略图");
    return startJob(
      "upload",
      {
        items: images.map((i) => ({
          root_key: "images",
          relative_path: i.output_relative_path,
        })),
        provider: (context.config || config())["assistant-provider"] || null,
      },
      context,
    );
  }
  async function runKind(kind, context = {}) {
    const sources = context.sources || state.sources,
      c = context.config || config(),
      source = selectedMedia(sources)[0];
    context = { ...context, source };
    if (kind === "thumbnail")
      return startJob(kind, thumbnailPayload(sources, c), context);
    if (kind === "report")
      return startJob(
        kind,
        {
          root_key: source.root_key,
          relative_path: source.relative_path,
          format: "text",
          filename: `media-${Date.now()}.txt`,
        },
        context,
      );
    if (kind === "upload")
      return uploadImages(
        context.pipeline ? state.pipeline.images || state.images : state.images,
        context,
      );
    if (kind === "torrent") {
      if (sources.length !== 1)
        throw Error("制作种子请选择一个视频或包含所有文件的目录");
      const trackers = (context.trackers ?? $("assistant-trackers").value)
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      return startJob(
        kind,
        {
          source_root_key: source.root_key,
          source_relative_path: source.relative_path,
          private: c["assistant-private"],
          ...(trackers.length ? { trackers } : {}),
        },
        context,
      );
    }
    if (kind === "publish") {
      if (sourceKey(source) !== sourceKey(state.sources[0]))
        throw Error("当前文件已改变，请切回原资源后继续生成发布信息");
      compose();
      setTab("preview");
    }
  }
  async function runPipeline() {
    if (pipelineRunning) return;
    const pipeline = state.pipeline;
    if (!pipeline) return;
    pipelineRunning = true;
    pipeline.error = "";
    renderJobs();
    try {
      while (pipeline.steps.length) {
        if (pipeline.active) {
          const job = state.jobs.find((j) => j.id === pipeline.active);
          if (!job) throw Error("组合任务记录缺失，请重新选择操作");
          if (job.status === "failed" || job.status === "cancelled") {
            await post(`/api/tasks/${job.id}/retry`, {});
            job.status = "queued";
            job.applied = false;
          }
          await watchJob(job);
        } else
          await runKind(pipeline.steps[0], {
            sources: pipeline.sources,
            config: pipeline.config,
            trackers: pipeline.trackers,
            pipeline: pipeline.id,
          });
        pipeline.steps.shift();
        pipeline.active = null;
        persist();
      }
      state.pipeline = null;
      persist();
      notify("所选操作全部完成");
    } catch (e) {
      pipeline.error = `组合任务已暂停：${e.message}。修正后可继续。`;
      persist();
      renderJobs();
      notify(pipeline.error, true);
    } finally {
      pipelineRunning = false;
    }
  }
  async function saveDraft() {
    if (!state.sources.length) throw Error("请先选择文件");
    const data = {
      title: $("assistant-title").value,
      description: $("assistant-description").value,
      source_root_key: state.sources[0].root_key,
      source_relative_path: state.sources[0].relative_path,
      metadata: { ...state.metadata, subtitle: $("assistant-subtitle").value },
      release_document_id: state.report?.id || null,
      image_upload_ids: state.uploads
        .filter(
          (u) =>
            u.status === "completed" &&
            state.images.some(
              (i) =>
                i.output_relative_path === u.source_relative_path &&
                i.sourceKey === sourceKey(state.sources[0]),
            ),
        )
        .map((u) => u.id),
    };
    const session = state.publishId
      ? await api(`/api/publish/sessions/${state.publishId}`, {
          method: "PUT",
          body: JSON.stringify(data),
        })
      : await post("/api/publish/sessions", data);
    state.publishId = session.id;
    persist();
    notify("发布草稿已保存");
  }
  async function initBrowser() {
    if (browser) return;
    if (initializing) return initializing;
    initializing = (async () => {
      const source = state.sources[0];
      browser = new FileBrowser($("assistant-browser"), {
        mode: "select-file",
        selectionType: "both",
        allowMultiple: true,
        rootKey: state.location?.root || source?.root_key,
        path: state.location?.path || "",
        onSelectionChange: (items) => {
          clearTimeout(selectionTimer);
          selectionTimer = setTimeout(
            () => chooseSources(items).catch((e) => notify(e.message, true)),
            180,
          );
        },
      });
      const searchbar = node("div", null, "browser-searchbar"),
        roots = node("select");
      roots.setAttribute("aria-label", "文件根目录");
      const up = action("↑", () =>
        browser.navigate(
          browser.rootKey,
          browser.path.split("/").slice(0, -1).join("/"),
        ),
      );
      up.setAttribute("aria-label", "上级目录");
      const refresh = action("↻", () => browser.refresh());
      refresh.setAttribute("aria-label", "刷新文件");
      const search = node("input");
      search.placeholder = "搜索当前目录";
      search.setAttribute("aria-label", "搜索文件");
      let timer;
      search.oninput = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          browser.search = search.value;
          browser.loadDirectory({ page: 1 });
        }, 200);
      };
      roots.onchange = () => {
        search.value = "";
        browser.search = "";
        browser.navigate(roots.value, "");
      };
      searchbar.append(roots, up, search, refresh);
      $("assistant-browser").prepend(searchbar);
      const navigate = browser.navigate.bind(browser);
      browser.navigate = async (...args) => {
        await navigate(...args);
        roots.value = browser.rootKey;
        state.location = { root: browser.rootKey, path: browser.path };
        persist();
      };
      await browser.init();
      for (const r of browser.roots) roots.append(new Option(r.label, r.key));
      roots.value = browser.rootKey;
      if (source) {
        for (const s of state.sources) {
          if (s.root_key === browser.rootKey)
            browser.selected.set(`${s.root_key}\n${s.relative_path}`, s);
        }
        browser.updateSelection();
      }
    })()
      .catch((e) => {
        browser = null;
        $("assistant-browser").replaceChildren(
          node("p", e.message, "form-error"),
          action("重试读取", () => initBrowser()),
        );
      })
      .finally(() => (initializing = null));
    return initializing;
  }
  // Advanced tools stay inline; their existing services and event handlers remain shared.
  function openAdvanced(kind) {
    root.classList.add("tools-open");
    $("assistant-extra-tools").hidden = false;
    const content = $(kind + "-view");
    if (!content) return;
    const existing = $("assistant-tool-content").firstElementChild;
    if (existing) {
      existing.classList.add("hidden");
      $("file-tool-dock").append(existing);
    }
    content.classList.remove("hidden");
    $("assistant-tool-content").append(content);
    document.dispatchEvent(
      new CustomEvent("file-tool:open", { detail: { kind } }),
    );
  }
  function closeAdvanced() {
    if (!$("assistant-legacy").hidden) {
      const snapshot = BiliModules.assistantSnapshot();
      if (snapshot.source) {
        state.sources = [snapshot.source];
        state.metadata = snapshot.metadata;
        state.publishId = snapshot.publishId;
        for (const [key, value] of Object.entries(snapshot.fields)) {
          state.fields[key] = value;
          state.dirty[key] = true;
          $("assistant-" + key).value = value;
        }
        livePreview();
        renderMedia();
        renderMetadata();
        persist();
      }
    }
    const content = $("assistant-tool-content").firstElementChild;
    if (content) {
      content.classList.add("hidden");
      $("file-tool-dock").append(content);
    }
    root.classList.remove("tools-open");
    $("assistant-extra-tools").hidden = true;
    $("assistant-legacy").hidden = true;
  }
  root
    .querySelectorAll("[data-step]")
    .forEach((b) => (b.onclick = () => setStep(b.dataset.step)));
  root
    .querySelectorAll("[data-result]")
    .forEach((b) => (b.onclick = () => setTab(b.dataset.result)));
  root
    .querySelectorAll("[data-copy]")
    .forEach(
      (b) => (b.onclick = () => guard(b, () => copy($(b.dataset.copy).value))),
    );
  root
    .querySelectorAll("[data-assistant-tool]")
    .forEach((b) => (b.onclick = () => openAdvanced(b.dataset.assistantTool)));
  $("assistant-legacy-open").onclick = () =>
    guard($("assistant-legacy-open"), async () => {
      if (state.sources[0]) {
        await saveDraft();
        await BiliModules.openPublishSession(state.publishId);
      }
      root.classList.add("tools-open");
      $("assistant-legacy").hidden = false;
    });
  $("assistant-legacy-close").onclick = closeAdvanced;
  $("assistant-tools-close").onclick = closeAdvanced;
  $("assistant-rename").onclick = () =>
    guard($("assistant-rename"), () => {
      const videos = state.sources.filter(
        (s) => s.kind === "video" && s.allowed,
      );
      if (!videos.length) throw Error("请先选择要重命名的视频文件");
      openFileRenameWorkbench(videos);
    });
  document.addEventListener("files:renamed", (event) => {
    const { rootKey, directory, renamed } = event.detail;
    state.sources = state.sources.map((source) => {
      const match = renamed.find(
        (item) =>
          source.root_key === rootKey &&
          source.relative_path ===
            [directory, item.source_name].filter(Boolean).join("/"),
      );
      return match
        ? {
            ...source,
            name: match.target_name,
            relative_path: [directory, match.target_name]
              .filter(Boolean)
              .join("/"),
          }
        : source;
    });
    renderMedia();
    persist();
    browser?.refresh();
    notify(`已重命名 ${renamed.length} 个文件`);
  });
  document.addEventListener("tools:completed", (event) => {
    const task = event.detail.task;
    notify(
      task.status === "completed"
        ? "工具操作已完成"
        : task.error || "工具操作失败，可在当前面板重试",
      task.status !== "completed",
    );
  });
  for (const id of ["assistant-query", "assistant-type"])
    $(id).addEventListener("input", () => {
      ++searchSeq;
      $("assistant-matches").replaceChildren();
    });
  $("assistant-search").onsubmit = (event) => {
    event.preventDefault();
    const b = event.submitter;
    guard(b, async () => {
      const seq = ++searchSeq;
      $("assistant-search-error").textContent = "";
      $("assistant-matches").replaceChildren(node("p", "正在搜索…", "muted"));
      try {
        const type = $("assistant-type").value,
          result = await api(
            "/api/media/search?" +
              new URLSearchParams({
                query: $("assistant-query").value,
                media_type: type,
              }),
          );
        if (seq !== searchSeq) return;
        $("assistant-matches").replaceChildren();
        if (!result.items.length)
          $("assistant-matches").append(
            node("p", "未找到作品，请调整名称或切换电影 / 剧集", "muted"),
          );
        for (const item of result.items) {
          const card = metadataCard(item);
          card.onclick = () =>
            guard(card, async () => {
              const token = ++searchSeq;
              const response = await api(
                `/api/media/details/${item.media_type}/${item.tmdb_id}`,
              );
              if (token !== searchSeq) return;
              state.metadata = response.proposal;
              renderMetadata();
              $("assistant-matches").replaceChildren();
              if (state.sources.length) compose();
              persist();
            });
          $("assistant-matches").append(card);
        }
      } catch (e) {
        $("assistant-matches").replaceChildren();
        $("assistant-search-error").textContent = e.message;
      }
    });
  };
  for (const key of ["title", "subtitle", "description"]) {
    const input = $("assistant-" + key);
    input.value = state.fields[key] || "";
    input.oninput = () => {
      state.fields[key] = input.value;
      state.dirty[key] = true;
      livePreview();
      persist();
    };
  }
  const controls = root.querySelectorAll(
    ".assistant-config input[id],.assistant-config select[id]",
  );
  controls.forEach((n) => {
    if (state.config[n.id] !== undefined) {
      if (n.type === "checkbox") n.checked = state.config[n.id];
      else n.value = state.config[n.id];
    }
    n.addEventListener("change", () => {
      state.config = config();
      persist();
    });
  });
  function layoutChanged(changeCount = false) {
    const columns = Number($("assistant-layout").value);
    if (changeCount)
      $("assistant-count").value = columns * Number($("assistant-rows").value);
    else
      $("assistant-rows").value = Math.ceil(
        Number($("assistant-count").value) / columns,
      );
    for (const option of $("assistant-layout").options)
      option.textContent = `${option.value} × ${Math.ceil(Number($("assistant-count").value) / Number(option.value))}${option.value === "4" ? "（推荐）" : ""}`;
    state.config = config();
    persist();
  }
  for (const id of ["assistant-count", "assistant-layout"])
    $(id).addEventListener("change", () => layoutChanged());
  $("assistant-rows").addEventListener("change", () => layoutChanged(true));
  layoutChanged();
  const mode = () => {
    const single = $("assistant-mode").value === "single";
    root
      .querySelectorAll("[data-grid-option]")
      .forEach((n) => (n.hidden = single));
    root
      .querySelectorAll("[data-single-option]")
      .forEach((n) => (n.hidden = !single));
  };
  $("assistant-mode").addEventListener("change", mode);
  mode();
  for (const [id, kind] of [
    ["assistant-generate", "thumbnail"],
    ["assistant-report", "report"],
    ["assistant-torrent", "torrent"],
  ])
    $(id).onclick = () => guard($(id), () => runKind(kind));
  $("assistant-upload-selected").onclick = () =>
    guard($("assistant-upload-selected"), async () => {
      const items = state.sources.filter((s) => s.kind === "image");
      if (!items.length) throw Error("请在左栏选择图片");
      return startJob("upload", {
        items: items.map((s) => ({
          root_key: s.root_key,
          relative_path: s.relative_path,
        })),
        provider: $("assistant-provider").value || null,
      });
    });
  $("assistant-compose").onclick = () =>
    guard($("assistant-compose"), () => {
      compose();
      setTab("preview");
    });
  $("assistant-regenerate").onclick = () =>
    guard($("assistant-regenerate"), () => {
      if (
        Object.values(state.dirty).some(Boolean) &&
        !window.confirm(
          "重新生成会替换手动编辑的主标题、副标题和简介，是否继续？",
        )
      )
        return;
      compose(true);
    });
  $("assistant-save").onclick = () => guard($("assistant-save"), saveDraft);
  $("assistant-copy-all").onclick = () =>
    guard($("assistant-copy-all"), () =>
      copy(
        `[b]${state.fields.title || ""}[/b]\n${state.fields.subtitle || ""}\n\n${state.fields.description || ""}`,
      ),
    );
  $("assistant-run").onclick = () =>
    guard($("assistant-run"), async () => {
      if (state.pipeline) throw Error("已有组合任务，请先完成或继续该任务");
      selectedMedia();
      const steps = [
        "report",
        "thumbnail",
        "upload",
        "publish",
        "torrent",
      ].filter((k) => $("assistant-batch-" + k).checked);
      if (!steps.length) throw Error("请勾选至少一项操作");
      state.pipeline = {
        id: Date.now(),
        steps,
        sources: structuredClone(state.sources),
        config: config(),
        trackers: $("assistant-trackers").value,
        active: null,
      };
      persist();
      await runPipeline();
    });

  document.addEventListener("app:view", (event) => {
    if (event.detail.view === "publish") initBrowser();
  });
  renderMedia();
  renderMetadata();
  renderImages();
  renderUploads();
  renderTorrents();
  renderJobs();
  renderReport();
  livePreview();
  setStep(state.step);
  setTab(state.tab);

  for (const job of state.jobs)
    if (!terminal(job.status) && job.id !== state.pipeline?.active)
      watchJob(job).catch((e) => notify(e.message, true));
  if (state.pipeline && !state.pipeline.error) runPipeline();
  if (!root.classList.contains("hidden")) initBrowser();
  window.SeedAssistant = {
    openTool: (kind) => {
      openBusinessView("publish");
      openAdvanced(kind);
    },
    select: async (sources) => {
      openBusinessView("publish");
      closeAdvanced();
      await initBrowser();
      await chooseSources(sources);
      setStep("config");
    },
    state,
  };
})();
