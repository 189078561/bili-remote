let mediaBrowser = null;
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const modal = document.querySelector("#modal");
const modalContent = document.querySelector("#modal-content");
let currentQrSession = null;
let qrTimer = null;
let qrMode = "web";
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
  single: "",
  playlist: "",
  output: ""
};
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
  appSettings = data;
  const values = data?.values || {};
  downloadDefaults = {
    single: data?.single_output_template || "",
    playlist: data?.playlist_output_template || "",
    output: data?.download_default_output_dir || ""
  };
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
      window.AppNotice?.show(`读取默认设置失败：${error.message}`, true);
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

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined || Number.isNaN(Number(bytes))) return "—";
  const value = Number(bytes);
  if (value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
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
      document.dispatchEvent(new Event("bili:account-changed"));
    } else if (data.status === "expired") {
      stopQrPolling();
    }
  } catch (error) {
    const label = document.querySelector("#qr-status");
    if (label) label.textContent = error.message;
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

document.querySelectorAll(".app-tab").forEach(tab => {
  tab.addEventListener("click", () => openBusinessView(tab.dataset.view));
});
document.querySelectorAll(".home-open").forEach(button => {
  button.addEventListener("click", () => openBusinessView(button.dataset.target));
});
document.querySelector("#settings-entry").addEventListener("click", openSettingsPage);
document.querySelector("#settings-back").addEventListener("click", leaveSettingsPage);
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
