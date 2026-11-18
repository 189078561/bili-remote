(() => {
  "use strict";

  const EPISODES_PER_PAGE = 50;
  const CATEGORIES = ["正片", "花絮", "预告"];
  const HISTORY_STATUSES = new Set(["completed", "failed", "cancelled", "auth_required"]);
  const DEFAULT_FORMAT = "bv*+ba/b";
  const TRACKS_PER_REQUEST = 12;
  const parseHistoryTime = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"
  });

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
    if (text.includes("avc") || text === "h264") return "H.264";
    if (text.includes("hev") || text.includes("hvc") || text === "h265") return "H.265";
    if (text.includes("av01") || text === "av1") return "AV1";
    if (text.includes("mp4a")) return "AAC";
    if (text.includes("opus")) return "Opus";
    if (text.includes("ac-3") || text.includes("ec-3")) return "Dolby";
    return "未知编码";
  }

  function formatBitrate(kbps) {
    const value = Number(kbps);
    if (!Number.isFinite(value) || value <= 0) return null;
    return value >= 1000 ? `${(value / 1000).toFixed(2)} Mbps` : `${Math.round(value)} kbps`;
  }

  function formatResourceSize(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value <= 0) return null;
    if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)} GB`;
    return value < 100_000 ? "<0.1 MB" : `${(value / 1_000_000).toFixed(1)} MB`;
  }

  function formatLabel(item) {
    const bits = [
      item.height ? `${item.height}P` : "仅音频",
      item.fps ? `${Math.round(Number(item.fps))}fps` : null,
      item.vcodec && item.vcodec !== "none" ? codecName(item.vcodec) : codecName(item.acodec),
      item.dynamic_range && item.dynamic_range !== "SDR" ? item.dynamic_range : null,
      item.format_note,
      formatBitrate(item.tbr),
      formatResourceSize(item.filesize)
    ].filter(Boolean);
    return `${bits.join(" · ")} [${item.format_id || item.selector}]`;
  }

  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function trackFamily(track) {
    const codec = track.vcodec && track.vcodec !== "none" ? track.vcodec : track.acodec;
    const name = codecName(codec);
    return JSON.stringify([name === "未知编码" ? codec : name, track.dynamic_range || "", Number(track.fps) || 0]);
  }

  function trackTier(track) {
    return JSON.stringify([track.format_id, Number(track.height) || 0, trackFamily(track)]);
  }

  function bitrateRange(values) {
    const rates = values.map(Number).filter(rate => Number.isFinite(rate) && rate > 0);
    if (!rates.length) return "码率未知";
    const low = Math.min(...rates), high = Math.max(...rates);
    const label = low === high ? formatBitrate(low) :
      low >= 1000 ? (low / 1000).toFixed(2) + "–" + formatBitrate(high) :
      high < 1000 ? Math.round(low) + "–" + formatBitrate(high) :
      formatBitrate(low) + "–" + formatBitrate(high);
    return label + (rates.length < values.length ? "（部分未知）" : "");
  }

  function combinedSize(rows) {
    const known = rows.filter(row => Number(row.track?.size_with_audio) > 0);
    return {
      bytes: known.reduce((sum, row) => sum + Number(row.track.size_with_audio), 0),
      complete: known.length === rows.length && rows.length > 0
    };
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

  function historyIdentity(item) {
    try {
      const url = new URL(item.source_url);
      const path = url.pathname.replace(/\/+$/, "");
      const bangumi = path.match(/^\/bangumi\/play\/(ep|ss)(\d+)$/);
      if (bangumi) return {key: bangumi[1] + bangumi[2], kind: bangumi[1], id: bangumi[2]};
      if (/^\/video\/(?:BV[\w]+|av\d+)$/i.test(path)) {
        return {key: `${path}?p=${Number(url.searchParams.get("p")) || 1}`};
      }
    } catch { /* older records may lack a usable source URL */ }
    return {key: item.source_url || `record:${item.id}`};
  }

  async function groupHistory(items, getDetail) {
    const exact = new Map();
    const families = new Map();
    items.forEach((item, index) => {
      const identity = historyIdentity(item);
      const key = JSON.stringify([item.api_mode, identity.key]);
      if (exact.has(key)) {
        exact.get(key).ids.push(item.id);
        return;
      }
      const group = {item, index, ids: [item.id], identity};
      exact.set(key, group);
      if (identity.kind && item.is_playlist && item.title) {
        const family = JSON.stringify([item.api_mode, item.title.trim()]);
        if (!families.has(family)) families.set(family, []);
        families.get(family).push(group);
      }
    });
    const merged = new Set();
    const mainEpisodeIds = episodes => new Set(episodes.filter(episode => episode.group === "正片")
      .map(episode => String(episode.id)));
    await Promise.all([...families.values()].map(async groups => {
      // A season URL identifies one season. Its cached list also provides the
      // episode IDs needed to match records opened through individual links.
      const pending = new Set([...groups.filter(group => group.identity.kind === "ss"),
        ...groups.filter(group => group.identity.kind === "ep")]);
      const details = new Map();
      const readEpisodes = async group => {
        if (!details.has(group)) details.set(group, (await getDetail(group.item))?.episodes || []);
        return details.get(group);
      };
      for (const anchor of pending) {
        pending.delete(anchor);
        const candidates = [...pending].filter(group => group.identity.kind === "ep");
        if (!candidates.length) continue;
        const episodes = await readEpisodes(anchor);
        const episodeIds = new Set(episodes.map(episode => String(episode.id)));
        const mainIds = mainEpisodeIds(episodes);
        const related = [];
        for (const group of candidates) {
          if (episodeIds.has(group.identity.id)) {
            related.push(group);
          } else if (group.index < anchor.index && mainIds.size) {
            // A newer parse can contain episodes added after the season link
            // was cached. Require a shared main episode to match that season.
            const newer = await readEpisodes(group);
            if ([...mainEpisodeIds(newer)].some(id => mainIds.has(id))) related.push(group);
          }
        }
        if (!related.length) continue;
        const members = [anchor, ...related].sort((a, b) => a.index - b.index);
        for (const group of related) pending.delete(group);
        const [latest, ...older] = members;
        for (const group of older) {
          latest.ids.push(...group.ids);
          merged.add(group);
        }
      }
    }));
    return [...exact.values()].filter(group => !merged.has(group));
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

  function savedEpisodeNumber(episode) {
    if (episode.group !== "正片") return null;
    let number = episode.display_episode_number;
    // Older snapshots joined the platform's episode label and chapter as
    // "1 来世，你可以找我报仇". A null field in newer data is already evaluated.
    if (!Object.hasOwn(episode, "display_episode_number")) {
      const title = String(episode.title || "").trim();
      const match = title.match(/^(?:第\s*(\d{1,5})\s*[集话話]|(\d{1,5})(?=\s|$))/);
      if (match) number = Number(match[1] || match[2]);
    }
    return Number.isInteger(number) && number >= 1 && number <= 99999 ? number : null;
  }

  function savedEpisodeNamingFacts(episodes) {
    const counts = new Map();
    const numbers = episodes.map(savedEpisodeNumber);
    for (const number of numbers) {
      if (number !== null) counts.set(number, (counts.get(number) || 0) + 1);
    }
    return Object.fromEntries(episodes.map((episode, index) => {
      const id = String(episode.id);
      const number = counts.get(numbers[index]) === 1 ? numbers[index] : null;
      return [id, {
        identifier: /^\d{1,20}$/.test(id) ? `ep${id}` : null,
        number_kind: number === null ? null : "episode",
        number,
        chapter: String(episode.chapter_title || "").slice(0, 300) || null,
        extra_title: episode.group && episode.group !== "正片" ? String(episode.title || "").trim().slice(0, 300) || null : null
      }];
    }));
  }

  function mount(root, options = {}) {
    if (!root) throw new TypeError("download workbench root is required");
    const api = options.api || createApi();
    const $ = (selector) => root.querySelector(selector);
    const state = {
      resolved: null, resolvedUrl: "", jobs: [], previousStatuses: new Map(), seenInitialJobs: false,
      timer: null, refreshInFlight: null, refreshQueued: false, refreshAbort: null, destroyed: false,
      jobCards: new Map(), listeners: [], logScrollY: 0, logReturnFocus: null,
      episodeFormats: new Map(), episodeChoices: new Map(), formatRequest: 0,
      batchTracks: new Map(), batchErrors: new Map(), batchTarget: null, batchGroup: null,
      batchTimer: null, batchProbe: null, batchPlan: null,
      parseVersion: 0, episodesOpen: false, submitting: false,
      historyVersion: 0,
      selected: new Set(), categories: new Map(), category: null, activeEpisodeId: null,
      selectionsBySource: new Map(),
      namingNumbers: new Map(), namingRequest: 0, namingTimer: null, namingAbort: null
    };
    const form = $("[data-download-form]");
    const urlField = $("#dv2-url");
    const modeField = $("[data-mode]");
    const formatField = $("[data-format]");
    const batchFormat = $("[data-batch-format]");
    const batchFallback = $("[data-batch-fallback]");
    const batchList = $("[data-batch-list]");
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
      if (state.batchPlan) $('[data-summary-quality]').textContent = state.batchPlan.title;
      form.querySelector('button[type=submit]').disabled = state.submitting || Boolean(state.batchPlan && !state.batchPlan.ready);
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
    function updateNamingEditor(preferredEpisodeId) {
      const result = state.resolved;
      if (!result) return;
      const batch = Boolean(result.episodes?.length);
      const enabled = $("[data-naming-mode]").value === "smart";
      const ids = new Set(selectedIds());
      const select = $("[data-naming-episode]"), previous = select.value;
      if (batch) {
        const chosen = result.episodes.filter(episode => ids.has(String(episode.id)));
        select.replaceChildren(...chosen.map(episode => new Option(episode.title || String(episode.id), String(episode.id))));
        const preferred = ids.has(preferredEpisodeId) ? preferredEpisodeId : previous;
        const main = chosen.find(episode => episode.group === "正片");
        if (ids.has(preferred)) select.value = preferred;
        else if (main) select.value = String(main.id);
        $("[data-naming-number]").value = state.namingNumbers.get(select.value) || "";
      }
      $("[data-naming-episode-label]").hidden = !enabled || !batch || !select.value;
      $("[data-naming-kind-label]").hidden = !enabled || batch || allPartsSelected();
      $("[data-naming-number-label]").hidden = !enabled || (batch ? !select.value : allPartsSelected());
      $("[data-naming-name]").disabled = !enabled;
      $("[data-naming-number]").disabled = !enabled || (batch ? !select.value : allPartsSelected());
      queueNamingPreview();
    }
    function updateNamingHint(result, preferences) {
      const batch = Boolean(result.episodes?.length);
      const id = $("[data-naming-episode]").value;
      const picked = batch ? result.naming_items?.[id] : result.naming_video;
      const edited = batch ? preferences.episode_numbers[id] : preferences.number;
      const unit = !batch && preferences.number_kind === "part" ? "P" : "集";
      const inferred = edited ? `手动编号 · 第 ${edited} ${unit}` :
        picked?.extra_title ? `按标题命名 · ${picked.extra_title}` :
        picked?.number_kind === "part" ? `分 P · 第 ${picked.number} P` :
        picked?.number_kind === "episode" ? `连续第 ${picked.number} 集` : picked?.identifier ? `平台 ID ${picked.identifier}` : "未确认集号，保留可用平台标识";
      $("[data-naming-facts]").textContent = allPartsSelected()
        ? (result.naming_parts?.length ? `已识别 ${result.naming_parts.length} 个分 P` : "下载全部内容，按各文件的实际信息命名")
        : batch && !id ? "选择剧集后显示命名依据。" : [inferred, picked?.extra_title ? null : picked?.chapter].filter(Boolean).join(" · ");
    }
    function queueNamingPreview() {
      clearTimeout(state.namingTimer);
      state.namingAbort?.abort();
      const result = state.resolved, version = ++state.namingRequest;
      if (!result) return;
      const output = $("[data-naming-preview]");
      const preferences = namingOptions();
      updateNamingHint(result, preferences);
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
    function updateCount({preserveFormats = false, preferredEpisodeId} = {}) {
      const count = selectedIds().length;
      selectedCount.textContent = `已选 ${count} 集`;
      $("[data-check-batch]").hidden = count < 2;
      if (!preserveFormats && count > 1 && !state.batchPlan && !state.batchTarget && formatField.value !== DEFAULT_FORMAT) {
        const track = JSON.parse(formatField.dataset.formats || "[]").find(item => item.selector === formatField.value);
        if (track?.format_id) state.batchTarget = {key: trackTier(track), track};
      }
      if (!preserveFormats && count !== 1) formatField.value = DEFAULT_FORMAT;
      if (count > 1) formatHint.textContent = "体积包含每集实际匹配的视频轨和音轨。";
      else if (count === 1) formatHint.textContent = "已选 1 集；提交时使用该集可用格式。";
      else formatHint.textContent = "请选择剧集，或使用默认格式下载单个视频。";
      state.formatRequest += 1;
      if (!preserveFormats && count === 1) loadSelectedEpisodeFormat(selectedIds()[0], state.formatRequest);
      updateBatchQuality();
      if (state.resolved) stage("selecting"); summary();
      updateNamingEditor(preferredEpisodeId);
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
          const added = !state.selected.delete(id);
          if (added) state.selected.add(id);
          showEpisodeDetail(episode, title);
          updateEpisodeTiles();
          updateCount({preferredEpisodeId: added ? id : undefined});
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
      const cached = state.batchTracks.get(id) || state.episodeFormats.get(id);
      if (cached) {
        for (const item of cached) quality.append(new Option(formatLabel(item), item.selector));
        quality.dataset.loaded = "true";
      }
      quality.value = state.episodeChoices.get(id) || "";
      quality.addEventListener("focus", () => loadEpisodeChoices(episode, quality));
      quality.addEventListener("change", () => {
        if (quality.value) state.episodeChoices.set(id, quality.value);
        else state.episodeChoices.delete(id);
        updateBatchQuality();
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
        if (state.batchTracks.has(id)) return;
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
    function batchCatalogue(ids) {
      const tiers = new Map();
      for (const id of ids) {
        for (const track of state.batchTracks.get(id) || []) {
          const key = trackTier(track);
          if (!tiers.has(key)) tiers.set(key, {key, track, rates: []});
          tiers.get(key).rates.push(track.tbr);
        }
      }
      if (state.batchTarget && !tiers.has(state.batchTarget.key)) {
        tiers.set(state.batchTarget.key, {...state.batchTarget, rates: []});
      }
      const highest = tier => Math.max(0, Number(tier.track.tbr) || 0, ...tier.rates.map(Number).filter(Number.isFinite));
      const ordered = [...tiers.values()].sort((a, b) =>
        (Number(b.track.height) || 0) - (Number(a.track.height) || 0) || highest(b) - highest(a)
      );
      for (const tier of ordered) {
        const track = tier.track;
        const group = ordered.filter(other => other.track.height === track.height && trackFamily(other.track) === trackFamily(track));
        if (group.length > 1) {
          const rank = group.indexOf(tier);
          tier.label = rank === 0 ? "高码" : rank === 1 ? "标准" : "档位 " + (rank + 1);
        }
      }
      return ordered;
    }

    function batchRows(ids, selectedTier, catalogue) {
      const target = selectedTier?.track;
      const nextTier = target?.height && batchFallback.checked
        ? catalogue.slice(catalogue.indexOf(selectedTier) + 1).find(tier =>
          tier.track.height && trackFamily(tier.track) === trackFamily(target))
        : null;
      return ids.map(id => {
        const tracks = state.batchTracks.get(id);
        if (!tracks) return {id, reason: state.batchErrors.get(id) || "正在检测"};
        const override = state.episodeChoices.get(id);
        const baseline = target ? tracks.find(track => trackTier(track) === selectedTier.key)
          : tracks.find(track => track.height) || tracks[0];
        const exact = override ? tracks.find(track => track.selector === override || track.locked_selector === override) : baseline;
        const lower = !override && !exact && nextTier && tracks.find(track => trackTier(track) === nextTier.key);
        const track = exact || lower;
        return {id, track, reason: !track ? "目标档位缺失" : lower ? "缺档降一档" :
          override && track.locked_selector !== baseline?.locked_selector ? "单集指定" : ""};
      });
    }

    function batchTrackLabel(track, tier, rate) {
      const video = track.height && track.vcodec !== "none";
      return [
        video ? track.height + "P" : "仅音频",
        video && track.fps ? track.fps + "fps" : null,
        codecName(video ? track.vcodec : track.acodec), tier?.label,
        video ? track.dynamic_range : null, rate
      ].filter(Boolean).join(" · ");
    }

    function renderBatchWindow() {
      const rows = batchList.children;
      const height = rows[0]?.offsetHeight;
      const index = height ? Math.floor(batchList.scrollTop / height) + 1 : 1;
      const end = height ? Math.floor((batchList.scrollTop + batchList.clientHeight) / height) : rows.length;
      $("[data-batch-window]").textContent = rows.length > 4
        ? "显示 " + index + "–" + Math.max(index, Math.min(end, rows.length)) + " / " + rows.length + " 集 · 在列表内滚动" : "";
    }

    function renderBatchBreakdown(rows, catalogue) {
      const ordinary = rows.filter(row => row.track && !row.reason);
      // Highest-available can choose different formats per episode. Compare them to the first match.
      if (!state.batchTarget && ordinary.length) {
        const first = trackTier(ordinary[0].track);
        for (const row of ordinary) {
          if (trackTier(row.track) !== first) row.reason = "各集最高可用档位不同";
        }
      }
      const groups = {
        different: rows.filter(row => row.reason || !row.track),
        same: rows.filter(row => row.track && !row.reason)
      };
      const group = groups[state.batchGroup]?.length ? state.batchGroup : groups.different.length ? "different" : "same";
      const sameGroup = batchList.dataset.group === group;
      const previousScroll = sameGroup ? batchList.scrollTop : 0;
      batchList.dataset.group = group;
      for (const button of root.querySelectorAll("[data-batch-group]")) {
        const name = button.dataset.batchGroup;
        button.querySelector("b").textContent = groups[name].length;
        button.disabled = !groups[name].length;
        button.setAttribute("aria-pressed", String(name === group));
      }
      const episodes = new Map(state.resolved.episodes.map(item => [String(item.id), item]));
      const tiers = new Map(catalogue.map(tier => [tier.key, tier]));
      batchList.replaceChildren(...groups[group].map(({id, track, reason}) => {
        const episode = episodes.get(id);
        const number = episode.display_episode_number || episode.group_index;
        const title = episodeCategory(episode) === "正片" && number ? "第 " + number + " 集" : episode.title || id;
        const row = make("div", "dv2-batch-track");
        const header = make("div", "dv2-batch-track-head");
        header.append(make("strong", "", title));
        if (reason) header.append(make("small", "dv2-batch-reason", reason));
        if (reason) header.lastElementChild.title = reason;
        header.append(make("span", "dv2-batch-track-size", track ? formatResourceSize(track.size_with_audio) || "体积未知" : "待确认"));
        const description = track ? batchTrackLabel(track, tiers.get(trackTier(track)), formatBitrate(track.tbr) || "码率未知")
          : episode.title || "";
        const detail = make("div", "dv2-batch-track-detail", description);
        detail.title = description;
        row.append(header, detail);
        return row;
      }));
      batchList.scrollTop = previousScroll;
      renderBatchWindow();
    }

    function updateBatchQuality({retry = false} = {}) {
      const ids = selectedIds();
      const active = ids.length > 1 || Boolean(ids.length && state.batchTarget);
      $("[data-single-quality]").hidden = active;
      $("[data-batch-quality]").hidden = !active;
      $("[data-batch-review]").hidden = !active;
      $("[data-batch-totals]").hidden = !active;
      clearTimeout(state.batchTimer);
      if (!active) {
        state.batchPlan = null;
        summary();
        return;
      }
      if (retry) for (const id of ids) state.batchErrors.delete(id);
      const catalogue = batchCatalogue(ids);
      const selectedTier = catalogue.find(tier => tier.key === state.batchTarget?.key);
      const rows = batchRows(ids, selectedTier, catalogue);
      const total = combinedSize(rows);
      const knownSize = formatResourceSize(total.bytes);
      const title = selectedTier ? batchTrackLabel(selectedTier.track, selectedTier, bitrateRange(selectedTier.rates)) : "最高可用 · 最佳音轨";
      const ready = rows.every(row => row.track);
      state.batchPlan = {ready, title, selectors: Object.fromEntries(rows.filter(row => row.track).map(row => [row.id, row.track.locked_selector]))};
      const optionText = tier => {
        const size = combinedSize(batchRows(ids, tier, catalogue));
        const estimated = formatResourceSize(size.bytes);
        const label = tier ? batchTrackLabel(tier.track, tier, bitrateRange(tier.rates)) : "最高可用 · 最佳音轨";
        return label + " · " + (size.complete ? estimated : estimated ? "已知 " + estimated : "体积待确认");
      };
      batchFormat.replaceChildren(new Option(optionText(null), ""), ...catalogue.map(tier => new Option(optionText(tier), tier.key)));
      batchFormat.value = selectedTier?.key || "";
      batchFormat.disabled = !catalogue.length;
      state.batchCatalogue = catalogue;
      $("[data-batch-size]").textContent = total.complete ? knownSize : knownSize ? "已知 " + knownSize : "待确认";
      $("[data-batch-totals]").textContent = "已选 " + ids.length + " 集 · 每集按自己的档位和大小算 · " +
        (total.complete ? "本档合计 " + knownSize + " · 每集约 " + formatResourceSize(total.bytes / rows.length)
          : knownSize ? "已知部分 " + knownSize + " · 总体积待确认" : "总体积待确认");
      const failed = ids.filter(id => state.batchErrors.has(id)).length;
      const pending = ids.filter(id => !state.batchTracks.has(id) && !state.batchErrors.has(id)).length;
      $("[data-batch-status]").textContent = pending ? "正在检测画质 · 剩余 " + pending + " 集" :
        failed ? failed + " 集检测失败，可重新检测" : ready ? "" : "有剧集缺档，请调整画质或启用同编码降档。";
      $("[data-batch-retry]").hidden = !failed;
      renderBatchBreakdown(rows, catalogue);
      summary();
      if (pending && !state.batchProbe) state.batchTimer = setTimeout(probeBatchQuality, 180);
    }

    async function probeBatchQuality() {
      const resolved = state.resolved;
      const ids = selectedIds().filter(id => !state.batchTracks.has(id) && !state.batchErrors.has(id)).slice(0, TRACKS_PER_REQUEST);
      if (!resolved || !ids.length || !state.batchPlan || state.batchProbe) return;
      const controller = new AbortController();
      state.batchProbe = controller;
      try {
        const result = await api("/api/episodes/formats/batch", {
          method: "POST", signal: controller.signal, body: JSON.stringify({
            url: state.resolvedUrl, api_mode: resolved.api_mode || modeField.value, episode_ids: ids, detailed: true
          })
        });
        if (controller.signal.aborted || resolved !== state.resolved) return;
        const failures = new Map((result.failed_episodes || []).map(episode => [String(episode.id), episode.error]));
        for (const id of ids) {
          const tracks = result.episode_formats?.[id]?.filter(track => track.locked_selector);
          if (tracks?.length) {
            state.batchTracks.set(id, tracks);
            if (id === state.activeEpisodeId) {
              const quality = $(".dv2-episode-quality");
              quality?.replaceChildren(new Option("跟随统一画质", ""), ...tracks.map(track => new Option(formatLabel(track), track.selector)));
              if (quality) {
                quality.value = state.episodeChoices.get(id) || "";
                quality.dataset.loaded = "true";
              }
            }
          } else state.batchErrors.set(id, failures.get(id) || "未返回可用轨道");
        }
      } catch (error) {
        if (!controller.signal.aborted && resolved === state.resolved) for (const id of ids) state.batchErrors.set(id, error.message);
      } finally {
        if (state.batchProbe === controller) state.batchProbe = null;
        if (!controller.signal.aborted && resolved === state.resolved) updateBatchQuality();
      }
    }

    function resetBatchQuality() {
      clearTimeout(state.batchTimer);
      state.batchProbe?.abort();
      state.batchProbe = null;
      state.batchTracks.clear();
      state.batchErrors.clear();
      state.batchTarget = null;
      state.batchGroup = null;
      state.batchPlan = null;
      batchFallback.checked = false;
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
    async function parse() {
      clearError();
      const url = urlField.value.trim();
      if (!url) { showError("请先粘贴 B 站链接。"); urlField.focus(); return; }
      const request=++state.parseVersion, mode=modeField.value;
      rememberSelection();
      resetBatchQuality();
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
      if (fromHistory && result.episodes.length && !result.naming_items) {
        result.naming_items = savedEpisodeNamingFacts(result.episodes);
      }
      state.resolved = result;
      resetBatchQuality();
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
          if (state.batchPlan) {
            if (!state.batchPlan?.ready) throw new Error("请确认每集下载档位后再提交。");
            payload.episode_selectors = state.batchPlan.selectors;
          } else if (state.episodeChoices.has(ids[0])) {
            payload.format_selector = state.episodeChoices.get(ids[0]);
          }
          await api("/api/jobs/batch", {method: "POST", body: JSON.stringify(payload)});
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
      if (HISTORY_STATUSES.has(job.status)) addAction(actions, "删除记录", async () => {
        if (!window.confirm("删除这条下载历史？已下载的文件会保留。")) return;
        await deleteHistoryRecords("jobs", [job.id], $("[data-download-history-error]"),
          () => refreshJobs({force: true}));
      }, "is-danger");
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
      const host = $("[data-history]");
      const request = ++state.historyVersion;
      try {
        const items = await api("/api/history?limit=200");
        if (state.destroyed || request !== state.historyVersion) return;
        const groups = await groupHistory(items, async item => {
          try { return await api(`/api/history/${item.id}`); }
          catch { return null; }
        });
        if (state.destroyed || request !== state.historyVersion) return;
        host.replaceChildren();
        $("[data-history-count]").textContent = `${groups.length} 条`;
        if (!groups.length) host.append(make("p", "dv2-empty", "暂无解析记录"));
        for (const {item, ids} of groups) {
          const row = make("div", "dv2-history-item");
          const summary = make("div", "dv2-history-summary");
          summary.append(make("div", "", item.title || "未命名内容"));
          const date = new Date(item.updated_at || "");
          const time = Number.isNaN(date.getTime()) ? "时间未知" : parseHistoryTime.format(date);
          summary.append(make("span", "", `${modeLabel(item.api_mode)} · ${time}`));
          const actions = make("div", "dv2-history-actions");
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
          actions.append(restore);
          addAction(actions, "删除记录", async () => {
            if (!window.confirm(`删除「${item.title || "未命名内容"}」的 ${modeLabel(item.api_mode)} 解析记录？`)) return;
            await deleteHistoryRecords("history", ids, $("[data-parse-history-error]"), refreshHistory);
          }, "is-danger");
          row.append(summary, actions);
          host.append(row);
        }
      } catch (error) {
        if (!state.destroyed && request === state.historyVersion) {
          host.replaceChildren(make("p", "dv2-empty", error.message));
        }
      }
    }
    async function deleteHistoryRecords(collection, ids, message, refresh) {
      message.hidden = true;
      const results = await Promise.allSettled(ids.map(id => api(`/api/${collection}/${id}`, {method: "DELETE"})));
      await refresh();
      if (state.destroyed) return;
      const failed = results.filter(result => result.status === "rejected");
      if (failed.length) {
        message.textContent = `${failed.length} 条记录删除失败：${failed[0].reason.message}`;
        message.hidden = false;
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
      resetBatchQuality();
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
    listen($("[data-naming-mode]"), "change", () => updateNamingEditor());
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
    listen($("[data-all-parts] input"), "change", () => updateNamingEditor());
    listen($("[data-output]"), "input", summary);
    listen(modeField, "change", () => { ++state.parseVersion; if (urlField.value.trim()) parse(); });
    listen(selectAll, "click", togglePageSelection);
    listen($("[data-select-invert]"), "click", () => setSelection("invert"));
    listen($("[data-select-none]"), "click", () => setSelection("none"));
    listen($("[data-check-batch]"), "click", () => updateBatchQuality({retry: true}));
    listen($("[data-batch-retry]"), "click", () => updateBatchQuality({retry: true}));
    listen(batchFallback, "change", () => {state.batchGroup = null; updateBatchQuality();});
    listen(batchFormat, "change", () => {
      state.batchTarget = state.batchCatalogue.find(tier => tier.key === batchFormat.value) || null;
      state.batchGroup = null;
      updateBatchQuality();
    });
    listen(batchList, "scroll", renderBatchWindow);
    listen($(".dv2-batch-breakdown"), "toggle", renderBatchWindow);
    for (const button of root.querySelectorAll("[data-batch-group]")) {
      listen(button, "click", () => {state.batchGroup = button.dataset.batchGroup; updateBatchQuality();});
    }
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
        resetBatchQuality();
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
