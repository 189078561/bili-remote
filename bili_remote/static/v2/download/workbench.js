(() => {
  "use strict";

  const EPISODES_PER_PAGE = 50;
  const CATEGORIES = ["正片", "花絮", "预告"];
  const HISTORY_STATUSES = new Set(["completed", "failed", "cancelled", "auth_required"]);
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
    return ({queued: "排队中", downloading: "下载中", merging: "合并中", running: "下载中", completed: "已完成", failed: "失败", auth_required: "需要登录", cancelled: "已取消"})[status] || status || "状态未知";
  }
  function displayStatus(job) {
    if (job.display_status) return String(job.display_status);
    if (job.status === "running") return Number(job.progress) >= 99.5 ? "merging" : "downloading";
    return job.status || "unknown";
  }
  function dateTimeLabel(value) {
    if (!value) return "时间未知";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", {hour12: false});
  }
  function durationSeconds(job) {
    const explicit = Number(job.download_duration_seconds);
    if (job.download_duration_seconds != null && Number.isFinite(explicit) && explicit >= 0) return Math.floor(explicit);
    const started = Date.parse(job.started_at || "");
    const finished = Date.parse(job.finished_at || "");
    if (!Number.isFinite(started) || !Number.isFinite(finished) || finished < started) return null;
    return Math.floor((finished - started) / 1000);
  }

  function episodeCategory(episode) {
    const group = String(episode.group || "").trim();
    if (!group || ["正片", "分P", "分 P", "合集"].includes(group)) return "正片";
    if (/预告|先导|trailer|teaser/i.test(group)) return "预告";
    if (/花絮|幕后|特辑|bonus/i.test(group)) return "花絮";
    return /预告|先导|trailer|teaser/i.test(episode.title || "") ? "预告" : "花絮";
  }

  function mount(root, options = {}) {
    if (!root) throw new TypeError("download workbench root is required");
    const api = options.api || createApi();
    const $ = (selector) => root.querySelector(selector);
    const state = {
      resolved: null, resolvedUrl: "", jobs: [], previousStatuses: new Map(), seenInitialJobs: false,
      timer: null, refreshInFlight: null, refreshQueued: false, refreshAbort: null, destroyed: false,
      jobCards: new Map(), listeners: [], logScrollY: 0, logReturnFocus: null,
      episodeFormats: new Map(), episodeChoices: new Map(), formatProfiles: [], formatRequest: 0,
      parseVersion: 0, episodesOpen: false, submitting: false,
      selected: new Set(), categories: new Map(), category: null, activeEpisodeId: null,
      selectionsBySource: new Map(),
      namingNumbers: new Map(), namingRequest: 0, namingTimer: null, namingAbort: null
    };
    const form = $("[data-download-form]");
    const urlField = $("#dv2-url");
    const modeField = $("[data-mode]");
    const formatField = $("[data-format]");
    const parseState = $("[data-parse-state]");
    const errorBox = $("[data-error]");
    const resolution = $("[data-resolution]");
    const episodesBox = $("[data-episodes]");
    const episodeHeading = $("[data-episode-heading]");
    const episodeToggle = $("[data-episode-toggle]");
    const episodeList = $("[data-episode-list]");
    const selectAll = $("[data-select-all]");
    const selectedCount = $("[data-selected-count]");
    const jobsHost = $("[data-jobs]");
    const jobsHistoryHost = $("[data-download-history]");
    const notice = $("[data-notice]");
    const formatHint = $("[data-format-hint]");
    const logDialog = $("[data-log-dialog]");

    function listen(target, type, handler, options) {
      if (!target) return;
      target.addEventListener(type, handler, options);
      state.listeners.push([target, type, handler, options]);
    }
    function pageVisible() {
      if (state.destroyed || document.hidden || !root.isConnected) return false;
      return !root.closest(".app-view")?.classList.contains("hidden");
    }
    function clearRefreshTimer() {
      if (state.timer) window.clearTimeout(state.timer);
      state.timer = null;
    }

    function stage(name) {
      root.dataset.stage=name;
      $('[data-idle]').hidden = !!state.resolved || name==='parsing';
    }
    function summary() {
      const count=state.resolved?.episodes?.length ? selectedIds().length : state.resolved ? 1 : 0;
      $('[data-summary-count]').textContent = state.resolved?.episodes?.length ? `${count} / ${state.resolved.episodes.length} 集` : `${count} 个视频`;
      const customized = selectedIds().filter(id=>state.episodeChoices.has(id)).length;
      $('[data-summary-quality]').textContent = customized ? `${customized} 集单独设置，其余跟随统一画质` : formatField.selectedOptions[0]?.textContent || '最高可用';
      $('[data-summary-output]').textContent = $('[data-output]').value.trim() || '下载目录';
      $('[data-submit-summary]').textContent = `已选 ${count} ${state.resolved?.episodes?.length ? '集' : '个视频'}`;
      form.querySelector('button[type=submit]').textContent = state.submitting ? '正在提交…' : `开始下载${count ? ` · ${count} ${state.resolved?.episodes?.length ? '集' : '项'}` : ''}`;
    }
    function showError(message) {
      errorBox.textContent = message || "操作失败，请稍后重试。";
      errorBox.hidden = false;
      parseState.textContent = "需要处理";
      stage(state.resolved ? "selecting" : "failed");
    }
    function clearError() { errorBox.textContent = ""; errorBox.hidden = true; }
    function setEpisodesOpen(open) {
      state.episodesOpen = Boolean(open) && Boolean(state.resolved?.episodes?.length);
      episodesBox.hidden = !state.episodesOpen;
      episodeHeading.hidden = !state.resolved?.episodes?.length;
      episodeToggle.hidden = !state.resolved?.episodes?.length;
      episodeToggle.setAttribute("aria-expanded", String(state.episodesOpen));
    }
    function selectedIds() {
      const ids = new Set((state.resolved?.episodes || []).map(episode => String(episode.id)));
      return [...ids].filter(id => state.selected.has(id));
    }
    function allPartsSelected() {
      return !state.resolved?.episodes?.length && !$("[data-all-parts]").hidden && $("[data-all-parts] input").checked;
    }
    function namingOptions() {
      const number = $("[data-naming-number]");
      const batch = Boolean(state.resolved?.episodes?.length);
      return {
        mode: $("[data-naming-mode]").value,
        title: $("[data-naming-name]").value.trim() || null,
        number_kind: $("[data-naming-kind]").value,
        number: !batch && !allPartsSelected() && number.validity.valid && number.value ? Number(number.value) : null,
        episode_numbers: batch ? Object.fromEntries(state.namingNumbers) : {}
      };
    }
    function updateNamingEditor() {
      const result = state.resolved;
      if (!result) return;
      const batch = Boolean(result.episodes?.length);
      const enabled = $("[data-naming-mode]").value === "smart";
      const ids = new Set(selectedIds());
      const select = $("[data-naming-episode]"), previous = select.value;
      if (batch) {
        const chosen = result.episodes.filter(episode => ids.has(String(episode.id)));
        select.replaceChildren(...chosen.map(episode => new Option(episode.title || String(episode.id), String(episode.id))));
        if (ids.has(previous)) select.value = previous;
        $("[data-naming-number]").value = state.namingNumbers.get(select.value) || "";
      }
      $("[data-naming-episode-label]").hidden = !enabled || !batch || !select.value;
      $("[data-naming-kind-label]").hidden = !enabled || batch || allPartsSelected();
      $("[data-naming-number-label]").hidden = !enabled || (batch ? !select.value : allPartsSelected());
      $("[data-naming-name]").disabled = !enabled;
      $("[data-naming-number]").disabled = !enabled || (batch ? !select.value : allPartsSelected());
      const picked = batch ? result.naming_items?.[select.value] : result.naming_video;
      const inferred = picked?.number_kind === "part" ? `分 P · 第 ${picked.number} P` :
        picked?.number_kind === "episode" ? `连续第 ${picked.number} 集` : picked?.identifier ? `平台 ID ${picked.identifier}` : "未确认集号，保留可用平台标识";
      $("[data-naming-facts]").textContent = allPartsSelected()
        ? (result.naming_parts?.length ? `已识别 ${result.naming_parts.length} 个分 P` : "下载全部内容，按各文件的实际信息命名")
        : batch && !select.value ? "选择剧集后显示连续集号或平台 ID。" : [inferred, picked?.chapter].filter(Boolean).join(" · ");
      queueNamingPreview();
    }
    function queueNamingPreview() {
      clearTimeout(state.namingTimer);
      state.namingAbort?.abort();
      const result = state.resolved, version = ++state.namingRequest;
      if (!result) return;
      const output = $("[data-naming-preview]");
      const preferences = namingOptions();
      if (preferences.mode === "source") { output.textContent = "沿用现有来源模板。"; return; }
      const ids = selectedIds();
      const items = result.episodes?.length ? ids.slice(0, 8).map(id => result.naming_items?.[id] || {identifier: `ep${id}`}) :
        allPartsSelected() && result.naming_parts?.length
          ? result.naming_parts.slice(0, 8).map(part => ({...result.naming_video, number_kind:"part", number:part}))
          : [result.naming_video || {}];
      if (!items.length) { output.textContent = "选择剧集后显示预计文件名。"; return; }
      output.textContent = "正在更新预计文件名…";
      state.namingTimer = window.setTimeout(async () => {
        const controller = state.namingAbort = new AbortController();
        try {
          const response = await api("/api/download/naming/preview", {method:"POST", signal:controller.signal,
            body:JSON.stringify({title:String(result.title || "").slice(0, 300), options:preferences, items})});
          if (version !== state.namingRequest || result !== state.resolved || state.destroyed) return;
          const names = (response.items || []).map(name => make("div", "", name));
          const total = ids.length || (allPartsSelected() ? result.naming_parts?.length : 1) || 1;
          if (total > names.length && names.length) names.push(make("small", "dv2-hint", `另有 ${total - names.length} 个文件`));
          output.replaceChildren(...names);
          if (!names.length) output.textContent = "暂时无法提供预计文件名，下载仍可继续。";
        } catch(error) {
          if (version === state.namingRequest && result === state.resolved && !controller.signal.aborted && !state.destroyed) {
            output.textContent = `暂时无法预览文件名：${error.message}`;
          }
        }
      }, 180);
    }
    function updateCount({preserveFormats = false} = {}) {
      const count = selectedIds().length;
      selectedCount.textContent = `已选 ${count} 集`;
      $("[data-check-batch]").hidden = count < 2;
      if (!preserveFormats && count !== 1) formatField.value = DEFAULT_FORMAT;
      if (count > 1) formatHint.textContent = `批量下载 ${count} 集；已选画质偏好会应用到每集。`;
      else if (count === 1) formatHint.textContent = "已选 1 集；提交时使用该集可用格式。";
      else formatHint.textContent = "请选择剧集，或使用默认格式下载单个视频。";
      state.formatRequest += 1;
      if (!preserveFormats && count === 1) loadSelectedEpisodeFormat(selectedIds()[0], state.formatRequest);
      if (state.resolved) stage("selecting"); summary();
      updateNamingEditor();
    }
    function renderEpisodes(data, saved) {
      state.categories.clear();
      for (const episode of data.episodes || []) {
        const name = episodeCategory(episode);
        if (!state.categories.has(name)) state.categories.set(name, {items: [], page: 0, descending: false, scrollTop: 0});
        const {items: group} = state.categories.get(name);
        const number = name === "正片" ? Number(episode.display_episode_number || episode.group_index) : 0;
        group.push({episode, number: number > 0 ? number : group.length + 1});
      }
      const available = new Set((data.episodes || []).map(episode => String(episode.id)));
      state.selected = new Set((saved?.selected || [String(data.default_episode_id ?? "")])
        .filter(id => available.has(id)));
      const initial = (data.episodes || []).find(episode => state.selected.has(String(episode.id)));
      const categories = CATEGORIES.filter(name => state.categories.has(name));
      state.category = categories.includes(saved?.category) ? saved.category : initial ? episodeCategory(initial) : categories[0];
      for (const [name, group] of state.categories) {
        const {items} = group;
        items.sort((first, second) => first.number - second.number);
        const selectedIndex = items.findIndex(item => state.selected.has(String(item.episode.id)));
        const previous = saved?.views[name];
        group.descending = previous?.descending ?? (name === "正片");
        group.page = Math.min(previous?.page ?? (selectedIndex >= 0
          ? Math.floor(selectedIndex / EPISODES_PER_PAGE)
          : group.descending ? Math.floor((items.length - 1) / EPISODES_PER_PAGE) : 0),
        Math.floor((items.length - 1) / EPISODES_PER_PAGE));
        group.scrollTop = previous?.scrollTop ?? 0;
      }
      const buttons = $("[data-episode-categories]");
      buttons.replaceChildren();
      for (const name of categories) {
        const button = make("button", "", name);
        button.type = "button";
        button.dataset.category = name;
        button.addEventListener("click", () => {
          if (state.category === name) return;
          state.categories.get(state.category).scrollTop = episodeList.scrollTop;
          state.category = name;
          drawEpisodePage();
        });
        buttons.append(button);
      }
      drawEpisodePage();
      setEpisodesOpen(categories.length > 0);
    }

    function drawEpisodePage() {
      const group = state.categories.get(state.category);
      const rangeButtons = $("[data-episode-ranges]");
      episodeList.replaceChildren();
      rangeButtons.replaceChildren();
      $("[data-episode-detail]").hidden = true;
      $("[data-episode-detail]").replaceChildren();
      state.activeEpisodeId = null;
      if (!group) { updateEpisodeTiles(); return; }
      const {items} = group;
      for (const button of $("[data-episode-categories]").children) {
        button.setAttribute("aria-pressed", String(button.dataset.category === state.category));
      }
      for (const button of root.querySelectorAll("[data-sort]")) {
        button.setAttribute("aria-pressed", String((button.dataset.sort === "desc") === group.descending));
      }
      const pages = Math.ceil(items.length / EPISODES_PER_PAGE);
      rangeButtons.hidden = pages < 2;
      for (let offset = 0; offset < pages; offset += 1) {
        const index = group.descending ? pages - offset - 1 : offset;
        const first = items[index * EPISODES_PER_PAGE].number;
        const last = items[Math.min((index + 1) * EPISODES_PER_PAGE, items.length) - 1].number;
        const button = make("button", "", first === last ? first : `${first}–${last}`);
        button.type = "button";
        button.setAttribute("aria-pressed", String(index === group.page));
        button.addEventListener("click", () => {
          group.page = index;
          group.scrollTop = 0;
          drawEpisodePage();
          rangeButtons.children[offset].focus({preventScroll: true});
        });
        rangeButtons.append(button);
      }
      const start = group.page * EPISODES_PER_PAGE;
      const visible = items.slice(start, start + EPISODES_PER_PAGE);
      if (group.descending) visible.reverse();
      for (const {episode, number} of visible) {
        const id = String(episode.id);
        const title = episode.title || `${state.category} ${number}`;
        const tile = make("button", "dv2-episode", number);
        tile.type = "button";
        tile.dataset.episodeId = id;
        tile.title = title;
        tile.setAttribute("aria-label", `${number} · ${title}`);
        tile.addEventListener("focus", () => showEpisodeDetail(episode, title));
        tile.addEventListener("pointerenter", () => {
          if (!$("[data-episode-detail]").contains(document.activeElement)) showEpisodeDetail(episode, title);
        });
        tile.addEventListener("click", () => {
          if (!state.selected.delete(id)) state.selected.add(id);
          showEpisodeDetail(episode, title);
          updateEpisodeTiles();
          updateCount();
        });
        episodeList.append(tile);
      }
      updateEpisodeTiles();
      episodeList.scrollTop = group.scrollTop;
    }

    function pageSelectionIds() {
      return Array.from(episodeList.children, tile => tile.dataset.episodeId);
    }

    function updateEpisodeTiles() {
      const ids = pageSelectionIds();
      const allSelected = ids.length > 0 && ids.every(id => state.selected.has(id));
      for (const tile of episodeList.children) {
        tile.setAttribute("aria-pressed", String(state.selected.has(tile.dataset.episodeId)));
      }
      selectAll.disabled = ids.length === 0;
      selectAll.textContent = allSelected ? "取消全选" : "全选";
      selectAll.setAttribute("aria-pressed", String(allSelected));
    }

    function togglePageSelection() {
      const ids = pageSelectionIds();
      const allSelected = ids.every(id => state.selected.has(id));
      for (const id of ids) {
        if (allSelected) state.selected.delete(id);
        else state.selected.add(id);
      }
      updateEpisodeTiles();
      updateCount();
    }

    function showEpisodeDetail(episode, title) {
      const id = String(episode.id);
      if (state.activeEpisodeId === id) return;
      state.activeEpisodeId = id;
      const detail = $("[data-episode-detail]");
      const text = make("span", "", [title, episode.badge, episode.duration ? elapsed(episode.duration) : null].filter(Boolean).join(" · "));
      const quality = make("select", "dv2-episode-quality");
      quality.setAttribute("aria-label", `${title} 的画质`);
      quality.append(new Option("跟随统一画质", ""));
      const cached = state.episodeFormats.get(id);
      if (cached) {
        for (const item of cached) quality.append(new Option(formatLabel(item), item.selector));
        quality.dataset.loaded = "true";
      }
      quality.value = state.episodeChoices.get(id) || "";
      quality.addEventListener("focus", () => loadEpisodeChoices(episode, quality));
      quality.addEventListener("change", () => {
        if (quality.value) state.episodeChoices.set(id, quality.value);
        else state.episodeChoices.delete(id);
        summary();
      });
      detail.replaceChildren(text, quality);
      detail.hidden = false;
    }
    async function loadEpisodeChoices(episode,select) {
      const id=String(episode.id), resolved=state.resolved, version=state.parseVersion;
      if (select.dataset.loaded || select.dataset.loading) return;
      select.dataset.loading='true';
      try {
        const result=await api('/api/episodes/formats',{method:'POST',body:JSON.stringify({url:episode.url || `https://www.bilibili.com/bangumi/play/ep${episode.id}`,api_mode:resolved.api_mode || modeField.value})});
        if (state.resolved!==resolved || version!==state.parseVersion || !select.isConnected) return;
        const formats=(result.formats || []).filter(item=>item.selector);
        state.episodeFormats.set(id,formats);select.replaceChildren(new Option('跟随统一画质',''));
        for (const item of formats) select.append(new Option(formatLabel(item),item.selector));
        select.dataset.loaded='true';select.value=state.episodeChoices.get(id) || '';
        select.title=formats.length ? '此集真实可用格式' : '未返回额外格式，使用统一画质';
      } catch(error) {
        if (state.resolved===resolved && version===state.parseVersion) {select.title=error.message;showError(`该集画质读取失败：${error.message}；再次打开可重试。`);}
      } finally {delete select.dataset.loading;}
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
      summary();
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
      summary();
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
      rememberSelection();
      state.resolved = null;
      resolution.hidden = true;
      ++state.formatRequest;
      stage("parsing");
      const button = $("[data-parse]");
      button.disabled = true;
      button.textContent = "解析中…";
      parseState.textContent = "正在读取视频与剧集信息";
      try {
        const result = await api("/api/resolve", {method: "POST", body: JSON.stringify({url, api_mode: mode})});
        if(request!==state.parseVersion || url!==urlField.value.trim() || mode!==modeField.value)return;
        applyResolved(result, url);
        void refreshHistory();
      } catch (error) {
        if(request!==state.parseVersion)return;
        state.resolved = null;
        resolution.hidden = true;
        showError(error.message);
      } finally {
        if(request===state.parseVersion){button.disabled = false;button.textContent = "解析链接";}
      }
    }
    function sourceKey() {
      return JSON.stringify([state.resolved.api_mode || modeField.value, state.resolvedUrl]);
    }
    function rememberSelection() {
      if (!state.resolved) return;
      const current = state.categories.get(state.category);
      if (current) current.scrollTop = episodeList.scrollTop;
      state.selectionsBySource.set(sourceKey(), {
        selected: selectedIds(),
        category: state.category,
        views: Object.fromEntries([...state.categories].map(([name, items]) => [
          name, {page: items.page, descending: items.descending, scrollTop: items.scrollTop}
        ]))
      });
    }
    function applyResolved(result, url, {fromHistory = false} = {}) {
      result = {...result, episodes: Array.isArray(result.episodes) ? result.episodes : []};
      state.resolved = result;
      state.episodeChoices.clear();
      state.episodeFormats.clear();
      state.resolvedUrl = result.source_url || result.webpage_url || url;
      state.namingNumbers.clear();
      $("[data-naming-mode]").value = "smart";
      $("[data-naming-name]").value = String(result.title || "").slice(0, 240);
      $("[data-naming-number]").value = "";
      $("[data-naming-kind]").value = result.naming_video?.number_kind || "episode";
      const cover=$("[data-cover]");
      if(cover){cover.hidden=true;cover.removeAttribute('src');try{const image=new URL(result.thumbnail||result.cover);if(['http:','https:'].includes(image.protocol)&&!image.username&&!image.password){cover.src=image.href;cover.hidden=false;cover.onerror=()=>{cover.hidden=true;};}}catch{}}
      $("[data-title]").textContent = result.title || "已解析内容";
      $("[data-summary]").textContent = [result.uploader, result.duration ? elapsed(result.duration) : null, result.playlist_count ? `${result.playlist_count} 项` : null, modeLabel(result.api_mode || modeField.value)].filter(Boolean).join(" · ");
      $("[data-template]").value = result.episodes?.length ? (options.playlistTemplate || options.outputTemplate || "") : (options.singleTemplate || options.outputTemplate || "");
      resolution.hidden = false;
      const allParts = $(`[data-all-parts]`);
      allParts.hidden = Boolean(result.episodes?.length) || modeField.value === "tv";
      allParts.querySelector('input').checked = false;
      const saved = state.selectionsBySource.get(sourceKey());
      renderFormats(result.formats);
      renderEpisodes(result, saved);
      updateCount({preserveFormats: true});
      if (result.warning) formatHint.textContent = result.warning;
      if (fromHistory && !result.naming_items && !result.naming_video) {
        formatHint.textContent += " 命名以提交时识别的信息为准。";
      }
      parseState.textContent = "";
      stage("selecting");
      summary();
    }
    function setSelection(method) {
      if (method === "invert") {
        const ids = (state.resolved?.episodes || []).map(episode => String(episode.id));
        state.selected = new Set(ids.filter(id => !state.selected.has(id)));
      } else {
        state.selected.clear();
      }
      updateEpisodeTiles();
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
      const resolved=state.resolved, version=state.parseVersion;
      const current=()=>state.resolved===resolved && version===state.parseVersion;
      const url = state.resolvedUrl || urlField.value.trim();
      const ids = selectedIds();
      const template = $("[data-template]").value.trim();
      const naming = namingOptions();
      const outputDir = $("[data-output]").value.trim();
      const submitButton = form.querySelector('button[type="submit"]');
      if(state.submitting)return;
      state.submitting=true;submitButton.disabled = true;stage("submitting");summary();
      try {
        if (state.resolved.episodes?.length) {
          if (!ids.length) throw new Error("请至少选择一集。");
          if (ids.length > 600) throw new Error("单次最多创建 600 个任务。");
          const payload = {url, episode_ids: ids, api_mode: state.resolved.api_mode || modeField.value, format_selector: formatField.value, output_template: template || null, output_dir: outputDir, naming};
          if (ids.length > 1) payload.format_profile = makeProfile();
          const customized = ids.filter(id=>state.episodeChoices.has(id));
          if (!customized.length) await api("/api/jobs/batch", {method: "POST", body: JSON.stringify(payload)});
          else {
            // Existing batch API owns creation. Each explicit per-episode selector is
            // submitted only for its episode. Remove acknowledged selections so a
            // partial failure can be retried without duplicating accepted jobs.
            const uniform=ids.filter(id=>!state.episodeChoices.has(id));
            const groups=[...(uniform.length ? [{...payload,episode_ids:uniform}] : []),...customized.map(id=>({...payload,episode_ids:[id],format_selector:state.episodeChoices.get(id),format_profile:null}))];
            let accepted=0;
            for(const group of groups) {
              try {await api('/api/jobs/batch',{method:'POST',body:JSON.stringify(group)});}
              catch(error){await refreshJobs();throw new Error(`已提交 ${accepted} 集，其余保持勾选：${error.message}`);}
              accepted+=group.episode_ids.length;
              if(current()){
                for (const id of group.episode_ids) state.selected.delete(id);
                updateEpisodeTiles();
                updateCount({preserveFormats: true});
              }
            }
          }
        } else {
          await api("/api/jobs", {method: "POST", body: JSON.stringify({url, api_mode: modeField.value, format_selector: formatField.value, all_parts: allPartsSelected(), output_template: template || null, output_dir: outputDir, naming, title: state.resolved.title || null, uploader: state.resolved.uploader || null})});
        }
        if(current()){stage("submitted");setEpisodesOpen(false);}
        notice.textContent = "任务已加入下载队列，完成后可将实际文件带入发种工作台。";
        await refreshJobs();
      } catch (error) { if(current())showError(error.message);else options.onNotice?.(error.message,true); }
      finally { state.submitting=false;submitButton.disabled = false;summary(); }
    }
    function createJobCard(job) {
      const card = make("article", "dv2-job");
      card.dataset.jobId = String(job.id);
      const head = make("div", "dv2-job-head");
      const title = make("strong", "dv2-job-title");
      const badge = make("span", "dv2-badge");
      const meta = make("div", "dv2-job-meta");
      const track = make("div", "dv2-progress");
      const bar = document.createElement("span");
      const actions = make("div", "dv2-job-actions");
      const error = make("div", "dv2-job-error");
      track.append(bar);
      head.append(title, badge);
      card.append(head, meta, track, error, actions);
      const entry = {card, title, badge, meta, track, bar, error, actions, actionSignature: null};
      state.jobCards.set(String(job.id), entry);
      return entry;
    }
    function updateJobCard(entry, job) {
      const status = displayStatus(job);
      entry.card.dataset.status = status;
      entry.card.dataset.rawStatus = job.status || "";
      entry.title.textContent = job.title || "等待解析";
      entry.badge.textContent = `${modeLabel(job.api_mode)} · ${statusLabel(status)}`;
      const progress = Math.max(0, Math.min(100, Number(job.progress) || 0));
      const meta = [`任务 #${job.id}`];
      if (status === "queued") {
        meta.push("等待开始");
      } else if (status === "downloading") {
        meta.push(`${Math.round(progress)}%`);
        if (Number(job.speed) > 0) meta.push(`${size(job.speed)}/s`);
        if (job.eta != null) meta.push(`剩余 ${elapsed(job.eta)}`);
        if (job.total_bytes) meta.push(`${size(job.downloaded_bytes)} / ${size(job.total_bytes)}`);
      } else if (status === "merging") {
        meta.push("下载完成，正在合并音视频并完成后处理");
      }
      if (HISTORY_STATUSES.has(job.status)) {
        const fields = [["任务编号", `#${job.id}`], ["线路", modeLabel(job.api_mode)]];
        if (job.result_path) fields.push(["最终文件名", job.result_path.split("/").at(-1)]);
        fields.push([status === "completed" ? "完成时间" : "结束时间", dateTimeLabel(job.finished_at)]);
        const duration = durationSeconds(job);
        if (duration != null) fields.push([status === "completed" ? "下载总用时" : "总用时", elapsed(duration)]);
        if (status === "completed") {
          fields.push(["最终文件大小", job.final_size_bytes == null ? "不可用" : size(job.final_size_bytes)]);
        }
        const signature = JSON.stringify(fields);
        if (entry.metaSignature !== signature) entry.meta.replaceChildren(...fields.map(([label, value]) => {
          const field = make("span", "dv2-job-fact");
          field.append(make("small", "", label + " "), make("strong", "", value));
          return field;
        }));
        entry.metaSignature = signature;
      } else {
        entry.metaSignature = null;
        const line = meta.join(" · ");
        if (entry.meta.textContent !== line) entry.meta.textContent = line;
      }
      entry.track.hidden = !["queued", "downloading", "merging"].includes(status);
      entry.bar.style.width = `${status === "merging" ? 100 : progress}%`;
      entry.error.hidden = !job.error;
      entry.error.textContent = job.error || "";

      if (entry.actionSignature === String(job.status)) return;
      entry.actionSignature = String(job.status);
      entry.actions.replaceChildren();
      const actions = entry.actions;
      if (["queued", "running"].includes(job.status)) addAction(actions, "取消", () => taskAction(job, "cancel"), "is-danger");
      if (["failed", "cancelled", "auth_required"].includes(job.status)) addAction(actions, "重试", () => taskAction(job, "retry"));
      addAction(actions, "查看日志", () => showLog(job.id));
      if (job.status === "completed" && options.onOpenFile) addAction(actions, "进入工作台", async () => {
        try { await options.onOpenFile(job); } catch (error) { showError(error.message); }
      });
      if(job.status==='completed' && options.onOpenDirectory) addAction(actions,'打开文件管理',()=>options.onOpenDirectory(job));
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
    function renderJobs(jobs) {
      const active = [], history = [];
      const keys = new Set();
      for (const job of jobs) {
        if (!job || job.id == null) continue;
        const key = String(job.id);
        keys.add(key);
        const entry = state.jobCards.get(key) || createJobCard(job);
        updateJobCard(entry, job);
        (HISTORY_STATUSES.has(job.status) ? history : active).push(entry.card);
      }
      for (const [key, entry] of state.jobCards) {
        if (keys.has(key)) continue;
        entry.card.remove();
        state.jobCards.delete(key);
        state.previousStatuses.delete(key);
      }
      placeCards(jobsHost, active, "暂无进行中的任务");
      placeCards(jobsHistoryHost, history, "暂无下载历史");
      $("[data-queue-count]").textContent = active.length ? `${active.length} 项` : "";
      $("[data-download-history-count]").textContent = `${history.length} 条`;
    }
    function placeCards(host, cards, emptyMessage) {
      const retained = new Set(cards);
      for (const child of [...host.children]) {
        if (!retained.has(child) && !(child.matches(".dv2-empty") && !cards.length)) child.remove();
      }
      cards.forEach((card, index) => {
        const current = host.children[index];
        if (current !== card) host.insertBefore(card, current || null);
      });
      if (!host.children.length) host.append(make("p", "dv2-empty", emptyMessage));
    }
    async function refreshJobs({force=false} = {}) {
      if (state.destroyed) return;
      if (state.refreshInFlight) {
        if (force) state.refreshQueued = true;
        return state.refreshInFlight;
      }
      if (!force && !pageVisible()) return;
      const controller = new AbortController();
      state.refreshAbort = controller;
      state.refreshInFlight = (async () => {
        try {
          const result = await api("/api/jobs?limit=500", {signal: controller.signal});
          if (state.destroyed) return;
          if (!Array.isArray(result)) throw new Error("任务列表响应格式无效");
          for (const job of result) {
            if (state.seenInitialJobs) alertTransition(job);
            else state.previousStatuses.set(String(job.id), job.status);
          }
          state.seenInitialJobs = true;
          state.jobs = result;
          renderJobs(result);
          notice.textContent = "队列状态自动更新。";
        } catch (error) {
          if (error.name === "AbortError" || state.destroyed) return;
          notice.textContent = `队列读取失败：${error.message}；保留上次结果。`;
          if (!state.jobs.length) showJobsUnavailable(error.message);
        } finally {
          if (state.refreshAbort === controller) state.refreshAbort = null;
          state.refreshInFlight = null;
          if (!state.destroyed) {
            if (state.refreshQueued) {
              state.refreshQueued = false;
              window.queueMicrotask(() => refreshJobs({force: true}));
            } else {
              scheduleRefresh();
            }
          }
        }
      })();
      return state.refreshInFlight;
    }
    function showJobsUnavailable(message) {
      for (const entry of state.jobCards.values()) entry.card.remove();
      state.jobCards.clear();
      jobsHost.replaceChildren(make("p", "dv2-empty", message));
      jobsHistoryHost.replaceChildren(make("p", "dv2-empty", message));
    }
    function scheduleRefresh(delay = options.pollInterval || 5000) {
      clearRefreshTimer();
      if (!pageVisible() || state.refreshInFlight) return;
      state.timer = window.setTimeout(() => {
        state.timer = null;
        refreshJobs();
      }, Math.max(1000, Number(delay) || 5000));
    }
    async function showLog(jobId) {
      try {
        const result = await api(`/api/jobs/${jobId}/log?lines=160`);
        $(`[data-log-content]`).textContent = result.content || "暂无日志内容";
        $("[data-log-title]").textContent = `任务 #${jobId} 日志`;
        state.logScrollY = window.scrollY;
        state.logReturnFocus = document.activeElement;
        $(`[data-log-content]`).scrollTop = 0;
        if (typeof logDialog.showModal === "function") logDialog.showModal();
        else logDialog.setAttribute("open", "");
        $("[data-log-close]")?.focus({preventScroll: true});
      } catch (error) { showError(error.message); }
    }
    async function refreshHistory() {
      const host = $(`[data-history]`);
      try {
        const items = await api("/api/history?limit=200");
        if (state.destroyed) return;
        host.replaceChildren();
        $(`[data-history-count]`).textContent = `${items.length} 条`;
        if (!items.length) host.append(make("p", "dv2-empty", "暂无解析记录"));
        for (const item of items) {
          const row = make("div", "dv2-history-item");
          const summary = make("div", "", `${item.title || "未命名内容"} · ${modeLabel(item.api_mode)} · ${item.updated_at || ""}`);
          const restore = make("button", "", "复用解析");
          restore.type = "button";
          restore.addEventListener("click", async () => {
            const request = ++state.parseVersion;
            restore.disabled = true;
            try {
              const detail = await api(`/api/history/${item.id}`);
              if (request !== state.parseVersion || state.destroyed) return;
              if (!detail.source_url) throw new Error("该记录没有来源链接，请重新解析。");
              rememberSelection();
              ++state.formatRequest;
              clearError();
              urlField.value = detail.source_url;
              modeField.value = detail.api_mode === "tv" ? "tv" : "web";
              applyResolved(detail, detail.source_url, {fromHistory: true});
              parseState.textContent = "已复用解析记录，可继续选集。";
              const destination = episodeToggle.hidden ? formatField : episodeToggle;
              destination.focus({preventScroll: true});
              destination.scrollIntoView({block: "nearest"});
            } catch (error) {
              if (request === state.parseVersion && !state.destroyed) showError(error.message);
            } finally {
              restore.disabled = false;
              if (request === state.parseVersion && !state.destroyed) {
                $("[data-parse]").disabled = false;
                $("[data-parse]").textContent = "解析链接";
              }
            }
          });
          row.append(summary, restore); host.append(row);
        }
      } catch (error) {
        if (!state.destroyed) host.replaceChildren(make("p", "dv2-empty", error.message));
      }
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

    const closeMenus = event => {
      for(const menu of root.querySelectorAll('.dv2-account,.dv2-select-actions details')) if(!menu.contains(event.target))menu.open=false;
    };
    function restoreLogView() {
      const scrollY = state.logScrollY;
      const focus = state.logReturnFocus;
      state.logReturnFocus = null;
      window.requestAnimationFrame(() => {
        if (focus?.isConnected) focus.focus({preventScroll: true});
        window.scrollTo(0, scrollY);
      });
    }
    function closeLog() {
      if (!logDialog) return;
      if (typeof logDialog.close === "function" && logDialog.open) logDialog.close();
      else {
        logDialog.removeAttribute("open");
        restoreLogView();
      }
    }
    listen(document, "pointerdown", closeMenus);
    listen(document, "visibilitychange", () => {
      if (pageVisible()) refreshJobs({force: true});
      else clearRefreshTimer();
    });
    listen(document, "app:view", event => {
      if (event.detail?.view === "download") refreshJobs({force: true});
      else clearRefreshTimer();
    });
    listen($("[data-parse]"), "click", parse);
    listen(urlField, "keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); parse(); } });
    listen(urlField, "input", () => {
      rememberSelection();
      ++state.parseVersion;
      ++state.formatRequest;
      state.resolved = null;
      resolution.hidden = true;
      stage("idle");
      clearError();
      parseState.textContent = "";
      $("[data-parse]").disabled = false;
      $("[data-parse]").textContent = "解析链接";
    });
    listen(form, "submit", submit);
    listen(formatField, "change", summary);
    listen($("[data-naming-mode]"), "change", updateNamingEditor);
    listen($("[data-naming-episode]"), "change", () => {
      const id = $("[data-naming-episode]").value;
      $("[data-naming-number]").value = state.namingNumbers.get(id) || "";
      updateNamingEditor();
    });
    listen($("[data-naming-name]"), "input", queueNamingPreview);
    listen($("[data-naming-kind]"), "change", queueNamingPreview);
    listen($("[data-naming-number]"), "input", () => {
      const id = $("[data-naming-episode]").value, number = $("[data-naming-number]");
      if (state.resolved?.episodes?.length && id) {
        if (!number.value) state.namingNumbers.delete(id);
        else if (number.validity.valid) state.namingNumbers.set(id, Number(number.value));
      }
      queueNamingPreview();
    });
    listen($("[data-all-parts] input"), "change", updateNamingEditor);
    listen($("[data-output]"), "input", summary);
    listen(modeField, "change", () => { ++state.parseVersion; if (urlField.value.trim()) parse(); });
    listen(selectAll, "click", togglePageSelection);
    listen($("[data-select-invert]"), "click", () => setSelection("invert"));
    listen($("[data-select-none]"), "click", () => setSelection("none"));
    listen($("[data-check-batch]"), "click", checkBatchFormats);
    for (const button of root.querySelectorAll("[data-sort]")) {
      listen(button, "click", () => {
        const group = state.categories.get(state.category);
        if (!group) return;
        const descending = button.dataset.sort === "desc";
        if (group.descending === descending) return;
        group.descending = descending;
        group.page = descending ? Math.floor((group.items.length - 1) / EPISODES_PER_PAGE) : 0;
        group.scrollTop = 0;
        drawEpisodePage();
      });
    }
    listen(episodeToggle, "click", () => setEpisodesOpen(!state.episodesOpen));
    listen($("[data-refresh]"), "click", () => refreshJobs({force:true}));
    listen($("[data-history-refresh]"), "click", refreshHistory);
    listen(logDialog, "cancel", event => { event.preventDefault(); closeLog(); });
    listen(logDialog, "close", restoreLogView);
    listen(logDialog, "click", event => {
      if (event.target !== logDialog) return;
      const bounds = logDialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
        closeLog();
      }
    });
    listen($("[data-log-close]"), "click", closeLog);
    listen($("[data-pick-folder]"), "click", async () => {
      if (typeof options.onChooseDirectory !== "function") return;
      try {
        const selected = await options.onChooseDirectory($("[data-output]").value.trim());
        if (typeof selected === "string") $("[data-output]").value = selected;
        else if (selected && typeof selected.relative_path === "string") $("[data-output]").value = selected.relative_path;
        summary();
      } catch (error) { showError(error.message); }
    });
    listen(document, "bili:account-changed", refreshAccount);
    listen($("[data-login-web]"), "click", () => options.onLogin?.("web"));
    listen($("[data-login-tv]"), "click", () => options.onLogin?.("tv"));
    listen($("[data-logout-web]"), "click", async () => {
      try { await api("/api/bili/logout", {method: "POST"}); await refreshAccount(); }
      catch (error) { showError(error.message); }
    });
    listen($("[data-logout-tv]"), "click", async () => {
      try { await api("/api/bili/tv/logout", {method: "POST"}); await refreshAccount(); }
      catch (error) { showError(error.message); }
    });

    stage("idle");summary();$("[data-parse]").disabled=false;root.dataset.mounted="true";refreshAccount(); refreshJobs(); refreshHistory();
    return {
      state,
      parse,
      refreshJobs,
      refreshHistory,
      destroy() {
        if (state.destroyed) return;
        state.destroyed = true;
        ++state.parseVersion;
        ++state.formatRequest;
        clearRefreshTimer();
        state.refreshAbort?.abort();
        state.namingAbort?.abort();
        clearTimeout(state.namingTimer);
        for (const [target, type, handler, listenerOptions] of state.listeners.splice(0)) {
          target.removeEventListener(type, handler, listenerOptions);
        }
        if (logDialog?.open) logDialog.close();
        state.jobCards.clear();
        jobsHost.replaceChildren();
        jobsHistoryHost.replaceChildren();
        episodeList.replaceChildren();
        delete root.dataset.mounted;
      }
    };
  }

  window.BiliDownloadV2 = Object.freeze({mount});
})();
