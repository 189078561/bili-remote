const csrf = document.querySelector('meta[name="csrf-token"]').content;
const jobsBody = document.querySelector("#jobs-body");
const historyBody = document.querySelector("#history-body");
const modal = document.querySelector("#modal");
const modalContent = document.querySelector("#modal-content");
const formatSelect = document.querySelector("#format-selector");
const formatHint = document.querySelector("#format-hint");
const batchCheckButton = document.querySelector("#check-batch-formats");
const apiModeSelect = document.querySelector("#api-mode");
const episodePicker = document.querySelector("#episode-picker");
const episodeGroups = document.querySelector("#episode-groups");
const episodeSelected = document.querySelector("#episode-selected");
const episodeSortSelect = document.querySelector("#episode-sort");
const allPartsRow = document.querySelector("#all-parts-row");
let resolvedData = null;
let resolvedUrl = "";
let currentQrSession = null;
let qrTimer = null;
let qrMode = "web";
let formatEpisodeId = null;
let formatRequestSeq = 0;
let formatRefreshTimer = null;
let batchFormatProfiles = new Map();
let episodeSortOrder = "asc";
let currentView = "files";
let settingsReturnView = "files";
let appSettings = null;
let appSettingsPromise = null;
const modalStack = [];
// Keep background tools mounted while a nested picker/preview is open.
// Task completion handlers resolve their original controls by ID.
const modalStackDock = document.createElement("div");
modalStackDock.hidden = true;
document.body.append(modalStackDock);
let downloadDefaults = {
  single: document.querySelector("#single-template")?.value || "",
  playlist: document.querySelector("#playlist-template")?.value || "",
  output: ""
};
const DEFAULT_FORMAT_SELECTOR = "bv*+ba/b";
const MIN_FORMAT_HEIGHT = 1080;
const BUSINESS_VIEWS = new Set([
  "home",
  "files",
  "download",
  "uploads",
  "screenshots",
  "release-info",
  "torrents",
  "publish"
]);

function businessView(value, fallback = "files") {
  return BUSINESS_VIEWS.has(value) ? value : fallback;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (options.method && options.method !== "GET") {
    headers.set("X-CSRF-Token", csrf);
  }
  const response = await fetch(path, {...options, headers});
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : {};
  if (response.status === 401) {
    window.location.assign("/");
    throw new Error("登录已失效");
  }
  if (!response.ok) {
    let detail = Array.isArray(data.detail)
      ? data.detail.map(item => item.msg || String(item)).join("；")
      : data.detail;
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      detail = detail.message || detail.code || JSON.stringify(detail);
    }
    const error = new Error(detail || `请求失败（${response.status}）`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function showModal(content, {stack = false} = {}) {
  const previous = modalContent.firstElementChild;
  if (stack && !modal.classList.contains("hidden") && previous) {
    modalStack.push(previous);
    modalStackDock.append(previous);
  } else if (!stack) {
    for (const panel of [previous, ...modalStack].filter(Boolean)) {
      if (panel === content) continue;
      document.dispatchEvent(new CustomEvent("app:modal-close", {
        detail: {panel, stacked: false, replaced: true}
      }));
      panel.remove();
    }
    modalStack.length = 0;
  }
  modalContent.replaceChildren(content);
  modal.classList.remove("hidden");
}

function closeModal() {
  const current = modalContent.firstElementChild;
  const previous = modalStack.pop() || null;
  document.dispatchEvent(new CustomEvent("app:modal-close", {
    detail: {panel: current, stacked: Boolean(previous)}
  }));
  if (previous) {
    modalContent.replaceChildren(previous);
    modal.classList.remove("hidden");
    return;
  }
  stopQrPolling();
  modal.classList.add("hidden");
  modalContent.replaceChildren();
}

function switchAppView(name) {
  const legacyTool = ["uploads", "torrents", "release-info", "screenshots"].includes(name) ? name : null;
  if (legacyTool) name = "files";
  currentView = name;
  document.querySelectorAll(".app-view").forEach(view => {
    view.classList.toggle("hidden", view.id !== `${name}-view`);
  });
  document.querySelectorAll(".app-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.view === name);
  });
  document.querySelector("#settings-entry").classList.toggle("active", name === "settings");
  document.dispatchEvent(new CustomEvent("app:view", {detail: {view: name}}));
  if (name === "settings" && window.SettingsPage) window.SettingsPage.load();
  if (legacyTool) setTimeout(() => window.FileTools?.open(legacyTool), 0);
}

function openBusinessView(name, {push = true} = {}) {
  if (!BUSINESS_VIEWS.has(name)) return;
  if (currentView === "settings" && window.SettingsPage && !window.SettingsPage.confirmLeave()) return;
  if (push) window.history.pushState({view: name}, "", "/");
  switchAppView(name);
}

function openSettingsPage() {
  if (currentView !== "settings") {
    settingsReturnView = businessView(currentView, appSettings?.app_default_view || "files");
    sessionStorage.setItem("settingsReturnView", settingsReturnView);
    window.history.pushState(
      {view: "settings", returnView: settingsReturnView, fromApp: true},
      "",
      "/settings"
    );
  }
  switchAppView("settings");
}

function leaveSettingsPage() {
  if (window.SettingsPage && !window.SettingsPage.confirmLeave()) return;
  const fallback = businessView(
    window.history.state?.returnView
      || sessionStorage.getItem("settingsReturnView")
      || appSettings?.app_default_view,
    "files"
  );
  if (window.history.state?.view === "settings" && window.history.state?.fromApp) {
    window.history.back();
    return;
  }
  window.history.replaceState({view: fallback}, "", "/");
  switchAppView(fallback);
}

function applyAppSettings(data) {
  const previous = downloadDefaults;
  appSettings = data;
  const values = data?.values || {};
  downloadDefaults = {
    single: data?.single_output_template || "",
    playlist: data?.playlist_output_template || "",
    output: data?.download_default_output_dir || ""
  };
  const single = document.querySelector("#single-template");
  const playlist = document.querySelector("#playlist-template");
  const template = document.querySelector("#job-template");
  const output = document.querySelector("#job-output-dir");
  if (single) single.value = downloadDefaults.single;
  if (playlist) playlist.value = downloadDefaults.playlist;
  if (template && (!template.value.trim() || template.value.trim() === previous.single.trim())) {
    template.value = downloadDefaults.single;
  }
  if (output) {
    output.placeholder = downloadDefaults.output
      ? `留空使用：${downloadDefaults.output}`
      : "可选，相对于下载根目录";
  }
  if (window.FileBrowser?.setDefaults) {
    window.FileBrowser.setDefaults(values);
  }
}

async function loadAppSettings() {
  if (appSettingsPromise) return appSettingsPromise;
  appSettingsPromise = (async () => {
    try {
      applyAppSettings(await api("/api/settings"));
    } catch (error) {
      appSettingsPromise = null;
      const status = document.querySelector("#download-error");
      if (status) status.textContent = `读取默认设置失败：${error.message}`;
    }
  })();
  return appSettingsPromise;
}

function stopQrPolling() {
  if (qrTimer) {
    clearInterval(qrTimer);
    qrTimer = null;
  }
  currentQrSession = null;
}

function setBusy(button, busy) {
  button.disabled = busy;
  button.classList.toggle("is-busy", busy);
  button.setAttribute("aria-busy", busy ? "true" : "false");
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return "—";
  const value = Number(bytes);
  if (value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

function formatSpeed(speed) {
  if (!speed || Number(speed) <= 0) return "—";
  return `${formatBytes(speed)}/s`;
}

function formatEta(seconds) {
  if (seconds === null || seconds === undefined || Number(seconds) < 0) return "—";
  const total = Math.round(Number(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours) return `${hours}小时${String(minutes).padStart(2, "0")}分`;
  if (minutes) return `${minutes}分${String(rest).padStart(2, "0")}秒`;
  return `${rest}秒`;
}

function statusText(status) {
  return {
    queued: "排队中",
    running: "下载中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
    auth_required: "需要登录"
  }[status] || status;
}

function modeText(mode) {
  return mode === "tv" ? "TV 线路" : "Web 线路";
}

async function loadAccount() {
  try {
    const data = await api("/api/bili/status");
    const dot = document.querySelector("#account-dot");
    const webStatus = document.querySelector("#web-account-status");
    const tvStatus = document.querySelector("#tv-account-status");
    const loginButton = document.querySelector("#bili-login");
    const logoutButton = document.querySelector("#bili-logout");
    const tvLoginButton = document.querySelector("#tv-login");
    const tvLogoutButton = document.querySelector("#tv-logout");
    dot.classList.toggle("online", data.logged_in);
    logoutButton.classList.toggle("hidden", !data.logged_in);
    tvLogoutButton.classList.toggle("hidden", !data.tv_logged_in);
    loginButton.textContent = data.logged_in ? "更换账号" : "扫码登录";
    tvLoginButton.textContent = data.tv_logged_in ? "更换 TV 账号" : "TV 扫码登录";

    if (data.logged_in) {
      const vip = data.vip_status ? "大会员" : "普通账号";
      webStatus.textContent = `${data.account_name || "B 站账号"} · ${vip}`;
    } else {
      webStatus.textContent = "未登录。大会员画质需要先扫码登录。";
    }
    if (data.tv_logged_in) {
      tvStatus.textContent = `${data.tv_account_name || "TV 账号"} · 已连接`;
    } else {
      tvStatus.textContent = "未登录。TV 线路用于无水印片源。";
    }
  } catch (error) {
    document.querySelector("#web-account-status").textContent = error.message;
  }
}

async function openQrLogin(mode) {
  try {
    const path = mode === "tv" ? "/api/bili/tv/qr" : "/api/bili/qr";
    const data = await api(path, {method: "POST"});
    qrMode = mode;
    currentQrSession = data.session_id;
    const box = element("div", "qr-box");
    box.append(element("h2", null, mode === "tv" ? "扫码登录 B 站 TV" : "扫码登录 B 站"));
    const image = element("img");
    image.alt = "B 站登录二维码";
    image.src = data.qr_image;
    box.append(image);
    const status = element("p", "muted", data.message);
    status.id = "qr-status";
    box.append(status);
    showModal(box);
    qrTimer = setInterval(pollQr, 1800);
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function pollQr() {
  if (!currentQrSession) return;
  try {
    const base = qrMode === "tv" ? "/api/bili/tv/qr" : "/api/bili/qr";
    const data = await api(`${base}/${encodeURIComponent(currentQrSession)}`);
    const label = document.querySelector("#qr-status");
    if (label) label.textContent = data.message;
    if (data.status === "authorized") {
      stopQrPolling();
      closeModal();
      await loadAccount();
    } else if (data.status === "expired") {
      stopQrPolling();
    }
  } catch (error) {
    const label = document.querySelector("#qr-status");
    if (label) label.textContent = error.message;
  }
}

async function resolveVideo() {
  const url = document.querySelector("#video-url").value.trim();
  if (!url) {
    document.querySelector("#download-error").textContent = "请先填写链接";
    return;
  }
  await resolveUrl(url, apiModeSelect.value);
}

async function resolveUrl(url, mode) {
  const button = document.querySelector("#resolve-button");
  const error = document.querySelector("#download-error");
  error.textContent = "";
  setBusy(button, true);
  button.textContent = "解析中…";
  try {
    resolvedData = await api("/api/resolve", {
      method: "POST",
      body: JSON.stringify({url, api_mode: mode})
    });
    resolvedUrl = url;
    renderResolution(resolvedData);
    const base = resolvedData.episodes?.length || resolvedData.is_playlist
      ? document.querySelector("#playlist-template").value
      : document.querySelector("#single-template").value;
    document.querySelector("#job-template").value = base;
    document.querySelector("#all-parts").checked = Boolean(
      resolvedData.is_playlist && !resolvedData.episodes?.length
    );
    await loadHistory();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    setBusy(button, false);
    button.textContent = "解析";
  }
}

function renderResolution(data) {
  const result = document.querySelector("#resolve-result");
  result.replaceChildren();
  result.classList.remove("hidden");
  result.append(element("strong", null, data.title || "已解析"));
  const episodeCount = Array.isArray(data.episodes) ? data.episodes.length : 0;
  const meta = [
    data.uploader,
    episodeCount ? `${episodeCount} 集` : null,
    data.is_playlist && data.playlist_count ? `正片 ${data.playlist_count} 集` : null,
    data.duration ? `${Math.round(data.duration / 60)} 分钟` : null,
    modeText(data.api_mode),
    data.api_mode === "tv"
      ? (data.tv_logged_in ? "TV 已登录" : "TV 未登录")
      : (data.bili_logged_in ? "已使用账号登录态" : "未登录，仅公开画质")
  ].filter(Boolean);
  result.append(element("div", "resolve-meta", meta.join(" · ")));
  if (data.warning) result.append(element("div", "resolve-warning", data.warning));

  cancelFormatRefresh();
  setFormatOptions(data.formats || []);
  formatEpisodeId = data.default_episode_id && data.formats?.length
    ? String(data.default_episode_id)
    : null;
  renderEpisodes(data);
  if (!episodeCount) setFormatHint("格式列表来自当前解析结果");
}

function codecKey(value) {
  const codec = String(value || "").toLowerCase();
  if (codec.includes("av01")) return "av1";
  if (codec.includes("hev") || codec.includes("hvc")) return "hevc";
  if (codec.includes("avc")) return "avc";
  return "any";
}

function dynamicRangeKey(value) {
  const range = String(value || "").toLowerCase();
  if (range.includes("dolby") || range.includes("杜比") || range === "dv" || range === "dovi") {
    return "DV";
  }
  if (range.includes("hlg")) return "HLG";
  if (range.includes("hdr")) return "HDR10";
  if (range.includes("sdr")) return "SDR";
  return "any";
}

function codecLabel(value, isAudio = false) {
  const codec = String(value || "").toLowerCase();
  if (codec.includes("av01")) return "AV1";
  if (codec.includes("hev") || codec.includes("hvc")) return "H.265 (HEVC)";
  if (codec.includes("avc")) return "H.264 (AVC)";
  if (codec.includes("mp4a")) return "AAC";
  if (codec.includes("ec-3") || codec.includes("ac-3")) return "Dolby";
  if (codec.includes("flac")) return "FLAC";
  if (codec.includes("opus")) return "Opus";
  return isAudio ? "未知音频编码" : "未知视频编码";
}

function heightLabel(height) {
  const value = Number(height) || 0;
  if (value >= 4320) return "8K";
  if (value >= 2160) return "4K";
  if (value >= 1440) return "2K";
  return value ? `${value}P` : "未知分辨率";
}

function bitrateLabel(tbr, isAudio = false) {
  const value = Number(tbr);
  if (!Number.isFinite(value) || value <= 0) return null;
  if (isAudio) return `${Math.round(value)} kbps`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)} Mbps`;
  return `${Math.round(value)} kbps`;
}

function formatVideoOptionLabel(format) {
  const parts = [
    heightLabel(format.height),
    format.fps ? `${Math.round(Number(format.fps))}fps` : null,
    codecLabel(format.vcodec),
    format.dynamic_range && format.dynamic_range !== "SDR" ? format.dynamic_range : null,
    format.format_note,
    bitrateLabel(format.tbr),
    format.filesize ? formatBytes(format.filesize) : null
  ].filter(Boolean);
  return `${parts.join(" · ")} [${format.format_id}]`;
}

function formatAudioOptionLabel(format) {
  const parts = [
    codecLabel(format.acodec, true),
    bitrateLabel(format.tbr, true),
    format.format_note,
    format.filesize ? formatBytes(format.filesize) : null
  ].filter(Boolean);
  return `${parts.join(" · ")} [${format.format_id}]`;
}

function formatGroupLabel(format) {
  if (!format.height) return "仅音频";
  return `${heightLabel(format.height)} (${format.width || "?"}x${format.height})`;
}

function appendGroupedFormatOptions(formats) {
  const groups = new Map();
  for (const format of formats || []) {
    if (!format?.selector) continue;
    const group = formatGroupLabel(format);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(format);
  }
  for (const [group, items] of groups) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group;
    for (const format of items) {
      const isAudio = !format.height || format.vcodec === "none";
      optgroup.append(new Option(
        isAudio ? formatAudioOptionLabel(format) : formatVideoOptionLabel(format),
        format.selector
      ));
    }
    formatSelect.append(optgroup);
  }
}

function setFormatOptions(formats) {
  batchFormatProfiles = new Map();
  formatSelect.replaceChildren();
  formatSelect.append(new Option("最高可用视频 + 最佳音轨", DEFAULT_FORMAT_SELECTOR));
  const ordered = [...(formats || [])].filter(format => (
    !Number(format?.height) || Number(format.height) >= MIN_FORMAT_HEIGHT
  )).sort((left, right) => (
    (Number(right.height) || 0) - (Number(left.height) || 0)
    || (Number(right.fps) || 0) - (Number(left.fps) || 0)
    || (Number(right.tbr) || 0) - (Number(left.tbr) || 0)
  ));
  appendGroupedFormatOptions(ordered);
}

function batchProfilesFromFormats(formats) {
  const profiles = new Map();
  for (const format of formats || []) {
    if (!format?.height || !format.vcodec || format.vcodec === "none") continue;
    if (Number(format.height) < MIN_FORMAT_HEIGHT) continue;
    const fpsHigh = format.fps === null || format.fps === undefined
      ? null
      : Number(format.fps) >= 50;
    const profile = {
      height: Number(format.height),
      fps_high: fpsHigh,
      vcodec: codecKey(format.vcodec),
      dynamic_range: dynamicRangeKey(format.dynamic_range)
    };
    const key = [
      profile.height,
      profile.fps_high,
      profile.vcodec,
      profile.dynamic_range
    ].join("|");
    const existing = profiles.get(key);
    if (!existing || Number(format.tbr) > Number(existing.sample.tbr)) {
      profiles.set(key, {profile, sample: format});
    }
  }
  return [...profiles.values()].sort((left, right) => (
    right.profile.height - left.profile.height
    || Number(Boolean(right.profile.fps_high)) - Number(Boolean(left.profile.fps_high))
    || Number(right.sample.tbr || 0) - Number(left.sample.tbr || 0)
  ));
}

function setBatchFormatOptions(profiles, meta = null) {
  batchFormatProfiles = new Map();
  formatSelect.replaceChildren();
  formatSelect.append(new Option("最高可用视频 + 最佳音轨（批量）", DEFAULT_FORMAT_SELECTOR));

  const ordered = (profiles || []).map(item => {
    const profile = item.profile || {
      height: Number(item.height),
      fps_high: item.fps_high,
      vcodec: item.vcodec,
      dynamic_range: item.dynamic_range
    };
    return {
      profile: {...profile, dynamic_range: dynamicRangeKey(profile.dynamic_range)},
      sample: item.sample || item,
      coverage: item.coverage
    };
  }).filter(item => item.profile.height >= MIN_FORMAT_HEIGHT).sort((left, right) => (
    right.profile.height - left.profile.height
    || Number(Boolean(right.profile.fps_high)) - Number(Boolean(left.profile.fps_high))
    || Number(right.coverage || 0) - Number(left.coverage || 0)
    || Number(right.sample.tbr || 0) - Number(left.sample.tbr || 0)
  ));
  const groups = new Map();
  for (const item of ordered) {
    const group = `${heightLabel(item.profile.height)} (${item.sample.width || "?"}x${item.profile.height})`;
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(item);
  }
  for (const [group, items] of groups) {
    const optgroup = document.createElement("optgroup");
    optgroup.label = group;
    for (const item of items) {
      const sample = item.sample;
      const checked = Number(meta?.checked) || 0;
      const coverage = Number(item.coverage);
      const coverageText = checked > 0 && Number.isFinite(coverage)
        ? (coverage >= checked ? `检测 ${checked} 集全部可用` : `${coverage}/${checked} 集可用`)
        : null;
      const label = [
        heightLabel(item.profile.height),
        sample.fps ? `${Math.round(Number(sample.fps))}fps` : null,
        codecLabel(sample.vcodec),
        item.profile.dynamic_range !== "SDR" && item.profile.dynamic_range !== "any"
          ? item.profile.dynamic_range
          : null,
        bitrateLabel(sample.tbr),
        coverageText,
        "最佳音轨"
      ].filter(Boolean).join(" · ");
      const value = `batch:${batchFormatProfiles.size}`;
      batchFormatProfiles.set(value, item.profile);
      optgroup.append(new Option(label, value));
    }
    formatSelect.append(optgroup);
  }
}

function cancelFormatRefresh() {
  formatRequestSeq += 1;
  if (formatRefreshTimer) {
    clearTimeout(formatRefreshTimer);
    formatRefreshTimer = null;
  }
  formatSelect.disabled = false;
  batchCheckButton.classList.add("hidden");
  batchCheckButton.disabled = false;
  batchCheckButton.textContent = "检测所选集数";
}

function setFormatHint(text, isError = false) {
  formatHint.textContent = text || "";
  formatHint.classList.toggle("error", Boolean(isError));
}

function episodeTitle(episode) {
  return episode.title || `第 ${episode.group_index || episode.index || "?"} 集`;
}

function episodeUrl(episode) {
  return episode.url || `https://www.bilibili.com/bangumi/play/ep${episode.id}`;
}

function scheduleEpisodeFormats(ids) {
  if (!resolvedData?.episodes?.length) return;
  const seq = ++formatRequestSeq;
  if (formatRefreshTimer) {
    clearTimeout(formatRefreshTimer);
    formatRefreshTimer = null;
  }
  if (ids.length !== 1) {
    formatSelect.disabled = false;
    formatEpisodeId = null;
    if (ids.length) {
      setBatchFormatOptions(batchProfilesFromFormats(resolvedData.formats || []));
      setFormatHint(
        `已选 ${ids.length} 集：当前按解析样本生成画质偏好；可检测所选集数的实际可用格式`
      );
      batchCheckButton.classList.remove("hidden");
      batchCheckButton.disabled = false;
      batchCheckButton.textContent = "检测所选集数";
    } else {
      setFormatOptions([]);
      setFormatHint("未选择集数：使用统一默认格式；选择单集可查看该集专属格式");
      batchCheckButton.classList.add("hidden");
    }
    return;
  }
  const episode = resolvedData.episodes.find(item => String(item.id) === ids[0]);
  if (!episode) return;
  batchCheckButton.classList.add("hidden");
  if (formatEpisodeId === ids[0]) {
    formatSelect.disabled = false;
    setFormatHint(`当前格式来自：${episodeTitle(episode)}`);
    return;
  }
  formatSelect.disabled = true;
  setFormatHint(`正在获取「${episodeTitle(episode)}」的下载格式…`);
  formatRefreshTimer = setTimeout(() => {
    formatRefreshTimer = null;
    loadEpisodeFormats(seq, episode);
  }, 180);
}

async function loadEpisodeFormats(seq, episode) {
  try {
    const data = await api("/api/episodes/formats", {
      method: "POST",
      body: JSON.stringify({
        url: episodeUrl(episode),
        api_mode: resolvedData?.api_mode || apiModeSelect.value
      })
    });
    if (seq !== formatRequestSeq) return;
    setFormatOptions(data.formats || []);
    formatEpisodeId = String(episode.id);
    formatSelect.disabled = false;
    if (data.formats?.length) {
      setFormatHint(`当前格式来自：${episodeTitle(episode)}（${data.formats.length} 个可用格式）`);
    } else {
      setFormatHint(`「${episodeTitle(episode)}」未返回专属格式，将使用统一默认格式`, true);
    }
  } catch (error) {
    if (seq !== formatRequestSeq) return;
    setFormatOptions([]);
    formatEpisodeId = String(episode.id);
    formatSelect.disabled = false;
    setFormatHint(
      `「${episodeTitle(episode)}」格式获取失败，将使用统一默认格式：${error.message}`,
      true
    );
  }
}

async function checkBatchFormats() {
  const ids = selectedEpisodeIds();
  if (!resolvedData?.episodes?.length || ids.length < 2) return;
  const seq = ++formatRequestSeq;
  if (formatRefreshTimer) {
    clearTimeout(formatRefreshTimer);
    formatRefreshTimer = null;
  }
  formatEpisodeId = null;
  formatSelect.disabled = true;
  batchCheckButton.disabled = true;
  batchCheckButton.textContent = "检测中…";
  setFormatHint(`正在抽样检测所选集数的下载格式，请稍候…`);
  try {
    const data = await api("/api/episodes/formats/batch", {
      method: "POST",
      body: JSON.stringify({
        url: resolvedUrl || document.querySelector("#video-url").value.trim(),
        episode_ids: ids,
        api_mode: resolvedData.api_mode || apiModeSelect.value
      })
    });
    if (seq !== formatRequestSeq) return;
    const profiles = data.profiles?.length
      ? data.profiles
      : batchProfilesFromFormats(resolvedData.formats || []);
    setBatchFormatOptions(profiles, data.profiles?.length ? data : null);
    const checked = Number(data.checked) || 0;
    const selectedCount = Number(data.selected_count) || ids.length;
    const parts = [`已检测 ${checked}/${selectedCount} 集`];
    if (data.sampled) parts.push("抽样");
    if (data.failed) parts.push(`${data.failed} 集失败`);
    if (data.profiles?.length) {
      parts.push("缺失画质自动就近回退");
      setFormatHint(parts.join(" · "));
    } else {
      parts.push("继续使用解析样本");
      setFormatHint(parts.join(" · "), true);
    }
  } catch (error) {
    if (seq !== formatRequestSeq) return;
    setBatchFormatOptions(batchProfilesFromFormats(resolvedData.formats || []));
    setFormatHint(`检测失败，继续使用解析样本：${error.message}`, true);
  } finally {
    if (seq === formatRequestSeq) {
      formatSelect.disabled = false;
      batchCheckButton.disabled = false;
      batchCheckButton.textContent = "重新检测";
    }
  }
}

function episodeOrderValue(episode) {
  const value = Number(episode.group_index ?? episode.index ?? 0);
  return Number.isFinite(value) ? value : 0;
}

function renderEpisodes(data, {preserveSelection = false, refreshFormats = true} = {}) {
  const selected = preserveSelection ? new Set(selectedEpisodeIds()) : new Set();
  const scrollTop = preserveSelection ? episodeGroups.scrollTop : 0;
  const openGroups = new Set(
    [...episodeGroups.querySelectorAll("details.episode-group[open]")]
      .map(section => section.dataset.group)
  );
  episodeGroups.replaceChildren();
  const episodes = Array.isArray(data.episodes) ? data.episodes : [];
  episodePicker.classList.toggle("hidden", !episodes.length);
  syncAllPartsVisibility();
  if (!episodes.length) {
    episodeSelected.textContent = "已选 0 集";
    return;
  }
  const groups = new Map();
  for (const episode of episodes) {
    const group = episode.group || "其他";
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(episode);
  }
  for (const [group, items] of groups) {
    const orderedItems = [...items].sort((left, right) => (
      episodeOrderValue(left) - episodeOrderValue(right)
    ));
    if (episodeSortOrder === "desc") orderedItems.reverse();
    const isMain = group === "正片";
    const section = isMain
      ? element("section", "episode-group")
      : element("details", "episode-group");
    section.dataset.group = group;
    const heading = element(
      isMain ? "div" : "summary",
      "episode-group-heading",
      `${group} · ${items.length} 集`
    );
    section.append(heading);
    if (!isMain) {
      section.open = openGroups.has(group);
    }
    const grid = element("div", "episode-grid");
    for (const episode of orderedItems) {
      const label = element("label", "episode-item");
      const checkbox = element("input", "episode-checkbox");
      checkbox.type = "checkbox";
      checkbox.value = String(episode.id);
      checkbox.dataset.group = group;
      checkbox.checked = preserveSelection
        ? selected.has(String(episode.id))
        : String(episode.id) === String(data.default_episode_id || "");
      checkbox.addEventListener("change", updateEpisodeCount);
      label.append(checkbox);
      const body = element("span", "episode-body");
      body.append(element("span", "episode-title", episode.title || `第 ${episode.index} 集`));
      const details = [
        episode.duration ? formatEta(episode.duration) : null,
        episode.badge
      ].filter(Boolean);
      if (details.length) body.append(element("span", "episode-meta", details.join(" · ")));
      label.append(body);
      grid.append(label);
    }
    section.append(grid);
    episodeGroups.append(section);
  }
  episodeGroups.scrollTop = scrollTop;
  if (refreshFormats) {
    updateEpisodeCount();
  } else {
    episodeSelected.textContent = `已选 ${selected.size} 集`;
  }
}

function changeEpisodeSort() {
  episodeSortOrder = episodeSortSelect.value === "desc" ? "desc" : "asc";
  if (resolvedData?.episodes?.length) {
    renderEpisodes(resolvedData, {preserveSelection: true, refreshFormats: false});
  }
}

function syncAllPartsVisibility() {
  const hide = Boolean(resolvedData?.episodes?.length) || apiModeSelect.value === "tv";
  allPartsRow.classList.toggle("hidden", hide);
  if (hide) document.querySelector("#all-parts").checked = false;
}

function clearResolution() {
  resolvedData = null;
  resolvedUrl = "";
  cancelFormatRefresh();
  formatEpisodeId = null;
  setFormatOptions([]);
  setFormatHint("解析后显示可用下载格式");
  document.querySelector("#resolve-result").classList.add("hidden");
  episodePicker.classList.add("hidden");
  episodeGroups.replaceChildren();
  episodeSelected.textContent = "已选 0 集";
  syncAllPartsVisibility();
}

function updateEpisodeCount() {
  const ids = selectedEpisodeIds();
  episodeSelected.textContent = `已选 ${ids.length} 集`;
  scheduleEpisodeFormats(ids);
}

function selectedEpisodeIds() {
  return [...document.querySelectorAll(".episode-checkbox:checked")].map(node => node.value);
}

function selectMainEpisodes() {
  for (const node of document.querySelectorAll('.episode-checkbox[data-group="正片"]')) {
    node.checked = true;
  }
  updateEpisodeCount();
}

function clearEpisodeSelection() {
  for (const node of document.querySelectorAll(".episode-checkbox")) {
    node.checked = false;
  }
  updateEpisodeCount();
}

async function createJob(event) {
  event.preventDefault();
  const error = document.querySelector("#download-error");
  error.textContent = "";
  const button = event.currentTarget.querySelector("button[type=submit]");
  const url = document.querySelector("#video-url").value.trim();
  const outputDir = document.querySelector("#job-output-dir").value.trim();
  const template = document.querySelector("#job-template").value.trim();
  const mode = apiModeSelect.value;
  setBusy(button, true);
  try {
    if (resolvedData?.episodes?.length) {
      const episodeIds = selectedEpisodeIds();
      if (!episodeIds.length) throw new Error("请至少选择一集");
      if (episodeIds.length > 600) throw new Error("单次最多创建 600 个任务");
      if (formatSelect.disabled) {
        throw new Error(
          episodeIds.length === 1
            ? "正在获取该集下载格式，请稍候再提交"
            : "正在检测所选集的下载格式，请稍候再提交"
        );
      }
      const selector = episodeIds.length === 1
        ? formatSelect.value
        : DEFAULT_FORMAT_SELECTOR;
      const profile = episodeIds.length > 1
        ? batchFormatProfiles.get(formatSelect.value)
        : null;
      const payload = {
        url: resolvedUrl || url,
        episode_ids: episodeIds,
        api_mode: resolvedData.api_mode || mode,
        format_selector: selector,
        output_template: template,
        output_dir: outputDir
      };
      if (profile) payload.format_profile = profile;
      const result = await api("/api/jobs/batch", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      if (result.count) alert(`已创建 ${result.count} 个集数任务`);
    } else {
      await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          url,
          api_mode: mode,
          format_selector: formatSelect.value,
          all_parts: document.querySelector("#all-parts").checked,
          output_template: template,
          output_dir: outputDir,
          title: resolvedData?.title || null,
          uploader: resolvedData?.uploader || null
        })
      });
      document.querySelector("#video-url").value = "";
      document.querySelector("#job-output-dir").value = "";
      document.querySelector("#resolve-result").classList.add("hidden");
      resolvedData = null;
      resolvedUrl = "";
    }
    await loadJobs();
  } catch (err) {
    error.textContent = err.message;
  } finally {
    setBusy(button, false);
  }
}

async function loadJobs() {
  try {
    const jobs = await api("/api/jobs?limit=500");
    window.AppTaskCenter?.receive("download", jobs);
    jobsBody.replaceChildren();
    if (!jobs.length) {
      const row = element("tr");
      const cell = element("td", "empty", "还没有下载任务");
      cell.colSpan = 5;
      row.append(cell);
      jobsBody.append(row);
      return;
    }
    for (const job of jobs) jobsBody.append(renderJob(job));
  } catch (error) {
    window.AppTaskCenter?.failed("download", error);
    jobsBody.replaceChildren();
    const row = element("tr");
    const cell = element("td", "empty", error.message);
    cell.colSpan = 5;
    row.append(cell);
    jobsBody.append(row);
  }
}

function progressSummary(job) {
  const percent = Math.max(0, Math.min(100, Number(job.progress) || 0));
  if (job.status === "queued") return "等待开始";
  if (job.status === "completed") {
    return job.total_bytes ? `已完成 · ${formatBytes(job.total_bytes)}` : "已完成";
  }
  if (job.status === "failed" || job.status === "auth_required") {
    return `${Math.round(percent)}% · ${statusText(job.status)}`;
  }
  if (job.status === "cancelled") {
    return `${Math.round(percent)}% · 已取消`;
  }
  const downloaded = Number(job.downloaded_bytes) || 0;
  const total = Number(job.total_bytes) || 0;
  const size = total > 0
    ? `${formatBytes(downloaded)} / ${formatBytes(total)}`
    : formatBytes(downloaded);
  const parts = [`${Math.round(percent)}%`, formatSpeed(job.speed), size];
  if (job.eta !== null && job.eta !== undefined) parts.push(`剩余 ${formatEta(job.eta)}`);
  return parts.join(" · ");
}

function renderJob(job) {
  const row = element("tr");
  row.append(element("td", null, `#${job.id}`));
  const content = element("td");
  const titleLine = element("div", "job-title-line");
  titleLine.append(element("div", "job-title", job.title || "等待解析"));
  titleLine.append(element("span", `mode-badge ${job.api_mode || "web"}`, modeText(job.api_mode)));
  content.append(titleLine);
  content.append(element("div", "job-url", job.source_url));
  if (job.result_path) content.append(element("div", "job-url", job.result_path));
  if (job.error) content.append(element("div", "job-error", job.error));
  row.append(content);
  const stateCell = element("td");
  stateCell.append(element("span", `status-badge ${job.status}`, statusText(job.status)));
  row.append(stateCell);
  const progressCell = element("td", "progress-cell");
  const track = element("div", "progress-track");
  const bar = element("div", "progress-bar");
  bar.style.width = `${Math.max(0, Math.min(100, Number(job.progress) || 0))}%`;
  track.append(bar);
  progressCell.append(track);
  progressCell.append(element("div", "progress-text", progressSummary(job)));
  row.append(progressCell);
  const actions = element("td");
  const actionRow = element("div", "button-stack");
  if (["queued", "running"].includes(job.status)) {
    const cancel = element("button", "button small danger", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", () => cancelJob(job.id));
    actionRow.append(cancel);
  }
  if (["failed", "cancelled", "auth_required"].includes(job.status)) {
    const retry = element("button", "button small secondary", "重试");
    retry.type = "button";
    retry.addEventListener("click", () => retryJob(job.id));
    actionRow.append(retry);
  }
  const log = element("button", "button small ghost", "日志");
  log.type = "button";
  log.addEventListener("click", () => showLog(job.id));
  actionRow.append(log);
  if (["completed", "failed", "cancelled", "auth_required"].includes(job.status)) {
    const remove = element("button", "button small danger", "删除");
    remove.type = "button";
    remove.addEventListener("click", () => deleteJob(job.id));
    actionRow.append(remove);
  }
  actions.append(actionRow);
  row.append(actions);
  return row;
}

async function cancelJob(id) {
  try {
    await api(`/api/jobs/${id}/cancel`, {method: "POST"});
    await loadJobs();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function retryJob(id) {
  try {
    await api(`/api/jobs/${id}/retry`, {method: "POST"});
    await loadJobs();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function deleteJob(id) {
  if (!confirm(`删除任务 #${id} 的记录与日志？已下载的文件不会删除。`)) return;
  try {
    await api(`/api/jobs/${id}`, {method: "DELETE"});
    await loadJobs();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function showLog(id) {
  const holder = element("div");
  holder.append(element("h2", null, `任务 #${id} 日志`));
  const pre = element("pre", "log-output", "正在读取…");
  holder.append(pre);
  showModal(holder);
  try {
    const data = await api(`/api/jobs/${id}/log?lines=160`);
    pre.textContent = data.content || "暂无日志";
  } catch (error) {
    pre.textContent = error.message;
  }
}

async function loadHistory() {
  try {
    const records = await api("/api/history?limit=200");
    historyBody.replaceChildren();
    if (!records.length) {
      const row = element("tr");
      const cell = element("td", "empty", "还没有解析历史");
      cell.colSpan = 4;
      row.append(cell);
      historyBody.append(row);
      return;
    }
    for (const record of records) historyBody.append(renderHistory(record));
  } catch (error) {
    historyBody.replaceChildren();
    const row = element("tr");
    const cell = element("td", "empty", error.message);
    cell.colSpan = 4;
    row.append(cell);
    historyBody.append(row);
  }
}

function renderHistory(record) {
  const row = element("tr");
  const content = element("td");
  content.append(element("div", "job-title", record.title || "未命名内容"));
  content.append(element("div", "job-url", record.source_url));
  const meta = [
    record.parse_count > 1 ? `解析 ${record.parse_count} 次` : "解析 1 次",
    record.is_playlist && record.playlist_count ? `正片 ${record.playlist_count} 集` : null
  ].filter(Boolean);
  content.append(element("div", "history-meta", meta.join(" · ")));
  row.append(content);
  const modeCell = element("td");
  modeCell.append(element("span", `mode-badge ${record.api_mode}`, modeText(record.api_mode)));
  row.append(modeCell);
  row.append(element("td", "history-time", new Date(record.updated_at).toLocaleString("zh-CN")));
  const actions = element("td");
  const actionRow = element("div", "button-stack");
  const use = element("button", "button small secondary", "使用记录");
  use.type = "button";
  use.addEventListener("click", () => useHistory(record.id));
  actionRow.append(use);
  const refresh = element("button", "button small ghost", "重新解析");
  refresh.type = "button";
  refresh.addEventListener("click", () => reparseHistory(record));
  actionRow.append(refresh);
  const remove = element("button", "button small danger", "删除");
  remove.type = "button";
  remove.addEventListener("click", () => deleteHistory(record.id));
  actionRow.append(remove);
  actions.append(actionRow);
  row.append(actions);
  return row;
}

async function useHistory(id) {
  try {
    const record = await api(`/api/history/${id}`);
    document.querySelector("#video-url").value = record.source_url;
    apiModeSelect.value = record.api_mode || "web";
    resolvedData = record;
    resolvedUrl = record.source_url;
    renderResolution(record);
    const base = record.episodes?.length || record.is_playlist
      ? document.querySelector("#playlist-template").value
      : document.querySelector("#single-template").value;
    document.querySelector("#job-template").value = base;
    document.querySelector("#all-parts").checked = Boolean(
      record.is_playlist && !record.episodes?.length
    );
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function reparseHistory(record) {
  document.querySelector("#video-url").value = record.source_url;
  apiModeSelect.value = record.api_mode || "web";
  await resolveVideo();
}

async function deleteHistory(id) {
  if (!confirm("删除这条解析历史？不会影响已经创建的任务。")) return;
  try {
    await api(`/api/history/${id}`, {method: "DELETE"});
    await loadHistory();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function clearHistory() {
  if (!confirm("清空全部解析历史？不会影响下载任务。")) return;
  try {
    await api("/api/history", {method: "DELETE"});
    await loadHistory();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function logoutApp() {
  if (currentView === "settings" && window.SettingsPage && !window.SettingsPage.confirmLeave()) return;
  try {
    await api("/api/auth/logout", {method: "POST"});
  } finally {
    window.location.assign("/");
  }
}

async function logoutBili() {
  if (!confirm("断开 B 站 Web 账号？")) return;
  try {
    await api("/api/bili/logout", {method: "POST"});
    await loadAccount();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

async function logoutTv() {
  if (!confirm("断开 B 站 TV 账号？")) return;
  try {
    await api("/api/bili/tv/logout", {method: "POST"});
    await loadAccount();
  } catch (error) {
    if (window.AppNotice) AppNotice.show(error.message, true);
    else alert(error.message);
  }
}

document.querySelector("#bili-login").addEventListener("click", () => openQrLogin("web"));
document.querySelector("#bili-logout").addEventListener("click", logoutBili);
document.querySelector("#tv-login").addEventListener("click", () => openQrLogin("tv"));
document.querySelector("#tv-logout").addEventListener("click", logoutTv);
document.querySelector("#app-logout").addEventListener("click", logoutApp);
document.querySelector("#resolve-button").addEventListener("click", resolveVideo);
document.querySelector("#download-form").addEventListener("submit", createJob);
document.querySelectorAll(".app-tab").forEach(tab => {
  tab.addEventListener("click", () => openBusinessView(tab.dataset.view));
});
document.querySelectorAll(".home-open").forEach(button => {
  button.addEventListener("click", () => openBusinessView(button.dataset.target));
});
document.querySelector("#settings-entry").addEventListener("click", openSettingsPage);
document.querySelector("#settings-back").addEventListener("click", leaveSettingsPage);
document.querySelector("#job-output-browse").addEventListener("click", async () => {
  const input = document.querySelector("#job-output-dir");
  try {
    const selected = await FileBrowser.openPicker({
      mode: "select-directory",
      scope: "browse",
      rootKeys: ["downloads"],
      rootKey: "downloads",
      path: input.value.trim(),
      selectionType: "directory",
      title: "选择本次下载的保存目录",
      confirmLabel: "使用此目录"
    });
    if (selected) input.value = selected.relative_path;
  } catch (error) {
    document.querySelector("#download-error").textContent = error.message;
  }
});
document.querySelector("#refresh-jobs").addEventListener("click", loadJobs);
document.querySelector("#select-main-episodes").addEventListener("click", selectMainEpisodes);
document.querySelector("#clear-episodes").addEventListener("click", clearEpisodeSelection);
episodeSortSelect.addEventListener("change", changeEpisodeSort);
document.querySelector("#check-batch-formats").addEventListener("click", checkBatchFormats);
document.querySelector("#clear-history").addEventListener("click", clearHistory);
apiModeSelect.addEventListener("change", clearResolution);
document.querySelector("#video-url").addEventListener("input", clearResolution);
episodeGroups.addEventListener("wheel", event => {
  if (episodeGroups.scrollHeight <= episodeGroups.clientHeight) return;
  const before = episodeGroups.scrollTop;
  episodeGroups.scrollTop += event.deltaY;
  if (episodeGroups.scrollTop !== before) event.preventDefault();
}, {passive: false});
document.querySelector("#modal-close").addEventListener("click", closeModal);
modal.addEventListener("click", event => {
  if (event.target === modal) closeModal();
});
document.addEventListener("keydown", event => {
  if (event.key === "Escape") closeModal();
});
document.addEventListener("settings-updated", event => {
  if (event.detail) applyAppSettings(event.detail);
});
window.addEventListener("popstate", event => {
  const isSettings = window.location.pathname.replace(/\/$/, "") === "/settings";
  if (currentView === "settings" && !isSettings && window.SettingsPage && !window.SettingsPage.confirmLeave()) {
    window.history.pushState(
      {view: "settings", returnView: settingsReturnView, fromApp: true},
      "",
      "/settings"
    );
    switchAppView("settings");
    return;
  }
  if (isSettings) {
    settingsReturnView = event.state?.returnView
      || sessionStorage.getItem("settingsReturnView")
      || "files";
    switchAppView("settings");
    return;
  }
  switchAppView(businessView(
    event.state?.view || appSettings?.app_default_view,
    "files"
  ));
});

window.addEventListener("DOMContentLoaded", async () => {
  await loadAppSettings();
  if (window.location.pathname.replace(/\/$/, "") === "/settings") {
    settingsReturnView = sessionStorage.getItem("settingsReturnView")
      || appSettings?.app_default_view
      || "files";
    window.history.replaceState(
      {view: "settings", returnView: settingsReturnView},
      "",
      "/settings"
    );
    switchAppView("settings");
  } else {
    const initial = businessView(
      window.history.state?.view || appSettings?.app_default_view,
      "files"
    );
    window.history.replaceState({view: initial}, "", "/");
    switchAppView(initial);
  }
});

loadAccount();
loadJobs();
loadHistory();
setInterval(loadJobs, 3000);
