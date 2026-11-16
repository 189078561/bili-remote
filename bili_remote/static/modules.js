(function () {
  "use strict";

  const state = {
    uploads: [],
    selectedUploads: [],
    releaseDocuments: [],
    torrentBuilds: [],
    currentReleaseAnalysis: null,
    currentReleaseDocumentId: null,
    releaseSource: null,
    torrentSource: null,
    loading: new Set()
  };

  const STATUS_LABELS = {
    queued: "排队中",
    uploading: "上传中",
    completed: "已完成",
    failed: "失败",
    saved: "已保存",
    ready: "就绪",
    draft: "草稿",
    running: "运行中",
    partial: "部分完成",
    cancelled: "已取消"
  };

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function button(label, className = "button ghost small") {
    const item = node("button", className, label);
    item.type = "button";
    return item;
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function busy(target, active) {
    if (!target) return;
    target.disabled = active;
    target.classList.toggle("is-busy", active);
    target.setAttribute("aria-busy", active ? "true" : "false");
  }

  function message(id, text, kind = "error") {
    const target = byId(id);
    if (!target) return;
    target.textContent = text || "";
    target.classList.toggle("module-success", kind === "success");
    target.classList.toggle("form-error", kind === "error");
  }

  function statusLabel(value) {
    return STATUS_LABELS[value] || value || "未知";
  }

  function formatTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN");
  }

  function formatBytes(value) {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "—";
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

  function statusPill(value) {
    return node("span", `status-pill ${value || ""}`.trim(), statusLabel(value));
  }

  function empty(text) {
    return node("p", "empty", text);
  }

  async function copyText(value) {
    const text = String(value || "");
    if (!text) throw new Error("没有可复制的内容");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      window.prompt("请复制以下内容", text);
    }
  }

  function settingsValues() {
    return (typeof appSettings !== "undefined" && appSettings?.values) || {};
  }

  function displayPath(rootKey, relativePath) {
    const scope = rootKey === "release_info" ? "release-info" : rootKey;
    const view = byId(`${scope}-view`);
    void view;
    const roots = typeof appSettings !== "undefined" ? appSettings?.directories || [] : [];
    const directory = roots.find(item => item.key === rootKey);
    const base = directory?.path || rootKey;
    return relativePath ? `${base}/${relativePath}` : base;
  }

  async function picker(options) {
    const roots = options.rootKeys || ["downloads", "torrents", "images", "release_info"];
    return FileBrowser.openPicker({
      mode: options.mode || "select-file",
      scope: "browse",
      rootKeys: roots,
      rootKey: options.rootKey || roots[0],
      path: options.path || "",
      selectionType: options.selectionType || "file",
      allowMultiple: Boolean(options.allowMultiple),
      title: options.title || "选择文件",
      confirmLabel: options.confirmLabel || "选择"
    });
  }

  async function pickOne(options) {
    const selected = await picker({...options, allowMultiple: false});
    if (!selected) return null;
    return Array.isArray(selected) ? selected[0] || null : selected;
  }

  function sourceLabel(source) {
    if (!source) return "";
    return `${source.name || source.relative_path || "已选择"} · ${displayPath(source.root_key, source.relative_path)}`;
  }

  function recordCard({title, meta = [], status, body, actions = [], image = null}) {
    const card = node("article", "module-record");
    const head = node("div", "module-record-head");
    const copy = node("div", "module-record-copy");
    copy.append(node("strong", null, title));
    for (const line of meta) copy.append(node("p", "muted", line));
    head.append(copy);
    if (status) head.append(statusPill(status));
    card.append(head);
    if (image) {
      const thumb = node("img", "module-record-thumb");
      thumb.src = image;
      thumb.alt = title;
      thumb.loading = "lazy";
      thumb.addEventListener("error", () => thumb.remove());
      card.prepend(thumb);
    }
    if (body) card.append(body);
    if (actions.length) {
      const footer = node("div", "module-record-actions");
      footer.append(...actions);
      card.append(footer);
    }
    return card;
  }

  function loadOverview() { return window.V2Home?.load(); }

  // Image hosting -------------------------------------------------
  function uploadImageUrl(record) {
    if(record.status === "completed" && record.remote_url) return record.thumbnail_url || record.remote_url;
    if (!record?.source_root_key || !record?.source_relative_path) return null;
    return `/api/files/image?root_key=${encodeURIComponent(record.source_root_key)}`
      + `&path=${encodeURIComponent(record.source_relative_path)}`;
  }

  async function loadUploadStatus() {
    const badge = byId("uploads-status-badge");
    const provider = byId("uploads-provider-status");
    try {
      const status = await api("/api/uploads/status");
      if (badge) {
        badge.textContent = status.configured ? "可上传" : "需要配置";
        badge.classList.toggle("failed", !status.configured);
      }
      if (provider) {
        provider.textContent = status.provider === "imgbb"
          ? "ImgBB（需要 API Key）"
          : status.provider === "pixhost" ? "Pixhost（无需密钥）" : "Catbox（无需密钥）";
      }
    } catch (error) {
      if (provider) provider.textContent = error.message;
    }
  }

  async function loadUploads() {
    const host = byId("uploads-list");
    const refresh = byId("uploads-refresh");
    if (!host || state.loading.has("uploads")) return;
    state.loading.add("uploads");
    busy(refresh, true);
    try {
      await loadUploadStatus();
      const result = await api("/api/uploads?limit=200");
      state.uploads = result.items || [];
      renderUploads();
    } catch (error) {
      host.replaceChildren(empty(`读取上传记录失败：${error.message}`));
    } finally {
      state.loading.delete("uploads");
      busy(refresh, false);
    }
  }

  function renderUploads() {
    const host = byId("uploads-list");
    if (!host) return;
    if (!state.uploads.length) {
      host.replaceChildren(empty("暂无上传记录。选择本地图片后会上传并保存真实远程链接。"));
      return;
    }
    host.replaceChildren();
    for (const record of state.uploads) {
      const meta = [
        `${record.provider || "图床"} · 尝试 ${record.attempts || 0} 次 · ${formatTime(record.updated_at)}`,
        record.remote_url || record.error || "等待上传结果"
      ];
      const actions = [];
      if (record.status === "completed" && record.remote_url) {
        const copy = button("复制链接");
        copy.addEventListener("click", async () => {
          try {
            await copyText(record.remote_url);
            message("uploads-message", "链接已复制", "success");
          } catch (error) {
            message("uploads-message", error.message);
          }
        });
        actions.push(copy);
        const open = node("a", "button ghost small", "打开图片");
        open.href = record.remote_url;
        open.target = "_blank";
        open.rel = "noopener noreferrer";
        actions.push(open);
      }
      if (record.status === "failed") {
        const retry = button("重试上传", "button secondary small");
        retry.addEventListener("click", async () => {
          busy(retry, true);
          try {
            const updated = await api(`/api/uploads/${record.id}/retry`, {method: "POST"});
            message(
              "uploads-message",
              updated.status === "completed" ? "重试上传成功" : updated.error || "重试上传失败",
              updated.status === "completed" ? "success" : "error"
            );
            await loadUploads();
          } catch (error) {
            message("uploads-message", error.message);
          } finally {
            busy(retry, false);
          }
        });
        actions.push(retry);
      }
      const select = document.createElement("input");
      select.type = "checkbox";
      select.className = "module-select";
      select.value = String(record.id);
      select.checked = state.selectedUploads.includes(Number(record.id));
      select.addEventListener("change",()=>{const id=Number(record.id);state.selectedUploads=state.selectedUploads.filter(value=>value!==id);if(select.checked)state.selectedUploads.push(id);});
      select.disabled = record.status !== "completed" || !record.remote_url;
      select.setAttribute("aria-label", `选择 ${record.source_name || record.id} 生成引用`);
      const titleRow = node("label", "module-record-title");
      titleRow.append(select, node("strong", null, record.source_name || `上传 #${record.id}`));
      const card = recordCard({
        title: "",
        meta,
        status: record.status,
        image: uploadImageUrl(record),
        actions
      });
      card.querySelector(".module-record-copy")?.replaceChildren(titleRow);
      host.append(card);
    }
  }

  function selectedUploadIds() {
    const checked=new Set([...document.querySelectorAll("#uploads-list .module-select:checked")].map(input=>Number(input.value)));
    return state.selectedUploads.filter(id=>checked.has(id));
  }

  async function generateUploadFormat(format) {
    const ids = selectedUploadIds();
    if (!ids.length) {
      message("uploads-message", "请先勾选至少一张已上传成功的图片");
      return;
    }
    const output = byId("uploads-format-output");
    try {
      const params = new URLSearchParams({format,variant:byId("uploads-link-variant")?.value || "original"});
      for (const id of ids) params.append("ids", String(id));
      const result = await api(`/api/uploads/formats?${params.toString()}`);
      output.classList.remove("hidden");
      output.querySelector("pre").textContent = result.content || "没有可用的链接";
      output.querySelector(".uploads-copy-output").onclick = async () => {
        try {
          await copyText(result.content);
          message("uploads-message", "引用内容已复制", "success");
        } catch (error) {
          message("uploads-message", error.message);
        }
      };
    } catch (error) {
      message("uploads-message", error.message);
    }
  }

  async function uploadSelectedImages(preselected = null) {
    if (!Array.isArray(preselected)) preselected = null;
    const select = byId("uploads-select");
    busy(select, true);
    try {
      const selected = preselected || await picker({
        title: "选择要上传的图片",
        selectionType: "file",
        allowMultiple: true,
        confirmLabel: "上传所选图片"
      });
      const images = (selected || []).filter(item => !item.is_dir && item.kind === "image");
      if (!images.length) {
        if (selected?.length) message("uploads-message", "所选文件中没有受支持的图片");
        return;
      }
      message("uploads-message", `正在上传 ${images.length} 张图片…`, "success");
      await ToolUI.startJob("uploads", "/api/uploads/jobs", {provider: null,
        items: images.map(item=>({root_key:item.root_key,relative_path:item.relative_path}))});
      message("uploads-message", "上传任务已加入队列，可在下方查看或取消。", "success");
    } catch (error) {
      message("uploads-message", error.message);
    } finally {
      busy(select, false);
    }
  }

  // Release information ------------------------------------------
  function mediaSummary(video) {
    const details = video?.details || {};
    return [
      details.width && details.height ? `${details.width}×${details.height}` : "未知分辨率",
      details.video_codec || "未知视频编码",
      (details.audio_codecs || []).join("/") || details.audio_codec || "未知音频",
      details.duration_seconds ? `${Math.round(details.duration_seconds)} 秒` : "未知时长"
    ].join(" · ");
  }

  function renderReleaseAnalysis(analysis) {
    const host = byId("release-analysis");
    if (!host) return;
    host.replaceChildren();
    if (!analysis) {
      host.classList.add("hidden");
      return;
    }
    host.classList.remove("hidden");
    const head = node("div", "module-record-head");
    head.append(node("strong", null, `分析结果 · ${analysis.files?.length || 0} 个文件`));
    if (analysis.warnings?.length) {
      head.append(node("span", "status-pill partial", `${analysis.warnings.length} 个提示`));
    }
    host.append(head);
    if (analysis.warnings?.length) {
      const warning = node("ul", "release-warnings");
      for (const item of analysis.warnings) warning.append(node("li", null, item));
      host.append(warning);
    }
    for (const video of analysis.videos || []) {
      const row = node("div", "release-media-row");
      row.append(node("strong", null, video.relative_path));
      row.append(node("span", "muted", video.status === "completed" ? mediaSummary(video) : `失败：${video.error || "未知错误"}`));
      host.append(row);
    }
  }

  async function loadReleaseDocuments() {
    const host = byId("release-list");
    const refresh = byId("release-refresh");
    if (!host || state.loading.has("release-list")) return;
    state.loading.add("release-list");
    busy(refresh, true);
    try {
      const result = await api("/api/release-info?limit=200");
      state.releaseDocuments = result.items || [];
      renderReleaseDocuments();
    } catch (error) {
      host.replaceChildren(empty(`读取发布信息失败：${error.message}`));
    } finally {
      state.loading.delete("release-list");
      busy(refresh, false);
    }
  }

  function renderReleaseDocuments() {
    const host = byId("release-list");
    if (!host) return;
    if (!state.releaseDocuments.length) {
      host.replaceChildren(empty("暂无已保存的发布信息。"));
      return;
    }
    host.replaceChildren();
    for (const document of state.releaseDocuments) {
      const actions = [];
      const load = button("打开编辑");
      load.addEventListener("click", () => openReleaseDocument(document.id));
      actions.push(load);
      const use = button("用于 PT 发布");
      use.addEventListener("click",()=>ToolUI.useInPublish("release",document)); actions.push(use);
      const card = recordCard({
        title: document.title || `发布信息 #${document.id}`,
        meta: [
          `${document.source_root_key || "downloads"} / ${document.source_relative_path || "—"}`,
          `${document.format || "markdown"} · ${formatTime(document.updated_at)}`
        ],
        status: document.status,
        actions
      });
      host.append(card);
    }
  }

  async function selectReleaseSource() {
    try {
      const selected = await pickOne({
        title: "选择视频、文件或目录",
        selectionType: "both",
        confirmLabel: "分析此对象"
      });
      if (!selected) return;
      state.releaseSource = selected;
      state.currentReleaseDocumentId = null;
      byId("release-source").value = sourceLabel(selected);
      if (!byId("release-title").value.trim()) {
        byId("release-title").value = selected.name || String(selected.relative_path || "").split("/").at(-1) || "";
      }
      await ToolUI.optical(selected, byId("release-optical"));
      byId("release-save").textContent = "保存发布信息";
      message("release-message", "已选择源对象，点击读取媒体信息开始分析。", "success");
    } catch (error) {
      message("release-message", error.message);
    }
  }

  async function selectDirectoryInto(buttonId, inputId, rootKey, title) {
    const target = byId(buttonId);
    busy(target, true);
    try {
      const selected = await pickOne({
        title,
        rootKey,
        rootKeys: [rootKey],
        selectionType: "directory",
        confirmLabel: "使用此目录"
      });
      if (selected) byId(inputId).value = selected.relative_path || "";
    } catch (error) {
      const errorId = rootKey === "release_info" ? "release-message" : rootKey === "torrents" ? "torrent-message" : "uploads-message";
      message(errorId, error.message);
    } finally {
      busy(target, false);
    }
  }

  async function analyzeRelease() {
    const analyze = byId("release-analyze");
    if (!state.releaseSource) {
      message("release-message", "请先选择源视频、文件或目录");
      return;
    }
    busy(analyze, true);
    message("release-message", "正在读取媒体信息和文件清单…", "success");
    try {
      await ToolUI.startJob("release", "/api/release-info/jobs", {
        root_key: state.releaseSource.root_key, relative_path: state.releaseSource.relative_path,
        title: byId("release-title").value.trim() || state.releaseSource.name, format: byId("release-format").value,
        output_relative_path: byId("release-output").value.trim(), filename: byId("release-filename").value.trim() || null,
        overwrite: byId("release-overwrite").checked, disc_selection: byId("release-optical").querySelector("select")?.value || null,
        representative_files: byId("release-representatives").value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean),
        metadata: ToolUI.metadata("release")
      });
      message("release-message", "任务已加入队列，下方可查看进度。", "success");
    } catch (error) {
      message("release-message", error.message);
    } finally {
      busy(analyze, false);
    }
  }

  async function saveRelease() {
    const save = byId("release-save");
    if (!state.releaseSource) {
      message("release-message", "请先选择源对象并读取媒体信息");
      return;
    }
    const description = byId("release-editor").value;
    busy(save, true);
    try {
      let record;
      if (state.currentReleaseDocumentId) {
        record = await api(`/api/release-info/${state.currentReleaseDocumentId}`, {
          method: "PUT",
          body: JSON.stringify({
            description,
            metadata: ToolUI.metadata("release"),
            output_relative_path: byId("release-output").value.trim(),
            write_file: true,
            overwrite: Boolean(byId("release-overwrite").checked)
          })
        });
      } else {
        record = await api("/api/release-info", {
          method: "POST",
          body: JSON.stringify({
            root_key: state.releaseSource.root_key,
            relative_path: state.releaseSource.relative_path,
            title: byId("release-title").value.trim() || state.releaseSource.name,
            format: byId("release-format").value,
            output_relative_path: byId("release-output").value.trim(),
            filename: byId("release-filename").value.trim() || null,
            description,
            overwrite: Boolean(byId("release-overwrite").checked)
          })
        });
      }
      state.currentReleaseDocumentId = record.id;
      byId("release-save").textContent = "保存修改";
      byId("release-editor").value = record.user_text || record.generated_text || description;
      message("release-message", `发布信息已保存：${record.output_relative_path || "记录已更新"}`, "success");
      await loadReleaseDocuments();
    } catch (error) {
      if (error.status === 409) {
        message("release-message", `${error.message}。如确认覆盖，请勾选“允许覆盖同名文件”后重试。`);
      } else {
        message("release-message", error.message);
      }
    } finally {
      busy(save, false);
    }
  }

  async function openReleaseDocument(documentId) {
    try {
      const document = await api(`/api/release-info/${documentId}`);
      state.currentReleaseDocumentId = document.id;
      ToolUI.setMetadata("release", document.media?.metadata || {});
      renderReports(document);

      state.releaseSource = {
        root_key: document.source_root_key,
        relative_path: document.source_relative_path,
        name: String(document.source_relative_path || "").split("/").at(-1) || document.title
      };
      byId("release-source").value = displayPath(document.source_root_key, document.source_relative_path);
      byId("release-title").value = document.title || "";
      byId("release-format").value = document.format || "markdown";
      byId("release-output").value = String(document.output_relative_path || "").split("/").slice(0, -1).join("/");
      byId("release-filename").value = String(document.output_relative_path || "").split("/").at(-1) || "";
      byId("release-editor").value = document.user_text || document.generated_text || "";
      renderReleaseAnalysis({
        files: document.file_list || [],
        videos: document.media?.videos || [],
        warnings: document.media?.warnings || []
      });
      byId("release-save").disabled = false;
      byId("release-save").textContent = "保存修改";
      byId("release-copy").disabled = false;
      message("release-message", "已载入发布信息，可直接编辑。", "success");
    } catch (error) {
      message("release-message", error.message);
    }
  }

  // Torrents ------------------------------------------------------
  async function selectTorrentSource() {
    try {
      const selected = await pickOne({
        title: "选择要制作种子的文件或目录",
        selectionType: "both",
        confirmLabel: "使用此源"
      });
      if (!selected) return;
      state.torrentSource = selected;
      byId("torrent-source").value = sourceLabel(selected);
      if (!byId("torrent-name").value.trim()) {
        byId("torrent-name").value = selected.name || String(selected.relative_path || "").split("/").at(-1) || "";
      }
      message("torrent-message", "已选择种子源，确认参数后生成种子。", "success");
    } catch (error) {
      message("torrent-message", error.message);
    }
  }

  async function loadTorrentBuilds() {
    const host = byId("torrent-list");
    const refresh = byId("torrent-refresh");
    if (!host || state.loading.has("torrents")) return;
    state.loading.add("torrents");
    busy(refresh, true);
    try {
      const result = await api("/api/torrents?limit=200");
      state.torrentBuilds = result.items || [];
      renderTorrentBuilds();
    } catch (error) {
      host.replaceChildren(empty(`读取种子记录失败：${error.message}`));
    } finally {
      state.loading.delete("torrents");
      busy(refresh, false);
    }
  }

  function renderTorrentBuilds() {
    const host = byId("torrent-list");
    if (!host) return;
    if (!state.torrentBuilds.length) {
      host.replaceChildren(empty("暂无已生成种子。"));
      return;
    }
    host.replaceChildren();
    for (const build of state.torrentBuilds) {
      const actions = [];
      const download = node("a", "button ghost small", "下载 .torrent");
      download.href = build.download_url || `/api/torrents/${build.id}/file`;
      actions.push(download);
      const use = button("用于 PT 发布"); use.addEventListener("click",()=>ToolUI.useInPublish("torrent",build)); actions.push(use);
      host.append(recordCard({
        title: build.name || `种子 #${build.id}`,
        meta: [
          `${build.file_count || 0} 个文件 · ${formatBytes(build.total_size_bytes)} · piece ${formatBytes(build.piece_length)}`,
          `Info Hash：${build.info_hash || "—"}`,
          `${build.private ? "私有种子" : "公开种子"} · ${formatTime(build.created_at)}`
        ],
        status: build.status,
        actions
      }));
    }
  }

  async function createTorrent() {
    const create = byId("torrent-create");
    if (!state.torrentSource) {
      message("torrent-message", "请先选择要制作种子的文件或目录");
      return;
    }
    busy(create, true);
    message("torrent-message", "正在计算分片并写入种子文件…", "success");
    try {
      const trackers = byId("torrent-trackers").value
        .split(/\r?\n/)
        .map(item => item.trim())
        .filter(Boolean);
      await ToolUI.startJob("torrents", "/api/torrents/jobs", {
          source_root_key: state.torrentSource.root_key,
          source_relative_path: state.torrentSource.relative_path,
          output_relative_path: byId("torrent-output").value.trim(),
          filename: byId("torrent-filename").value.trim() || null,
          name: byId("torrent-name").value.trim() || null,
          trackers,
          private: Boolean(byId("torrent-private").checked),
          piece_length: byId("torrent-piece").value.trim() || "auto",
          source: byId("torrent-source-tag").value.trim(),
          overwrite: Boolean(byId("torrent-overwrite").checked)
      });
      message("torrent-message", "种子任务已加入队列，下方可查看进度。", "success");
    } catch (error) {
      if (error.status === 409) {
        message("torrent-message", `${error.message}。如确认覆盖，请勾选“允许覆盖同名种子”后重试。`);
      } else {
        message("torrent-message", error.message);
      }
    } finally {
      busy(create, false);
    }
  }

  // Publishing ----------------------------------------------------
  async function openPublishResource(source) {
    if (!source || !window.AppResources) return false;
    const meta = await api("/api/files/meta?" + new URLSearchParams({root_key:source.root_key,path:source.relative_path}));
    return AppResources.openWorkbench([{...meta, root_key: source.root_key}]);
  }

  function renderReports(document) {
    const host=byId("release-reports"); host.replaceChildren();
    for(const report of document.media?.reports || []) {
      const details=node("details", "tool-advanced"); details.append(node("summary",null,`${report.kind} · ${report.source || report.id} · ${report.valid ? "完整报告" : "不满足发布要求"}`));
      const pre=node("pre","log-output",report.text || "没有生成报告");details.append(pre);host.append(details);
    }
  }
  document.addEventListener("tools:completed", async event=>{
    const {scope,task}=event.detail;
    if(scope==="release" && task.result?.record) {await openReleaseDocument(task.result.record.id); await loadReleaseDocuments();}
    if(scope==="torrents") await loadTorrentBuilds();
    if(scope==="uploads") await loadUploads();
  });
  // Wiring --------------------------------------------------------
  function wireNavigation() {
    document.addEventListener("app:view", event => {
      const view = event.detail?.view;
      if (view === "home") loadOverview();
      else if (view === "uploads") loadUploads();
      else if (view === "release-info") loadReleaseDocuments();
      else if (view === "torrents") {
        loadTorrentBuilds();
      }
    });
  }

  function wireOverview() {
    byId("home-refresh")?.addEventListener("click", loadOverview);
  }

  function wireUploads() {
    byId("uploads-select")?.addEventListener("click", uploadSelectedImages);
    byId("uploads-refresh")?.addEventListener("click", loadUploads);
    byId("uploads-copy-bbcode")?.addEventListener("click", () => generateUploadFormat("bbcode"));
    byId("uploads-copy-html")?.addEventListener("click", () => generateUploadFormat("html"));
    byId("uploads-copy-markdown")?.addEventListener("click", () => generateUploadFormat("markdown"));
  }

  function wireRelease() {
    byId("release-source-select")?.addEventListener("click", selectReleaseSource);
    byId("release-output-select")?.addEventListener("click", () => selectDirectoryInto(
      "release-output-select", "release-output", "release_info", "选择发布信息输出目录"
    ));
    byId("release-analyze")?.addEventListener("click", analyzeRelease);
    byId("release-save")?.addEventListener("click", saveRelease);
    byId("release-refresh")?.addEventListener("click", loadReleaseDocuments);
    byId("release-copy")?.addEventListener("click", async () => {
      try {
        await copyText(byId("release-editor").value);
        message("release-message", "发布描述已复制", "success");
      } catch (error) {
        message("release-message", error.message);
      }
    });
  }

  function wireTorrents() {
    byId("torrent-source-select")?.addEventListener("click", selectTorrentSource);
    byId("torrent-output-select")?.addEventListener("click", () => selectDirectoryInto(
      "torrent-output-select", "torrent-output", "torrents", "选择种子输出目录"
    ));
    byId("torrent-create")?.addEventListener("click", createTorrent);
    byId("torrent-refresh")?.addEventListener("click", loadTorrentBuilds);
  }

  function applyToolSettings(settings, forceScope = null) {
    const values=settings?.values || {};
    const fields={"torrent-trackers":"torrent_default_trackers","torrent-private":"torrent_default_private","torrent-piece":"torrent_default_piece_length","torrent-source-tag":"torrent_default_source","torrent-output":"torrent_output_relative_path","release-output":"release_info_output_relative_path","release-format":"release_info_default_format"};
    for(const [id,key]of Object.entries(fields)) {
      const input=byId(id); if(!input)continue;
      const force=forceScope==="torrents"?id.startsWith("torrent-"):forceScope==="release"?id.startsWith("release-"):false;
      if(input.dataset.dirty&&!force)continue;
      if(values[key]===undefined)continue;
      if(input.type==="checkbox")input.checked=Boolean(values[key]);else input.value=Array.isArray(values[key])?values[key].join("\n"):values[key];
      if(force)delete input.dataset.dirty;
    }
    for(const [scope,text] of [["release",`默认：${values.release_info_default_format || "markdown"} · 完整 MediaInfo / BDInfo`],["torrents",`默认：${values.torrent_default_private ? "私有" : "公开"} · piece ${values.torrent_default_piece_length || "auto"} · ${(values.torrent_default_trackers||[]).length} 个 Tracker`]])if(byId(`${scope}-defaults`))byId(`${scope}-defaults`).textContent=text;
  }
  byId("release-reset-defaults")?.addEventListener("click",()=>applyToolSettings(appSettings,"release"));
  byId("torrents-reset-defaults")?.addEventListener("click",()=>applyToolSettings(appSettings,"torrents"));
  for(const id of ["torrent-trackers","torrent-private","torrent-piece","torrent-source-tag","torrent-output","release-output","release-format"])byId(id)?.addEventListener("input",event=>{event.target.dataset.dirty="true";});

  document.addEventListener("settings-updated", event => {
    applyToolSettings(event.detail);
  });

  wireNavigation();
  wireOverview();
  wireUploads();
  wireRelease();
  wireTorrents();
  applyToolSettings(typeof appSettings !== "undefined" ? appSettings : null);

  document.addEventListener("file-tool:open",event=>{
    if(event.detail.kind === "uploads") loadUploads();
    if(event.detail.kind === "release-info") loadReleaseDocuments();
    if(event.detail.kind === "torrents") loadTorrentBuilds();
  });
  async function runFileTool(kind,items,rootKey) {
    const sources=items.map(item=>({...item,root_key:item.root_key || rootKey}));
    if(kind === "publish") return openPublishResource(sources[0]);
    const scope={report:"release-info",torrent:"torrents",upload:"uploads"}[kind];
    FileTools.open(scope);
    applyToolSettings(appSettings,kind === "report" ? "release" : "torrents");
    try {
      if(kind === "upload") return uploadSelectedImages(sources);
      const source=sources[0];
      if(kind === "report") {
        state.releaseSource=source;state.currentReleaseDocumentId=null;
        byId("release-source").value=sourceLabel(source);byId("release-title").value=source.name || "";
        byId("release-filename").value="";byId("release-overwrite").checked=false;byId("release-representatives").value="";
        byId("release-editor").value="";byId("release-save").disabled=true;byId("release-copy").disabled=true;
        ToolUI.setMetadata("release",{});
        await ToolUI.optical(source,byId("release-optical"));
        await analyzeRelease();
      } else {
        state.torrentSource=source;byId("torrent-source").value=sourceLabel(source);
        byId("torrent-name").value=source.name || "";byId("torrent-filename").value="";byId("torrent-overwrite").checked=false;
        await createTorrent();
      }
    } catch(error) {message(kind === "report" ? "release-message" : "torrent-message",error.message);}
  }
  window.BiliModules = {openPublishResource, runFileTool};
})();
