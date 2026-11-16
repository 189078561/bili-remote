(function () {
  "use strict";

  const MAX_COLUMNS = 10;
  const MAX_ROWS = 12;
  const MAX_TOTAL = 120;
  const MAX_SOURCES = 100;
  const CONTENT_PRESETS = {
    movie: [90, 120],
    series: [90, 180],
    anime: [90, 90],
    custom: [0, 0]
  };
  const CONTENT_LABELS = {
    movie: "电影",
    series: "电视剧",
    anime: "动漫",
    custom: "自定义"
  };
  const RESERVED = new Set([
    "CON", "PRN", "AUX", "NUL",
    ...Array.from({length: 9}, (_, index) => `COM${index + 1}`),
    ...Array.from({length: 9}, (_, index) => `LPT${index + 1}`)
  ]);

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function button(text, className = "button ghost small") {
    const item = node("button", className, text);
    item.type = "button";
    return item;
  }

  function markBusy(target, busy) {
    target.disabled = busy;
    target.classList.toggle("is-busy", busy);
    target.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function settingsValues() {
    return (typeof appSettings !== "undefined" && appSettings?.values) || {};
  }

  function boundedInteger(value, fallback, minimum, maximum) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(minimum, Math.min(maximum, Math.trunc(number)));
  }

  function boundedSeconds(value, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.max(0, Math.min(86400, number));
  }

  function formatBytes(value) {
    const bytes = Number(value) || 0;
    if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
    if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(2)} MB`;
    if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${bytes} B`;
  }

  function formatDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN");
  }

  function outputName(sourceName) {
    const dot = String(sourceName || "").lastIndexOf(".");
    let stem = dot > 0 ? sourceName.slice(0, dot) : String(sourceName || "");
    stem = stem.replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/[\s.]+$/g, "").trim();
    stem = Array.from(stem).slice(0, 180).join("") || "video";
    if (RESERVED.has(stem.split(".", 1)[0].toUpperCase())) stem = `_${stem}`;
    return `${stem}_collage.jpg`;
  }

  function screenshotDefaults() {
    const values = settingsValues();
    const contentType = Object.hasOwn(CONTENT_LABELS, values.collage_default_content_type)
      ? values.collage_default_content_type
      : "anime";
    const preset = CONTENT_PRESETS[contentType];
    return {
      mode: values.screenshot_default_sampling || "random",
      total: boundedInteger(values.collage_default_total, 20, 1, MAX_TOTAL),
      timestamps: (values.screenshot_default_timestamps || []).join("\n"),
      width: boundedInteger(values.screenshot_default_width, 1920, 0, 3840),
      contentType,
      skipStart: contentType === "custom"
        ? boundedSeconds(values.collage_custom_skip_start, 0)
        : preset[0],
      skipEnd: contentType === "custom"
        ? boundedSeconds(values.collage_custom_skip_end, 0)
        : preset[1],
      outputRelativePath: String(values.screenshot_output_relative_path || "").trim()
    };
  }

  function collageDefaults() {
    const values = settingsValues();
    return {
      layoutMode: values.collage_default_layout_mode === "count" ? "auto" : "custom",
      columns: boundedInteger(values.collage_default_columns, 5, 1, MAX_COLUMNS),
      rows: boundedInteger(values.collage_default_rows, 4, 1, MAX_ROWS),
      width: boundedInteger(values.collage_default_width, 480, 240, 1920),
      gap: boundedInteger(values.collage_default_gap, 3, 0, 40),
      outputRelativePath: String(values.collage_output_relative_path || "").trim(),
      overwrite: false
    };
  }

  function imageDirectory() {
    return (typeof appSettings !== "undefined" ? appSettings : null)?.directories
      ?.find(item => item.key === "images") || null;
  }

  function directoryDisplay(relativePath) {
    const base = imageDirectory()?.path || "图片目录";
    return relativePath ? `${base}/${relativePath}` : base;
  }

  function imageUrl(item) {
    return `/api/files/image?root_key=${encodeURIComponent(item.root_key || "images")}`
      + `&path=${encodeURIComponent(item.relative_path || "")}`
      + `&v=${encodeURIComponent(item.fingerprint || item.mtime_ns || "")}`;
  }

  function imageDownloadUrl(item) {
    return `${imageUrl(item)}&download=1`;
  }

  function videoLabel(video) {
    const source = video?.source || {};
    return source.name || source.relative_path || "视频";
  }

  function mediaLabel(video) {
    const media = video?.media || {};
    const parts = [];
    if (media.width && media.height) parts.push(`${media.width}×${media.height}`);
    if (media.video_codec) parts.push(media.video_codec);
    if (media.audio_codec) parts.push(media.audio_codec);
    if (media.duration_seconds) parts.push(`${Math.round(media.duration_seconds)} 秒`);
    return parts.join(" · ") || "媒体信息待识别";
  }

  function statusLabel(status) {
    return ({
      pending: "等待中",
      queued: "排队中",
      running: "处理中",
      completed: "已完成",
      partial: "部分完成",
      failed: "失败",
      cancelled: "已取消"
    })[status] || status || "未知";
  }

  function terminalTask(task) {
    return ["completed", "failed", "cancelled"].includes(task?.status);
  }

  function normalizeSources(items, rootKey) {
    return (Array.isArray(items) ? items : [])
      .filter(item => item && (item.is_dir || item.kind === "video") && item.allowed !== false)
      .map(item => ({
        root_key: item.root_key || rootKey,
        relative_path: item.relative_path,
        name: item.name,
        disc_selection: item.disc_selection || null
      }))
      .filter(item => item.root_key && item.relative_path);
  }

  async function open(items, rootKey) {
    const sources = normalizeSources(items, rootKey);
    if (!sources.length) return;
    try {
      for(const source of sources) {
        const selected=(items || []).find(item=>item.relative_path===source.relative_path);
        if(selected?.is_dir) {
          const info=await api(`/api/media/optical?${new URLSearchParams({root_key:source.root_key,relative_path:source.relative_path})}`);
          source.disc_selection=info.selected;
        }
      }
      createWorkbench(sources, null);
    } catch(error) {
      const panel=node("div","collage-dialog");panel.append(node("h2",null,"无法生成缩略图"),node("p","form-error",error.message));showModal(panel);
    }
  }

  function openSession(sessionId) {
    if (!sessionId) return;
    createWorkbench([], sessionId);
  }

  function createWorkbench(initialSources, sessionId, host = null) {
    if (host) host.dispatchEvent(new Event("workbench:replace"));
    const state = {
      initialSources,
      sessionId,
      session: null,
      activeTask: null,
      pollTimer: null,
      polling: false,
      itemBusy: new Set(),
      reordering: new Set(),
      selected: new Map(),
      knownStatuses: new Map(),
      screenshot: screenshotDefaults(),
      collage: collageDefaults(),
      lastTaskId: null,
      drag: null
    };

    const panel = node("div", "collage-dialog screenshot-workbench");
    const header = node("div", "collage-header workbench-header");
    const titleBlock = node("div");
    titleBlock.append(node("p", "eyebrow", "SCREENSHOT WORKBENCH"));
    titleBlock.append(node("h2", null, "缩略图结果"));
    const subtitle = node("p", "muted", "按设置生成；可预览、下载及处理结果。");
    titleBlock.append(subtitle);
    header.append(titleBlock);
    const headerActions = node("div", "workbench-header-actions");
    const refreshSession = button("刷新会话", "button ghost small");
    headerActions.append(refreshSession);
    header.append(headerActions);
    panel.append(header);

    const globalError = node("p", "form-error");
    globalError.setAttribute("role", "alert");
    panel.append(globalError);

    const taskSection = node("section", "collage-progress workbench-task hidden");
    const taskTitle = node("strong", null, "正在处理…");
    const taskDetail = node("p", "muted");
    const taskBar = node("progress");
    taskBar.max = 1;
    taskBar.value = 0;
    const taskActions = node("div", "workbench-task-actions");
    const taskCancel = button("取消任务", "button danger small");
    const retryTask = button("重试失败项目", "button secondary small");
    retryTask.hidden = true;
    retryTask.addEventListener("click", async () => {
      try {const result = await api(`/api/tasks/${state.lastTaskId}/retry`, {method:"POST"}); startTask(result.task, state.lastTaskKind || "screenshot");}
      catch(error) {setGlobalError(error.message);}
    });
    const taskLogsButton = button("读取任务日志", "button ghost small");
    taskActions.append(taskCancel, retryTask, taskLogsButton);
    const taskLogs = node("pre", "log-output hidden");
    taskSection.append(taskTitle, taskDetail, taskBar, taskActions, taskLogs);
    panel.append(taskSection);
    const uploadTaskSection=node("section","tool-task");panel.append(uploadTaskSection);

    const generationSection = node("section", "collage-section screenshot-generation-section");
    const generationHead = node("div", "workbench-section-head");
    generationHead.append(node("h3", null, "资源"));
    generationHead.append(node("small", "muted", "默认参数在设置中管理。"));
    generationSection.append(generationHead);
    const sourceSummary = node("div", "screenshot-source-summary");
    generationSection.append(sourceSummary);

    const defaultsSummary = node("p", "muted", `${settingsValues().screenshot_output_mode === "collage" ? "拼图" : "独立单图"} · ${settingsValues().screenshot_default_format || "png"} · ${state.screenshot.mode === "timestamps" ? "指定时间点" : state.screenshot.mode === "uniform" ? "均匀取样" : "分段随机"}`);
    const settingsLink = button("前往设置");
    settingsLink.addEventListener("click", () => {window.FileTools?.returnTo({kind:"screenshots",sessionId:state.sessionId}); closeModal(); openSettingsPage();});
    const startGeneration = button("重新生成缩略图", "button secondary small");
    const publish = button("带入发布资料");
    publish.addEventListener("click", async () => {
      const source = state.session?.videos?.[0]?.source;
      closeModal();
      if (source) await window.BiliModules.openPublishResource(source);
      else openBusinessView("publish");
    });
    generationSection.replaceChildren(sourceSummary, defaultsSummary);
    const generationActions = node("div", "button-row");
    generationActions.append(startGeneration, publish, settingsLink);
    generationSection.append(generationActions);
    panel.append(generationSection);

    const screenshotSection = node("details", "collage-section screenshot-list-section");
    screenshotSection.open=settingsValues().screenshot_output_mode !== "collage";
    const framesSummary=node("summary",null,"独立帧与逐张编辑");screenshotSection.append(framesSummary);
    const screenshotHead = node("div", "workbench-section-head");
    screenshotHead.append(node("h3", null, "独立图片"));
    const screenshotHeadActions = node("div", "workbench-section-actions");
    const screenshotMeta = node("small", "muted", "尚未打开截图会话");
    const uploadSelected = button("上传所选截图", "button secondary small");
    screenshotHeadActions.append(screenshotMeta, uploadSelected);
    screenshotHead.append(screenshotHeadActions);
    screenshotSection.append(screenshotHead);
    const screenshotError = node("p", "form-error");
    const screenshotNotice = node("p", "muted screenshot-notice hidden", "");
    const screenshotBody = node("div", "screenshot-body");
    screenshotBody.append(node("p", "empty", "选择视频并生成截图后，可在此时逐张管理。"));
    screenshotSection.append(screenshotError, screenshotNotice, screenshotBody);
    panel.append(screenshotSection);

    const collageSection = node("section", "collage-section screenshot-collage-section");
    const collageHead = node("div", "workbench-section-head");
    collageHead.append(node("h3", null, "拼图"));
    const collageSummary = node("small", "muted", "勾选截图后可生成拼图");
    collageHead.append(collageSummary);
    collageSection.append(collageHead);

    const recompose = node("details", "result-secondary");
    recompose.append(node("summary", null, "编辑图片后重新合成 / 从所选图片合成"));
    const collageHint = node("p", "muted", `使用设置中的 ${state.collage.columns} 列${state.collage.layoutMode === "custom" ? " × " + state.collage.rows + " 行" : "自动排布"}，宽度 ${state.collage.width}px；从纯画面合成。`);
    recompose.append(collageHint);
    collageSection.append(recompose);
    const collageConflict = node("div", "collage-conflict hidden");
    collageConflict.setAttribute("role", "alert");
    collageSection.append(collageConflict);
    const collageActions = node("div", "dialog-actions workbench-actions");
    const generateCollage = button("重新合成所选图片", "button secondary small");
    collageActions.append(generateCollage);
    recompose.append(collageActions);
    const collageResults = node("div", "collage-results");
    collageSection.append(collageResults);
    panel.append(collageSection);
    if(settingsValues().screenshot_output_mode === "collage")panel.insertBefore(collageSection,screenshotSection);

    const footer = node("div", "dialog-actions collage-actions workbench-footer");
    const close = button("关闭", "button ghost");
    footer.append(close);
    panel.append(footer);
    if (host) { host.replaceChildren(panel); host.dataset.mounted = "true"; }
    else showModal(panel);

    const cleanup = () => {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
      document.removeEventListener("app:modal-close", onModalClose);
      document.removeEventListener("tools:completed", onUploadCompleted);
    };
    const onModalClose = event => {
      if (event.detail?.panel && event.detail.panel !== panel) return;
      cleanup();
    };
    const onUploadCompleted=event=>{if(event.detail.scope==="uploads"&&event.detail.task.input?.session_id===state.sessionId)loadSession().catch(error=>setScreenshotError(error.message));};
    document.addEventListener("tools:completed",onUploadCompleted);
    document.addEventListener("app:modal-close", onModalClose);
    if (host) host.addEventListener("workbench:replace", cleanup, {once:true});
    close.addEventListener("click", () => host ? openBusinessView("files") : closeModal());

    function activeTask() {
      return Boolean(state.activeTask);
    }

    function setGlobalError(message) {
      globalError.textContent = message || "";
    }

    function setScreenshotError(message) {
      screenshotError.textContent = message || "";
    }

    function renderSourceSummary() {
      const videos = state.session?.videos || [];
      const sources = videos.length
        ? videos.map(video => video.source || {})
        : state.initialSources;
      sourceSummary.replaceChildren();
      if (!sources.length) {
        sourceSummary.append(node("p", "empty", "请从文件浏览器选择视频后重新打开工具。"));
        return;
      }
      sourceSummary.append(node("strong", null, `源视频 ${sources.length} 个`));
      const list = node("div", "collage-source-list");
      for (const source of sources) {
        const row = node("div", "collage-source-row");
        row.append(node("strong", null, source.name || "视频"));
        row.append(node("span", "muted", source.relative_path || ""));
        row.append(node("code", "collage-output-name", `→ ${outputName(source.name)}`));
        if (source.disc_selection) {
          const selection=document.createElement("select");selection.setAttribute("aria-label",`光盘播放列表 ${source.name || source.relative_path}`);selection.disabled=activeTask();selection.title="选择播放列表后，点击重新生成应用到新会话";selection.append(new Option(source.disc_selection,source.disc_selection));
          selection.addEventListener("change",()=>{source.disc_selection=selection.value;});row.append(selection);
          api(`/api/media/optical?${new URLSearchParams({root_key:source.root_key,relative_path:source.relative_path})}`).then(info=>{selection.replaceChildren(...info.choices.map(choice=>new Option(`${choice.id} · ${choice.duration_seconds ? Math.round(choice.duration_seconds)+" 秒" : (choice.size_bytes/1024**3).toFixed(2)+" GiB"}`,choice.id)));selection.value=source.disc_selection;}).catch(error=>setGlobalError(error.message));
        }
        list.append(row);
      }
      sourceSummary.append(list);
    }

    function renderSessionMeta() {
      if (!state.session) {
        screenshotMeta.textContent = "尚未打开截图会话";
        return;
      }
      const counts = state.session.counts || {};
      screenshotMeta.textContent = `会话 ${state.session.id.slice(0, 8)} · ${statusLabel(state.session.status)} · 可用 ${counts.completed || 0} 张 · 缺失 ${counts.missing || 0} 张 · 失败 ${counts.failed || 0} 张`;
    }

    function syncSelection() {
      const videos = state.session?.videos || [];
      const liveVideos = new Set(videos.map(video => String(video.id)));
      for (const videoId of [...state.selected.keys()]) {
        if (!liveVideos.has(videoId)) state.selected.delete(videoId);
      }
      for (const video of videos) {
        const videoId = String(video.id);
        const selected = state.selected.get(videoId) || new Set();
        const items = video.items || [];
        const liveItems = new Set(items.map(item => String(item.id)));
        for (const itemId of [...selected]) {
          if (!liveItems.has(itemId)) selected.delete(itemId);
        }
        for (const item of items) {
          const itemId = String(item.id);
          const previous = state.knownStatuses.get(itemId);
          const usable = item.status === "completed" && item.available;
          if (usable && previous !== "completed") selected.add(itemId);
          if (!usable) selected.delete(itemId);
          state.knownStatuses.set(itemId, item.status);
        }
        state.selected.set(videoId, selected);
      }
    }

    function selectedItems(video) {
      const selected = state.selected.get(String(video.id)) || new Set();
      return (video.items || []).filter(item => selected.has(String(item.id)));
    }

    function renderSelectionSummary() {
      const videos = (state.session?.videos || []).filter(video => selectedItems(video).length);
      const total = videos.reduce((sum, video) => sum + selectedItems(video).length, 0);
      if (!state.session) {
        collageSummary.textContent = "生成截图后可勾选图片并生成拼图";
      } else if (!total) {
        collageSummary.textContent = "尚未选择可用于拼图的截图";
      } else {
        const highest = Math.max(...videos.map(video => selectedItems(video).length));
        const rows = state.collage.layoutMode === "auto"
          ? Math.ceil(highest / Math.max(1, state.collage.columns))
          : state.collage.rows;
        const layout = state.collage.layoutMode === "auto"
          ? `${state.collage.columns} 列，最多 ${rows} 行`
          : `${state.collage.columns} 列 × ${rows} 行`;
        collageSummary.textContent = `已选择 ${total} 张，涉及 ${videos.length} 个视频；${layout}`;
      }
      generateCollage.disabled = activeTask() || !state.session || total === 0;
      uploadSelected.disabled = activeTask() || !state.session || total === 0;
      startGeneration.disabled = activeTask() || !((state.session?.videos || []).length || state.initialSources.length);

    }

    function reorderLocally(video, draggedId, targetId) {
      const items = [...(video.items || [])];
      const from = items.findIndex(item => String(item.id) === draggedId);
      const to = items.findIndex(item => String(item.id) === targetId);
      if (from < 0 || to < 0 || from === to) return null;
      const [moved] = items.splice(from, 1);
      items.splice(to, 0, moved);
      return items.map(item => String(item.id));
    }

    async function persistOrder(video, itemIds) {
      const videoId = String(video.id);
      if (activeTask() || state.reordering.has(videoId)) return;
      state.reordering.add(videoId);
      setScreenshotError("");
      try {
        state.session = await api(`/api/files/screenshots/${encodeURIComponent(state.sessionId)}/items/order`, {
          method: "PUT",
          body: JSON.stringify({orders: {[videoId]: itemIds}})
        });
        syncSelection();
        renderScreenshots();
      } catch (error) {
        setScreenshotError(error.message);
      } finally {
        state.reordering.delete(videoId);
        renderScreenshots();
      }
    }

    function screenshotCard(video, item) {
      const card = node("article", `screenshot-card ${item.status === "failed" ? "failed" : ""} ${item.available ? "" : "missing"}`);
      const busy = state.itemBusy.has(String(item.id));
      const locked = activeTask() || busy || state.reordering.has(String(video.id));
      card.draggable = !locked;

      const top = node("div", "screenshot-card-top");
      const select = document.createElement("input");
      select.type = "checkbox";
      select.className = "screenshot-select";
      select.checked = (state.selected.get(String(video.id)) || new Set()).has(String(item.id));
      select.disabled = locked || item.status !== "completed" || !item.available;
      select.setAttribute("aria-label", `选择截图 ${item.timestamp}`);
      select.addEventListener("change", () => {
        const selected = state.selected.get(String(video.id)) || new Set();
        if (select.checked) selected.add(String(item.id));
        else selected.delete(String(item.id));
        state.selected.set(String(video.id), selected);
        renderSelectionSummary();
      });
      top.append(select, node("span", "screenshot-time", item.timestamp || "未知"));
      card.append(top);

      if (item.available) {
        const preview = document.createElement("img");
        preview.className = "screenshot-thumb";
        preview.loading = "lazy";
        preview.alt = `${videoLabel(video)} ${item.timestamp}`;
        preview.src = imageUrl(item);
        preview.addEventListener("click", () => openImagePreview(video, item));
        card.append(preview);
      } else {
        card.append(node("div", "screenshot-thumb placeholder", item.status === "failed" ? "截图失败" : "文件缺失"));
      }

      card.append(node("small", "screenshot-state", item.status === "failed" ? item.error || "截图失败" : item.available ? `实际时间 ${item.timestamp}` : "截图文件缺失，可尝试重截"));

      const timeRow = node("div", "screenshot-recapture-row");
      const timeInput = document.createElement("input");
      timeInput.value = item.timestamp || "";
      timeInput.placeholder = "HH:MM:SS";
      timeInput.maxLength = 20;
      timeInput.setAttribute("aria-label", "重截时间");
      const recapture = button("重截", "button secondary small");
      recapture.disabled = locked;
      recapture.addEventListener("click", async () => {
        markBusy(recapture, true);
        state.itemBusy.add(String(item.id));
        setScreenshotError("");
        try {
          state.session = await api(`/api/files/screenshots/${encodeURIComponent(state.sessionId)}/items/${encodeURIComponent(item.id)}/recapture`, {
            method: "POST",
            body: JSON.stringify({timestamp: timeInput.value.trim() || null})
          });
          syncSelection();
          renderScreenshots();
          renderSessionMeta();
        } catch (error) {
          setScreenshotError(error.message);
        } finally {
          state.itemBusy.delete(String(item.id));
          markBusy(recapture, false);
          renderScreenshots();
        }
      });
      timeRow.append(timeInput, recapture);
      card.append(timeRow);

      const actions = node("div", "screenshot-card-actions");
      if (item.raw_relative_path) {
        const raw = node("a", "button ghost small", "下载纯画面");
        raw.href = imageDownloadUrl({...item,relative_path:item.raw_relative_path,fingerprint:item.raw_fingerprint});
        raw.download = ""; actions.append(raw);
        const rawPreview=button("预览纯画面");
        rawPreview.addEventListener("click",()=>openImagePreview(video,{...item,relative_path:item.raw_relative_path,fingerprint:item.raw_fingerprint}));
        actions.append(rawPreview);
        const send = button("上传纯画面");
        send.addEventListener("click", async()=>{
          markBusy(send,true);
          try {await queueUploads([String(item.id)],"raw");}
          catch(error){setScreenshotError(error.message);}finally{markBusy(send,false);}
        }); actions.append(send);
      }
      const moveUp = button("↑", "button ghost small");
      const moveDown = button("↓", "button ghost small");
      const remove = button("删除", "button danger small");
      const upload = button("上传图床", "button ghost small");
      moveUp.title = "上移";
      moveDown.title = "下移";
      const uploadState = item.upload || {};
      upload.disabled = locked || !item.available || item.status !== "completed";
      upload.title = upload.disabled ? "截图文件不可用" : "上传此截图到当前图床";
      if (uploadState.status === "completed") upload.textContent = "重新上传";
      if (uploadState.status === "failed") upload.textContent = "重试上传";
      upload.addEventListener("click", async () => {
        if (upload.disabled) return;
        markBusy(upload, true);
        state.itemBusy.add(String(item.id));
        setScreenshotError("");
        try {
          await queueUploads([String(item.id)]);
          screenshotNotice.textContent="上传已加入后台队列，下方可查看进度。";screenshotNotice.classList.remove("hidden");
          renderScreenshots();
        } catch (error) {
          setScreenshotError(error.message);
        } finally {
          state.itemBusy.delete(String(item.id));
          markBusy(upload, false);
          renderScreenshots();
        }
      });
      const items = video.items || [];
      const index = items.findIndex(entry => String(entry.id) === String(item.id));
      moveUp.disabled = locked || index <= 0;
      moveDown.disabled = locked || index < 0 || index >= items.length - 1;
      remove.disabled = locked;
      moveUp.addEventListener("click", () => {
        if (index <= 0) return;
        const ids = items.map(entry => String(entry.id));
        [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
        persistOrder(video, ids);
      });
      moveDown.addEventListener("click", () => {
        if (index < 0 || index >= items.length - 1) return;
        const ids = items.map(entry => String(entry.id));
        [ids[index], ids[index + 1]] = [ids[index + 1], ids[index]];
        persistOrder(video, ids);
      });
      remove.addEventListener("click", async () => {
        if (!window.confirm(`确认删除截图 ${item.timestamp}？该操作只删除截图文件，不影响源视频。`)) return;
        markBusy(remove, true);
        state.itemBusy.add(String(item.id));
        setScreenshotError("");
        try {
          state.session = await api(`/api/files/screenshots/${encodeURIComponent(state.sessionId)}/items/${encodeURIComponent(item.id)}`, {method: "DELETE"});
          (state.selected.get(String(video.id)) || new Set()).delete(String(item.id));
          state.knownStatuses.delete(String(item.id));
          syncSelection();
          renderScreenshots();
          renderSessionMeta();
        } catch (error) {
          setScreenshotError(error.message);
        } finally {
          state.itemBusy.delete(String(item.id));
          markBusy(remove, false);
          renderScreenshots();
        }
      });
      actions.append(moveUp, moveDown);
      if (item.available) {
        const download = document.createElement("a");
        download.className = "button ghost small";
        download.textContent = "下载";
        download.href = imageDownloadUrl(item);
        download.setAttribute("download", "");
        actions.append(download);
      }
      actions.append(remove, upload);
      const more=node("details","screenshot-more");more.append(node("summary","button ghost small","更多"));
      const moreItems=node("div","screenshot-more-items");
      for(const control of [...actions.children]) if(!["下载","上传图床","重新上传","重试上传"].includes(control.textContent)) moreItems.append(control);
      more.append(moreItems);actions.append(more);
      card.append(actions);

      if (uploadState.status && uploadState.status !== "unavailable") {
        const uploadInfo = node("div", "screenshot-upload-state");
        if (uploadState.status === "completed" && uploadState.url) {
          const link = document.createElement("a");
          link.href = uploadState.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "已上传 · 打开图片";
          const copy = button("复制链接", "button ghost small");
          copy.addEventListener("click", async () => {
            try {
              await navigator.clipboard.writeText(uploadState.url);
              copy.textContent = "已复制";
            } catch (_) {
              window.prompt("复制图片链接", uploadState.url);
            }
          });
          uploadInfo.append(link, copy);
        } else if (uploadState.status === "failed") {
          uploadInfo.append(node("span", "form-error", uploadState.error || "上传失败"));
        } else {
          uploadInfo.append(node("span", "muted", "正在上传…"));
        }
        card.append(uploadInfo);
      }

      card.addEventListener("dragstart", event => {
        if (locked) {
          event.preventDefault();
          return;
        }
        state.drag = {videoId: String(video.id), itemId: String(item.id)};
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", String(item.id));
      });
      card.addEventListener("dragover", event => {
        if (state.drag?.videoId === String(video.id) && state.drag?.itemId !== String(item.id)) {
          event.preventDefault();
        }
      });
      card.addEventListener("drop", event => {
        event.preventDefault();
        if (state.drag?.videoId !== String(video.id)) return;
        const ids = reorderLocally(video, state.drag.itemId, String(item.id));
        state.drag = null;
        if (ids) persistOrder(video, ids);
      });
      card.addEventListener("dragend", () => {
        state.drag = null;
      });
      return card;
    }

    function renderScreenshots() {
      screenshotBody.replaceChildren();
      renderSessionMeta();
      if (!state.session) {
        screenshotBody.append(node("p", "empty", "生成截图后，可在此逐张预览、重截、删除和排序。"));
        renderSelectionSummary();
        return;
      }
      const videos = state.session.videos || [];
      if (!videos.length) {
        screenshotBody.append(node("p", "empty", "会话中没有视频记录。"));
        renderSelectionSummary();
        return;
      }
      for (const video of videos) {
        const group = node("article", "screenshot-video");
        const head = node("div", "screenshot-video-head");
        const titles = node("div");
        titles.append(node("strong", null, videoLabel(video)));
        titles.append(node("small", "muted", mediaLabel(video)));
        head.append(titles);
        head.append(node("span", `status-pill ${video.status || "pending"}`, statusLabel(video.status)));
        group.append(head);
        if (video.error) group.append(node("p", "form-error", video.error));
        const items = video.items || [];
        if (!items.length) {
          group.append(node("p", "empty", video.status === "failed" ? "该视频截图失败" : "正在等待截图…"));
        } else {
          const grid = node("div", "screenshot-grid");
          for (const item of items) grid.append(screenshotCard(video, item));
          group.append(grid);
        }
        screenshotBody.append(group);
      }
      renderSelectionSummary();
    }

    function openImagePreview(video, item) {
      const previewPanel = node("div", "preview-dialog image-preview screenshot-preview-dialog");
      previewPanel.append(node("h2", null, `${videoLabel(video)} · ${item.timestamp}`));
      const frame = node("div", "image-preview-frame");
      const image = document.createElement("img");
      image.alt = `${videoLabel(video)} ${item.timestamp}`;
      image.src = imageUrl(item);
      image.addEventListener("error", () => frame.append(node("p", "form-error", "图片加载失败")));
      frame.append(image);
      const download = document.createElement("a");
      download.className = "button primary";
      download.textContent = "下载原图";
      download.href = imageDownloadUrl(item);
      download.setAttribute("download", "");
      previewPanel.append(frame, node("p", "muted", item.relative_path || ""), download);
      showModal(previewPanel, {stack: true});
    }

    function renderTask(task, kind, active) {
      state.lastTaskKind=kind;
      taskSection.classList.remove("hidden");
      const result = task.result || {};
      taskBar.max = Math.max(1, Number(kind === "screenshot" ? result.image_total : result.total) || 1);
      taskBar.value = kind === "screenshot"
        ? Math.min(taskBar.max, Number(result.image_succeeded || 0) + Number(result.image_failed || 0))
        : Math.min(taskBar.max, Number(result.succeeded || 0) + Number(result.failed || 0));
      if (task.status === "queued") {
        taskTitle.textContent = `任务 #${task.id} 排队中`;
        taskDetail.textContent = "等待后台处理…";
      } else if (task.status === "running") {
        taskTitle.textContent = result.current ? `正在处理：${result.current}` : `任务 #${task.id} 运行中`;
        taskDetail.textContent = kind === "screenshot"
          ? `已生成 ${result.image_succeeded || 0} 张，失败 ${result.image_failed || 0} 张。`
          : `已完成 ${result.succeeded || 0} 个拼图，失败 ${result.failed || 0} 个。`;
      } else if (task.status === "cancelled") {
        taskTitle.textContent = "任务已取消";
        taskDetail.textContent = "已生成的截图会保留在会话中。";
      } else if (task.status === "failed") {
        taskTitle.textContent = "任务失败";
        taskDetail.textContent = task.error || "没有生成可用结果。";
      } else if (result.status === "partial") {
        taskTitle.textContent = "任务完成，部分项目失败";
        taskDetail.textContent = kind === "screenshot"
          ? `截图成功 ${result.image_succeeded || 0} 张，失败 ${result.image_failed || 0} 张。`
          : `拼图成功 ${result.succeeded || 0} 个，失败 ${result.failed || 0} 个。`;
      } else {
        taskTitle.textContent = "任务完成";
        taskDetail.textContent = kind === "screenshot"
          ? `已生成 ${result.image_succeeded || 0} 张独立截图。`
          : `已生成 ${result.succeeded || 0} 张拼图。`;
      }
      retryTask.hidden = !["failed", "cancelled"].includes(task.status);
      state.lastTaskId = task.id;
      taskCancel.classList.toggle("hidden", !active);
      taskLogsButton.classList.remove("hidden");
      if (kind === "screenshot_collage" || result.stage === "collage") renderCollageResults(task);
      if (["collage","upload"].includes(result.stage)) {
        taskBar.max=Math.max(1,Number(result.total)||1);taskBar.value=Number(result.succeeded || 0)+Number(result.failed || 0);
      }
      if (result.stage === "upload") taskDetail.textContent=`上传 ${result.succeeded || 0} / ${result.total || 0} · 失败 ${result.failed || 0}`;
      if (result.stage === "collage") taskDetail.textContent = `拼图 ${result.succeeded || 0} / ${result.total || 0} · ${statusLabel(task.status)}`;
    }

    async function loadSession() {
      if (!state.sessionId) return;
      state.session = await api(`/api/files/screenshots/${encodeURIComponent(state.sessionId)}`);
      if(!state.sectionConfigured) {
        const collage=state.session.params?.output_mode === "collage";
        screenshotSection.open=!collage;
        panel.insertBefore(collage ? collageSection : screenshotSection,collage ? screenshotSection : collageSection);
        state.sectionConfigured=true;
      }
      framesSummary.textContent=`独立帧与逐张编辑 · ${state.session.counts?.completed || 0} 张`;
      syncSelection();
      renderSourceSummary();
      renderScreenshots();
      renderSelectionSummary();
      const finals = state.session.collage_result?.items || Object.values(state.session.final_outputs || {});
      if (finals.length) renderCollageResults({result:{items:finals}});
    }

    async function pollTask() {
      if (!state.activeTask || state.polling) return;
      state.polling = true;
      const {id, kind} = state.activeTask;
      try {
        const task = await api(`/api/tasks/${id}`);
        renderTask(task, kind, !terminalTask(task));
        if (state.sessionId) {
          try {
            state.session = await api(`/api/files/screenshots/${encodeURIComponent(state.sessionId)}`);
        window.ToolUI?.storage.set("media-last-session", state.sessionId);
            syncSelection();
            renderSourceSummary();
            renderScreenshots();
          } catch {
            // Manifest replacement is atomic; ignore a transient read failure.
          }
        }
        if (terminalTask(task)) {
          state.activeTask = null;
          renderTask(task, kind, false);
          if (state.sessionId) await loadSession();
          renderSelectionSummary();
          document.dispatchEvent(new Event("app:files-refresh"));
          return;
        }
      } catch (error) {
        state.activeTask = null;
        taskTitle.textContent = "任务状态读取失败";
        taskDetail.textContent = error.message;
        taskCancel.classList.add("hidden");
      } finally {
        state.polling = false;
      }
      if (state.activeTask) {
        state.pollTimer = window.setTimeout(pollTask, 900);
      }
    }

    function startTask(task, kind) {
      state.lastTaskId = Number(task.id);
      state.activeTask = {id: Number(task.id), kind};
      taskLogs.classList.add("hidden");
      taskLogs.textContent = "";
      renderTask(task, kind, true);
      renderScreenshots();
      renderSelectionSummary();
      window.clearTimeout(state.pollTimer);
      state.pollTimer = window.setTimeout(pollTask, 120);
    }

    async function submitScreenshots() {
      const videos = state.session?.videos || [];
      const sources = videos.length ? videos.map(video => video.source) : state.initialSources;
      if (!sources.length) {
        setGlobalError("请至少选择一个视频");
        return;
      }
      if (sources.length > MAX_SOURCES) {
        setGlobalError(`单次最多处理 ${MAX_SOURCES} 个视频`);
        return;
      }
      setGlobalError("");
      setScreenshotError("");
      markBusy(startGeneration, true);
      try {
        const result = await api("/api/files/screenshots", {
          method: "POST",
          body: JSON.stringify({
            sources: sources.map(source => ({
              root_key: source.root_key,
              relative_path: source.relative_path,
              disc_selection: source.disc_selection || null
            })),
            output_mode: settingsValues().screenshot_output_mode || "single"

          })
        });
        state.sessionId = result.session_id;
        window.ToolUI?.storage.set("media-last-session",state.sessionId);
        state.selected.clear();
        state.knownStatuses.clear();
        await loadSession().catch(() => {});
        const params=state.session?.params || {};
        subtitle.textContent = `会话 ${result.session_id.slice(0,8)} · ${params.output_mode === "collage" ? "直接生成拼图" : "独立单图"} · 每个视频 ${params.total || "—"} 张。`;
        startTask(result.task, "screenshot");
      } catch (error) {
        setGlobalError(error.message);
      } finally {
        markBusy(startGeneration, false);
        renderSelectionSummary();
      }
    }

    async function uploadSelectedScreenshots() {
      const ids=(state.session?.videos || []).flatMap(video=>selectedItems(video).map(item=>String(item.id)));
      if(!ids.length)return;
      markBusy(uploadSelected,true);
      try {await queueUploads(ids);screenshotNotice.textContent="批量上传已加入队列，失败项目可独立重试。";screenshotNotice.classList.remove("hidden");}
      catch(error){setScreenshotError(error.message);}finally{markBusy(uploadSelected,false);renderSelectionSummary();}
    }
    async function queueUploads(ids, variant="styled") {
      const byId=new Map((state.session?.videos || []).flatMap(video=>video.items || []).map(item=>[String(item.id),item]));
      const items=ids.map(id=>byId.get(String(id))).filter(Boolean).map(item=>({root_key:"images",relative_path:variant==="raw"?(item.raw_relative_path || item.relative_path):item.relative_path,session_item_id:String(item.id)}));
      return window.ToolUI.startJob("uploads","/api/uploads/jobs",{items,session_id:state.sessionId},uploadTaskSection);
    }

    function renderConflicts(conflicts) {
      collageConflict.replaceChildren();
      collageConflict.append(node("strong", null, "发现拼图输出冲突，尚未开始生成"));
      const list = node("ul");
      for (const conflict of conflicts || []) {
        list.append(node("li", null, `${conflict.source_name}：${conflict.reason || "目标已存在"}`));
      }
      collageConflict.append(list);
      if (conflicts.some(conflict => conflict.hard)) {
        collageConflict.append(node("p", "form-error", "该冲突无法通过覆盖解决，请调整输出目录。"));
      } else {
        const confirm = button("覆盖已有拼图", "button danger small");
        confirm.addEventListener("click", () => submitCollage(true));
        collageConflict.append(confirm);
      }
      collageConflict.classList.remove("hidden");
    }

    async function submitCollage(overwriteConfirmed = false, videoIds = null) {
      if (!state.session) {
        setGlobalError("请先生成或打开截图会话");
        return;
      }
      const selections = [];
      for (const video of state.session.videos || []) {
        if(videoIds && !videoIds.includes(String(video.id))) continue;
        const items = selectedItems(video);
        if (!items.length) continue;
        selections.push({
          video_id: String(video.id),
          item_ids: items.map(item => String(item.id))
        });
      }
      if (!selections.length) {
        setGlobalError("请至少勾选一张截图");
        return;
      }
      setGlobalError("");
      collageConflict.classList.add("hidden");
      markBusy(generateCollage, true);
      try {
        const result = await api(`/api/files/screenshots/${encodeURIComponent(state.sessionId)}/collage`, {
          method: "POST",
          body: JSON.stringify({
            selections,
            format: settingsValues().screenshot_default_format || "png",
            layout_mode: state.collage.layoutMode,
            columns: state.collage.columns,
            rows: state.collage.layoutMode === "custom" ? state.collage.rows : null,
            thumbnail_width: state.collage.width,
            gap: state.collage.gap,
            output_relative_path: state.collage.outputRelativePath,
            overwrite: overwriteConfirmed || state.collage.overwrite
          })
        });
        if(["failed","cancelled"].includes(result.task.status)) {const retried=await api(`/api/tasks/${result.task.id}/retry`,{method:"POST"});startTask(retried.task,"screenshot_collage");}
        else startTask(result.task, "screenshot_collage");
      } catch (error) {
        if (error.status === 409 && error.data?.detail?.code === "screenshot_collage_conflicts") {
          renderConflicts(error.data.detail.conflicts || []);
        } else {
          setGlobalError(error.message);
        }
      } finally {
        markBusy(generateCollage, false);
        renderSelectionSummary();
      }
    }

    function renderCollageResults(task) {
      const items = task.result?.items || [];
      if (!items.length) return;
      collageResults.replaceChildren(node("h3", null, "拼图结果"));
      for (const item of items) {
        const row = node("article", `collage-result ${item.status === "failed" ? "failed" : ""}`);
        const copy = node("div", "collage-result-copy");
        copy.append(node("strong", null, item.source_name || "视频"));
        if (item.status === "completed") {
          copy.append(node("p", "muted", `→ ${item.output_name} · ${formatBytes(item.output_size_bytes)}`));
          copy.append(node("p", "muted", `使用截图：${(item.sample_timestamps || []).join("、")}`));
          const preview = document.createElement("img");
          preview.className = "collage-result-image";
          preview.loading = "lazy";
          preview.alt = item.output_name || "拼图结果";
          preview.src = `/api/files/image?root_key=${encodeURIComponent(item.output_root_key || "images")}&path=${encodeURIComponent(item.output_relative_path || "")}&v=${Date.now()}`;
          const download = node("a", "button secondary small", "下载拼图");
          download.href = imageDownloadUrl({root_key:item.output_root_key,relative_path:item.output_relative_path}); download.download="";
          const send = button("上传拼图");
          send.addEventListener("click", async () => {
            markBusy(send,true);
            try {await window.ToolUI.startJob("uploads", "/api/uploads/jobs", {session_id:state.sessionId,items:[{root_key:item.output_root_key || "images",relative_path:item.output_relative_path,session_item_id:"collage:"+item.video_id}]},uploadTaskSection);}
            catch(error){setGlobalError(error.message);}finally{markBusy(send,false);}
          });
          preview.addEventListener("click", () => openImagePreview({source:{name:item.source_name}},{relative_path:item.output_relative_path,timestamp:"拼图"}));
          copy.append(download,send);
          row.append(copy, preview);
        } else {
          copy.append(node("p", "form-error", item.error || "拼图失败"));
          const retry=button("重试此拼图");retry.addEventListener("click",()=>submitCollage(false,[String(item.video_id)]));copy.append(retry);
          row.append(copy);
        }
        collageResults.append(row);
      }
    }

    refreshSession.addEventListener("click", async () => {
      markBusy(refreshSession, true);
      setGlobalError("");
      try {
        await loadSession();
      } catch (error) {
        setGlobalError(error.message);
      } finally {
        markBusy(refreshSession, false);
      }
    });
    uploadSelected.addEventListener("click", uploadSelectedScreenshots);
    startGeneration.addEventListener("click", submitScreenshots);
    generateCollage.addEventListener("click", () => submitCollage(false));

    taskCancel.addEventListener("click", async () => {
      if (!state.activeTask) return;
      markBusy(taskCancel, true);
      try {
        await api(`/api/tasks/${state.activeTask.id}/cancel`, {method: "POST"});
        taskTitle.textContent = "正在取消任务…";
      } catch (error) {
        setGlobalError(error.message);
      } finally {
        markBusy(taskCancel, false);
      }
    });
    taskLogsButton.addEventListener("click", async () => {
      const taskId = state.activeTask?.id || state.lastTaskId;
      if (!taskId) return;
      markBusy(taskLogsButton, true);
      try {
        const response = await api(`/api/tasks/${taskId}/logs?lines=200`);
        taskLogs.textContent = response.content || "暂无日志";
        taskLogs.classList.remove("hidden");
      } catch (error) {
        taskLogs.textContent = error.message;
        taskLogs.classList.remove("hidden");
      } finally {
        markBusy(taskLogsButton, false);
      }
    });

    renderSourceSummary();
    renderSelectionSummary();
    if (sessionId) {
      loadSession().then(async () => {
        if (state.session.collage_task_id || state.session.task_id) {
          const task = await api(`/api/tasks/${state.session.collage_task_id || state.session.task_id}`);
          state.lastTaskId = task.id;
          if (!terminalTask(task)) startTask(task, task.task_type || "screenshot");
          else renderTask(task, task.task_type || "screenshot", false);
        }
      }).catch(error => setGlobalError(error.message));
    } else if (initialSources.length) { submitScreenshots(); }
  }

  async function openRecent() {
    const panel = node("div", "collage-dialog screenshot-sessions-dialog");
    panel.append(node("p", "eyebrow", "SCREENSHOT SESSIONS"));
    panel.append(node("h2", null, "最近截图会话"));
    panel.append(node("p", "muted", "会话保存在服务端，刷新页面或重启容器后仍可重新打开并继续拼图。"));
    const status = node("p", "muted", "正在读取会话…");
    panel.append(status);
    const list = node("div", "screenshot-session-list");
    panel.append(list);
    const footer = node("div", "dialog-actions");
    const close = button("关闭", "button ghost");
    footer.append(close);
    panel.append(footer);
    showModal(panel);
    close.addEventListener("click", () => closeModal());
    try {
      const response = await api("/api/files/screenshots/sessions?limit=50");
      const sessions = response.sessions || [];
      status.textContent = sessions.length ? `共 ${sessions.length} 个会话` : "还没有截图会话";
      list.replaceChildren();
      for (const session of sessions) {
        const row = node("article", "screenshot-session-row");
        const copy = node("div");
        copy.append(node("strong", null, `会话 ${session.id.slice(0, 8)} · ${statusLabel(session.status)}`));
        copy.append(node("p", "muted", `${session.source_count} 个视频 · ${session.image_count} 张截图 · 失败 ${session.failed_count} 张`));
        copy.append(node("p", "muted", `${formatDate(session.updated_at)} · ${session.output_relative_path || "图片目录根目录"}`));
        const open = button("打开会话", "button primary small");
        open.addEventListener("click", () => {
          closeModal();
          window.setTimeout(() => openSession(session.id), 0);
        });
        row.append(copy, open);
        list.append(row);
      }
    } catch (error) {
      status.className = "form-error";
      status.textContent = error.message;
    }
  }

  window.CollageTool = {open, openSession, openRecent, mount:(host,items,sessionId)=>createWorkbench(normalizeSources(items,"downloads"),sessionId,host)};
})();
