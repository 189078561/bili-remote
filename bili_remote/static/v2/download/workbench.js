(() => {
  "use strict";

  const DEFAULT_FORMAT = "bv*+ba/b";
  const formatModes = new Set(["avc", "hevc", "av1", "any"]);
  const ranges = new Set(["SDR", "HDR10", "DV", "HLG", "any"]);

  function createApi() {
    return async (url, options = {}) => {
      const method = (options.method || "GET").toUpperCase();
      const headers = new Headers(options.headers || {});
      if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
      if (!["GET", "HEAD"].includes(method)) {
        const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
        if (csrf) headers.set("X-CSRF-Token", csrf);
      }
      const response = await fetch(url, {cache: "no-store", ...options, method, headers});
      let data = {};
      try { data = await response.json(); } catch { /* an empty response is handled below */ }
      if (!response.ok) throw new Error(data.detail?.message || (typeof data.detail === "string" ? data.detail : `请求失败 (${response.status})`));
      return data;
    };
  }

  function codecName(value) {
    const text = String(value || "").toLowerCase();
    if (text.includes("avc")) return "H.264";
    if (text.includes("hev") || text.includes("hvc")) return "H.265";
    if (text.includes("av01")) return "AV1";
    if (text.includes("mp4a")) return "AAC";
    if (text.includes("opus")) return "Opus";
    if (text.includes("ac-3") || text.includes("ec-3")) return "Dolby";
    return "未知编码";
  }

  function formatLabel(item) {
    const bits = [
      item.height ? `${item.height}P` : "仅音频",
      item.fps ? `${Math.round(Number(item.fps))}fps` : null,
      item.vcodec && item.vcodec !== "none" ? codecName(item.vcodec) : codecName(item.acodec),
      item.dynamic_range && item.dynamic_range !== "SDR" ? item.dynamic_range : null,
      item.format_note,
      item.tbr ? `${Math.round(Number(item.tbr))} kbps` : null,
      item.filesize ? `${(Number(item.filesize) / 1048576).toFixed(1)} MiB` : null
    ].filter(Boolean);
    return `${bits.join(" · ")} [${item.format_id || item.selector}]`;
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function elapsed(seconds) {
    const value = Math.max(0, Math.floor(Number(seconds) || 0));
    if (value < 60) return `${value} 秒`;
    if (value < 3600) return `${Math.floor(value / 60)} 分 ${value % 60} 秒`;
    return `${Math.floor(value / 3600)} 小时 ${Math.floor(value % 3600 / 60)} 分`;
  }

  function size(bytes) {
    const value = Number(bytes) || 0;
    if (value >= 1073741824) return `${(value / 1073741824).toFixed(2)} GiB`;
    if (value >= 1048576) return `${(value / 1048576).toFixed(1)} MiB`;
    if (value >= 1024) return `${(value / 1024).toFixed(0)} KiB`;
    return `${value} B`;
  }

  function modeLabel(mode) { return mode === "tv" ? "TV" : "Web"; }
  function statusLabel(status) {
    return ({queued: "排队中", running: "下载中", completed: "已完成", failed: "失败", auth_required: "需要登录", cancelled: "已取消"})[status] || status || "状态未知";
  }

  function mount(root, options = {}) {
    if (!root) throw new TypeError("download workbench root is required");
    const api = options.api || createApi();
    const $ = (selector) => root.querySelector(selector);
    const state = {resolved: null, resolvedUrl: "", jobs: [], previousStatuses: new Map(), seenInitialJobs: false, timer: null, formatProfiles: [], formatRequest: 0, parseVersion: 0};
    const form = $("[data-download-form]");
    const urlField = $("#dv2-url");
    const modeField = $("[data-mode]");
    const formatField = $("[data-format]");
    const parseState = $("[data-parse-state]");
    const errorBox = $("[data-error]");
    const resolution = $("[data-resolution]");
    const episodesBox = $("[data-episodes]");
    const episodeList = $("[data-episode-list]");
    const selectedCount = $("[data-selected-count]");
    const jobsHost = $("[data-jobs]");
    const notice = $("[data-notice]");
    const formatHint = $("[data-format-hint]");

    function showError(message) {
      errorBox.textContent = message || "操作失败，请稍后重试。";
      errorBox.hidden = false;
      parseState.textContent = "需要处理";
    }
    function clearError() { errorBox.textContent = ""; errorBox.hidden = true; }
    function selectedIds() { return [...episodeList.querySelectorAll("input:checked")].map((input) => input.value); }
    function updateCount() {
      const count = selectedIds().length;
      selectedCount.textContent = `已选 ${count} 集`;
      $("[data-check-batch]").hidden = count < 2;
      if (count !== 1) formatField.value = DEFAULT_FORMAT;
      if (count > 1) formatHint.textContent = `批量下载 ${count} 集；已选画质偏好会应用到每集。`;
      else if (count === 1) formatHint.textContent = "已选 1 集；提交时使用该集可用格式。";
      else formatHint.textContent = "请选择剧集，或使用默认格式下载单个视频。";
      state.formatRequest += 1;
      const selected = episodeList.querySelector("input:checked");
      if (count === 1 && selected) loadSelectedEpisodeFormat(selected.value, state.formatRequest);
    }
    function renderEpisodes(data) {
      episodeList.replaceChildren();
      const episodes = Array.isArray(data.episodes) ? data.episodes : [];
      episodesBox.hidden = episodes.length === 0;
      const groups = new Map();
      for (const episode of episodes) {
        const group = episode.group || "其他";
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(episode);
      }
      for (const [groupName, grouped] of groups) {
        const group = make("div", "dv2-episode-group");
        group.dataset.group = groupName;
        const heading = make("strong", "", `${groupName} · ${grouped.length} 集`);
        group.append(heading);
        for (const episode of grouped) {
          const label = make("label", "dv2-episode");
          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.value = String(episode.id);
          checkbox.dataset.group = groupName;
          checkbox.checked = String(episode.id) === String(data.default_episode_id || "");
          checkbox.addEventListener("change", updateCount);
          const title = episode.title || `第 ${episode.group_index || episode.index || "?"} 集`;
          const text = make("span", "", [title, episode.badge, episode.duration ? elapsed(episode.duration) : null].filter(Boolean).join(" · "));
          label.append(checkbox, text);
          group.append(label);
        }
        episodeList.append(group);
      }
      updateCount();
    }
    function renderFormats(formats) {
      formatField.replaceChildren(new Option("最高可用视频 · 最佳音轨", DEFAULT_FORMAT));
      const ordered = [...(formats || [])].filter((item) => item.selector).sort((a, b) => (Number(b.height) || 0) - (Number(a.height) || 0) || (Number(b.fps) || 0) - (Number(a.fps) || 0));
      state.formatProfiles = [];
      const groups = new Map();
      for (const item of ordered) {
        const group = item.height ? `${item.height}P` : "音轨";
        if (!groups.has(group)) groups.set(group, []);
        groups.get(group).push(item);
      }
      for (const [label, items] of groups) {
        const optgroup = document.createElement("optgroup");
        optgroup.label = label;
        for (const item of items) optgroup.append(new Option(formatLabel(item), item.selector));
        formatField.append(optgroup);
      }
      formatField.dataset.formats = JSON.stringify(ordered);
      formatHint.textContent = ordered.length ? `列出 ${ordered.length} 种可用格式；音视频编码与音轨信息来自解析结果。` : "当前没有额外格式信息，将使用最高可用画质。";
    }
    function renderBatchProfiles(profiles, meta = {}) {
      formatField.replaceChildren(new Option("最高可用画质 · 最佳音轨", DEFAULT_FORMAT));
      state.formatProfiles = (profiles || []).map((item) => item.profile || {
        height: Number(item.height), fps_high: item.fps_high, vcodec: item.vcodec,
        dynamic_range: item.dynamic_range
      });
      state.formatProfiles.forEach((profile, index) => {
        const coverage = Number(profiles[index].coverage);
        const total = Number(meta.selected_count) || selectedIds().length;
        const coverageText = Number.isFinite(coverage) ? ` · ${coverage}/${meta.checked || total} 集可用` : "";
        const codec = String(profile.vcodec || "any");
        const label = `${profile.height}P · ${codec === "hevc" ? "H.265" : codec === "avc" ? "H.264" : codec.toUpperCase()}${profile.fps_high ? " · 高帧率" : ""}${profile.dynamic_range && profile.dynamic_range !== "SDR" ? ` · ${profile.dynamic_range}` : ""}${coverageText}`;
        formatField.append(new Option(label, `profile:${index}`));
      });
      formatField.dataset.formats = "[]";
      const checked = Number(meta.checked) || 0;
      const failed = Number(meta.failed) || 0;
      formatHint.textContent = `已检测 ${checked}/${Number(meta.selected_count) || selectedIds().length} 集${meta.sampled ? "（抽样）" : ""}${failed ? ` · ${failed} 集检测失败` : ""}；所选画质缺失时由下载引擎处理。`;
    }
    async function loadSelectedEpisodeFormat(episodeId, requestId) {
      const episode = state.resolved?.episodes?.find((item) => String(item.id) === String(episodeId));
      if (!episode) return;
      const url = episode.url || `https://www.bilibili.com/bangumi/play/ep${episode.id}`;
      try {
        const result = await api("/api/episodes/formats", {
          method: "POST",
          body: JSON.stringify({url, api_mode: state.resolved.api_mode || modeField.value})
        });
        if (requestId !== state.formatRequest || selectedIds().length !== 1) return;
        renderFormats(result.formats || []);
        formatHint.textContent = result.formats?.length ? `可用格式来自所选剧集「${episode.title || episode.id}」。` : "该集未返回专属格式，将使用最高可用画质。";
      } catch (error) {
        if (requestId === state.formatRequest) formatHint.textContent = `该集格式读取失败，仍可使用默认画质：${error.message}`;
      }
    }
    async function checkBatchFormats() {
      const ids = selectedIds();
      if (!state.resolved?.episodes?.length || ids.length < 2) return;
      const button = $("[data-check-batch]");
      const requestId = ++state.formatRequest;
      button.disabled = true;
      button.textContent = "检测中…";
      try {
        const result = await api("/api/episodes/formats/batch", {
          method: "POST",
          body: JSON.stringify({url: state.resolvedUrl || urlField.value.trim(), episode_ids: ids, api_mode: state.resolved.api_mode || modeField.value})
        });
        if (requestId !== state.formatRequest) return;
        renderBatchProfiles(result.profiles || [], result);
      } catch (error) {
        if (requestId === state.formatRequest) formatHint.textContent = `画质检测失败，继续使用解析结果：${error.message}`;
      } finally {
        button.disabled = false;
        button.textContent = "检测所选画质";
      }
    }
    async function parse() {
      clearError();
      const url = urlField.value.trim();
      if (!url) { showError("请先粘贴 B 站链接。"); urlField.focus(); return; }
      const request=++state.parseVersion, mode=modeField.value;
      state.resolved=null; resolution.hidden=true; ++state.formatRequest;
      const button = $("[data-parse]");
      button.disabled = true;
      button.textContent = "解析中…";
      parseState.textContent = "正在读取视频与剧集信息";
      try {
        const result = await api("/api/resolve", {method: "POST", body: JSON.stringify({url, api_mode: mode})});
        if(request!==state.parseVersion || url!==urlField.value.trim() || mode!==modeField.value)return;
        state.resolved = result;
        state.resolvedUrl = result.webpage_url || url;
        const cover=$("[data-cover]");
        if(cover){cover.hidden=true;cover.removeAttribute('src');try{const image=new URL(result.thumbnail||result.cover);if(['http:','https:'].includes(image.protocol)&&!image.username&&!image.password){cover.src=image.href;cover.hidden=false;cover.onerror=()=>{cover.hidden=true;};}}catch{}}
        $("[data-title]").textContent = result.title || "已解析内容";
        $("[data-summary]").textContent = [result.uploader, result.duration ? elapsed(result.duration) : null, result.playlist_count ? `${result.playlist_count} 项` : null, modeLabel(result.api_mode || modeField.value)].filter(Boolean).join(" · ");
        $("[data-template]").value = result.episodes?.length ? (options.playlistTemplate || options.outputTemplate || "") : (options.singleTemplate || options.outputTemplate || "");
        resolution.hidden = false;
        renderEpisodes(result);
        renderFormats(result.formats);
        const allParts = $(`[data-all-parts]`);
        allParts.hidden = Boolean(result.episodes?.length) || modeField.value === "tv";
        if (result.warning) formatHint.textContent = result.warning;
        parseState.textContent = "解析完成";
      } catch (error) {
        if(request!==state.parseVersion)return;
        state.resolved = null;
        resolution.hidden = true;
        showError(error.message);
      } finally {
        if(request===state.parseVersion){button.disabled = false;button.textContent = "解析链接";}
      }
    }
    function setSelection(method) {
      const checks = [...episodeList.querySelectorAll("input[type=checkbox]")];
      if (method === "all") checks.forEach((item) => { item.checked = true; });
      else if (method === "none") checks.forEach((item) => { item.checked = false; });
      else if (method === "invert") checks.forEach((item) => { item.checked = !item.checked; });
      else if (method === "main") checks.forEach((item) => { item.checked = item.dataset.group === "正片"; });
      updateCount();
    }
    function makeProfile() {
      const chosen = formatField.value;
      if (chosen.startsWith("profile:")) return state.formatProfiles[Number(chosen.slice(8))] || null;
      const selected = JSON.parse(formatField.dataset.formats || "[]").find((item) => item.selector === chosen);
      if (!selected?.height || selected.height < 144) return null;
      let vcodec = "any";
      const codec = String(selected.vcodec || "").toLowerCase();
      if (codec.includes("avc")) vcodec = "avc";
      else if (codec.includes("hev") || codec.includes("hvc")) vcodec = "hevc";
      else if (codec.includes("av01")) vcodec = "av1";
      const range = String(selected.dynamic_range || "SDR").toUpperCase();
      return {
        height: Number(selected.height),
        fps_high: selected.fps == null ? null : Number(selected.fps) >= 50,
        vcodec: formatModes.has(vcodec) ? vcodec : "any",
        dynamic_range: ranges.has(range) ? range : "any"
      };
    }
    async function submit(event) {
      event.preventDefault();
      clearError();
      if (!state.resolved) { showError("请先解析链接。"); return; }
      const url = state.resolvedUrl || urlField.value.trim();
      const ids = selectedIds();
      const template = $("[data-template]").value.trim();
      const outputDir = $("[data-output]").value.trim();
      const submitButton = form.querySelector('button[type="submit"]');
      submitButton.disabled = true;
      try {
        if (state.resolved.episodes?.length) {
          if (!ids.length) throw new Error("请至少选择一集。");
          if (ids.length > 600) throw new Error("单次最多创建 600 个任务。");
          const payload = {url, episode_ids: ids, api_mode: state.resolved.api_mode || modeField.value, format_selector: formatField.value, output_template: template, output_dir: outputDir};
          if (ids.length > 1) payload.format_profile = makeProfile();
          await api("/api/jobs/batch", {method: "POST", body: JSON.stringify(payload)});
        } else {
          await api("/api/jobs", {method: "POST", body: JSON.stringify({url, api_mode: modeField.value, format_selector: formatField.value, all_parts: $(`[data-all-parts] input`).checked, output_template: template, output_dir: outputDir, title: state.resolved.title || null, uploader: state.resolved.uploader || null})});
        }
        notice.textContent = "任务已加入现有下载队列；完成后可进入工作台识别并预览命名。";
        await refreshJobs();
      } catch (error) { showError(error.message); }
      finally { submitButton.disabled = false; }
    }
    function jobCard(job) {
      const card = make("article", "dv2-job");
      card.dataset.jobId = String(job.id);
      const head = make("div", "dv2-job-head");
      head.append(make("strong", "dv2-job-title", job.title || "等待解析"), make("span", "dv2-badge", `${modeLabel(job.api_mode)} · ${statusLabel(job.status)}`));
      card.append(head);
      const metaParts = [`任务 #${job.id}`, job.progress == null ? null : `${Math.max(0, Math.min(100, Math.round(Number(job.progress) || 0)))}%`, job.speed ? `${size(job.speed)}/s` : null, job.eta == null ? null : `剩余 ${elapsed(job.eta)}`, job.total_bytes ? `${size(job.downloaded_bytes)} / ${size(job.total_bytes)}` : null];
      card.append(make("div", "dv2-job-meta", metaParts.filter(Boolean).join(" · ")));
      const track = make("div", "dv2-progress");
      const bar = document.createElement("span");
      const percent = Math.max(0, Math.min(100, Number(job.progress) || 0));
      bar.style.width = `${job.status === "completed" ? 100 : percent}%`;
      track.append(bar); card.append(track);
      if (job.error) card.append(make("div", "dv2-job-error", job.error));
      const actions = make("div", "dv2-job-actions");
      if (["queued", "running"].includes(job.status)) addAction(actions, "取消", () => taskAction(job, "cancel"), "is-danger");
      if (["failed", "cancelled", "auth_required"].includes(job.status)) addAction(actions, "重试", () => taskAction(job, "retry"));
      addAction(actions, "查看日志", () => showLog(job.id));
      if (job.status === "completed" && options.onOpenFile) addAction(actions, "进入工作台", async () => {
        try { await options.onOpenFile(job); } catch (error) { showError(error.message); }
      });
      if(job.status==='completed' && options.onOpenDirectory) addAction(actions,'打开文件管理',()=>options.onOpenDirectory(job));
      card.append(actions);
      return card;
    }
    function addAction(parent, text, callback, extraClass = "") {
      const button = make("button", extraClass, text); button.type = "button"; button.addEventListener("click", async()=>{button.disabled=true;try{await callback();}catch(error){showError(error.message);}finally{button.disabled=false;}}); parent.append(button);
    }
    async function taskAction(job, action) {
      clearError();
      try { await api(`/api/jobs/${job.id}/${action}`, {method: "POST"}); await refreshJobs({force:true}); }
      catch (error) { showError(error.message); }
    }
    function alertTransition(job) {
      const key = `${job.id}`;
      const old = state.previousStatuses.get(key);
      state.previousStatuses.set(key, job.status);
      if (old && old !== job.status && ["completed", "failed", "auth_required"].includes(job.status)) {
        const message = `任务 #${job.id}：${statusLabel(job.status)}`;
        notice.textContent = message;
        if (job.status === "completed" && typeof options.onCompleted === "function") options.onCompleted(job);
        if (window.Notification?.permission === "granted") new Notification("B站下载", {body: message});
      }
    }
    async function refreshJobs({force=false} = {}) {
      try {
        const jobs = await api("/api/jobs?limit=500");
        if(!force && jobsHost.contains(document.activeElement)){state.jobs=jobs||[];return;}
        jobsHost.replaceChildren();
        if (!Array.isArray(jobs) || !jobs.length) jobsHost.append(make("p", "dv2-empty", "暂无下载任务"));
        else for (const job of jobs) { jobsHost.append(jobCard(job)); if (state.seenInitialJobs) alertTransition(job); else state.previousStatuses.set(String(job.id), job.status); }
        state.seenInitialJobs = true;
        state.jobs = jobs || [];
      } catch (error) { notice.textContent=`队列读取失败：${error.message}；保留上次结果。`;if(!state.jobs.length)jobsHost.replaceChildren(make("p", "dv2-empty", error.message)); }
    }
    async function showLog(jobId) {
      try {
        const result = await api(`/api/jobs/${jobId}/log?lines=160`);
        $(`[data-log-content]`).textContent = result.content || "暂无日志内容";
        const dialog = $(`[data-log-dialog]`);
        if (typeof dialog.showModal === "function") dialog.showModal();
        else dialog.setAttribute("open", "");
      } catch (error) { showError(error.message); }
    }
    async function refreshHistory() {
      const host = $(`[data-history]`);
      try {
        const items = await api("/api/history?limit=200");
        host.replaceChildren();
        $(`[data-history-count]`).textContent = `${items.length} 条`;
        if (!items.length) host.append(make("p", "dv2-empty", "暂无解析历史"));
        for (const item of items) {
          const row = make("div", "dv2-history-item");
          const summary = make("div", "", `${item.title || "未命名内容"} · ${modeLabel(item.api_mode)} · ${item.updated_at || ""}`);
          const restore = make("button", "", "重新解析"); restore.type = "button";
          restore.addEventListener("click", async () => {
            try {
              const detail = await api(`/api/history/${item.id}`);
              urlField.value = detail.source_url || ""; modeField.value = detail.api_mode || "web"; await parse();
            } catch (error) { showError(error.message); }
          });
          row.append(summary, restore); host.append(row);
        }
      } catch (error) { host.replaceChildren(make("p", "dv2-empty", error.message)); }
    }
    async function refreshAccount() {
      try {
        const status = await api("/api/bili/status");
        const loggedIn = Boolean(status.logged_in);
        const tvLoggedIn = Boolean(status.tv_logged_in);
        $(`[data-account-dot]`).classList.toggle("is-ready", loggedIn || tvLoggedIn);
        $(`[data-account-status]`).textContent = `Web ${loggedIn ? "已登录" : "未登录"} · TV ${tvLoggedIn ? "已登录" : "未登录"}`;
        $(`[data-login-web]`).hidden = loggedIn;
        $(`[data-login-tv]`).hidden = tvLoggedIn;
        $(`[data-logout-web]`).hidden = !loggedIn;
        $(`[data-logout-tv]`).hidden = !tvLoggedIn;
      } catch { $(`[data-account-status]`).textContent = "登录状态暂不可用"; }
    }

    $("[data-parse]").addEventListener("click", parse);
    urlField.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); parse(); } });
    urlField.addEventListener('input',()=>{++state.parseVersion;++state.formatRequest;state.resolved=null;resolution.hidden=true;parseState.textContent='等待解析';$("[data-parse]").disabled=false;$("[data-parse]").textContent='解析链接';});
    form.addEventListener("submit", submit);
    modeField.addEventListener("change", () => { ++state.parseVersion; if (urlField.value.trim()) parse(); });
    $("[data-select-main]").addEventListener("click", () => setSelection("main"));
    $("[data-select-all]").addEventListener("click", () => setSelection("all"));
    $("[data-select-invert]").addEventListener("click", () => setSelection("invert"));
    $("[data-select-none]").addEventListener("click", () => setSelection("none"));
    $("[data-check-batch]").addEventListener("click", checkBatchFormats);
    $("[data-refresh]").addEventListener("click", ()=>refreshJobs({force:true}));
    $("[data-history-refresh]").addEventListener("click", refreshHistory);
    $("[data-pick-folder]").addEventListener("click", async () => {
      if (typeof options.onChooseDirectory !== "function") return;
      try {
        const selected = await options.onChooseDirectory($("[data-output]").value.trim());
        if (typeof selected === "string") $("[data-output]").value = selected;
        else if (selected && typeof selected.relative_path === "string") $("[data-output]").value = selected.relative_path;
      } catch (error) { showError(error.message); }
    });
    $("[data-login-web]").addEventListener("click", () => options.onLogin?.("web"));
    $("[data-login-tv]").addEventListener("click", () => options.onLogin?.("tv"));
    $("[data-logout-web]").addEventListener("click", async () => {
      try { await api("/api/bili/logout", {method: "POST"}); await refreshAccount(); }
      catch (error) { showError(error.message); }
    });
    $("[data-logout-tv]").addEventListener("click", async () => {
      try { await api("/api/bili/tv/logout", {method: "POST"}); await refreshAccount(); }
      catch (error) { showError(error.message); }
    });
    $("[data-log-dialog] form").addEventListener("submit", () => $("[data-log-dialog]").removeAttribute("open"));

    refreshAccount(); refreshJobs(); refreshHistory();
    state.timer = window.setInterval(refreshJobs, options.pollInterval || 5000);
    return {
      state,
      parse,
      refreshJobs,
      refreshHistory,
      destroy() { if (state.timer) window.clearInterval(state.timer); }
    };
  }

  window.BiliDownloadV2 = Object.freeze({mount});
})();
