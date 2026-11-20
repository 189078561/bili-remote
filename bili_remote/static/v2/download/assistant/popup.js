import {readTab, loadTracks, tabRequest} from "./bilibili.js";
import {hasTrack, mountPicker} from "./picker.js";
import {mountLocalTv, tvPlayInfo} from "./tv.js";

const $ = selector => document.querySelector(selector);
const app = {
  ...await chrome.storage.local.get({projects: [], target: "", order: "asc", rows: 3}),
  catalogue: null, picker: null, request: null, busy: false, mode: "web",
};
const submitted = new Map();
const destinationKey = () => app.target + "|" + app.mode;
const requests = new Map();
const requestsQueue = [];
let inFlight = 0;

function requestTrack(index) {
  if (requests.has(index)) return requests.get(index);
  const episode = app.catalogue.episodes[index];
  const tv = app.mode === "tv";
  const request = new Promise(resolve => {
    requestsQueue.push(async () => {
      try {
        episode.error = "";
        await loadTracks(episode, app.request, tv ? tvPlayInfo : null);
      } catch (error) {
        episode.error = error.message;
        if (tv && /登录/.test(error.message)) await refreshTvLogin();
        episode.video = [];
        episode.audio = [];
      } finally {
        inFlight--;
        resolve();
        runTracks();
      }
    });
    runTracks();
  });
  requests.set(index, request);
  return request;
}
function runTracks() {
  while (inFlight < 3 && requestsQueue.length) {
    inFlight++;
    void requestsQueue.shift()();
  }
}

const status = message => { $("[data-status]").textContent = message; };
const current = () => app.projects.find(p => p.url === app.target);
const persist = () => chrome.storage.local.set({
  projects: app.projects, target: app.target, order: app.order, rows: app.rows,
});

function address(raw) {
  const url = new URL(raw.trim());
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
    throw new Error("请输入项目的网址，可包含端口和部署路径。");
  }
  return url.origin + url.pathname.replace(/\/+$/, "");
}

async function jsonRequest(base, path, options = {}) {
  const response = await fetch(base + path, {
    credentials: "include", redirect: "error", cache: "no-store",
    signal: AbortSignal.timeout(20000), ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) throw new Error("请先在目标项目网页登录，再打开解析助手。");
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "项目请求失败 (HTTP " + response.status + ")");
  return data;
}
async function verify(base) {
  const result = await jsonRequest(base, "/api/parse-assistant");
  if (result.version !== 1) throw new Error("该项目尚未启用解析助手接口。");
}
async function post(project, path, body) {
  const auth = await jsonRequest(project.url, "/api/auth/session");
  if (!auth.authenticated || !auth.csrf) throw new Error("请先登录目标项目。");
  return jsonRequest(project.url, path, {
    method: "POST", headers: {"Content-Type": "application/json", "X-CSRF-Token": auth.csrf},
    body: JSON.stringify(body),
  });
}
function updateButtons(choices = app.picker?.choices() || []) {
  const ready = !!current() && !!app.catalogue && !app.busy;
  $("[data-send]").disabled = !ready;
  $("[data-target]").disabled = app.busy || !app.projects.length;
  $("[data-settings]").disabled = app.busy;
  const retry = $("[data-retry]");
  retry.hidden = !app.picker?.selected().some(pick => app.catalogue?.episodes[pick.episode].error);
  for (const button of document.querySelectorAll("[data-source]")) {
    button.disabled = app.busy || !app.catalogue;
    button.setAttribute("aria-pressed", String(button.dataset.source === app.mode));
  }
  retry.disabled = app.busy;
  const download = $("[data-download]");
  download.disabled = !ready || !choices.length || !choices.every(pick => {
    const episode = app.catalogue.episodes[pick.episode];
    return hasTrack(episode, pick);
  });
  download.textContent = "推送下载" + (choices.length ? " · " + choices.length + " 项" : "");
}
function showProjects() {
  const picker = $("[data-target]");
  picker.replaceChildren(...app.projects.map(p => new Option(p.name, p.url)));
  if (!app.projects.length) picker.add(new Option("请先在设置中添加项目", ""));
  picker.value = current()?.url || "";
  $("[data-address]").textContent = current()?.url || "";
  updateButtons();
}
function edit(base) {
  const project = app.projects.find(p => p.url === base);
  const manage = $("[data-manage]");
  manage.replaceChildren(...app.projects.map(p => new Option(p.name, p.url)));
  if (!project) manage.add(new Option("新项目", ""));
  manage.value = project?.url || "";
  $("[data-url]").value = project?.url || "";
  $("[data-url]").readOnly = !!project;
  $("[data-name]").value = project?.name || "";
  $("[data-save]").textContent = project ? "保存名称" : "连接并设为目标";
  $("[data-login]").hidden = true;
}
function rebuildPicker(selections = app.picker?.snapshot()) {
  if (!app.catalogue) return;
  if (!submitted.has(destinationKey())) submitted.set(destinationKey(), []);
  app.picker = mountPicker($("[data-picker]"), app.catalogue.episodes, {
    order: app.order, rows: app.rows, active: app.catalogue.active, initial: selections,
    queued: submitted.get(destinationKey()),
    onSelect: index => requestTrack(index).then(() => app.picker?.refresh()),
    onChange: updateButtons,
  });
}
function reloadSource() {
  if (!app.catalogue) return;
  requests.clear();
  const selections = app.picker ? {episodes: app.picker.selected()} : undefined;
  app.catalogue.episodes = app.catalogue.episodes.map(episode => ({
    ...episode, video: [], audio: [], error: "",
  }));
  rebuildPicker(selections);
}
const refreshTvLogin = await mountLocalTv($("[data-local-tv]"), () => {
  if (app.mode === "tv") reloadSource();
});
for (const button of document.querySelectorAll("[data-source]")) {
  button.onclick = () => {
    if (button.dataset.source === app.mode) return;
    app.mode = button.dataset.source;
    $("[data-local-tv]").hidden = app.mode !== "tv";
    status("");
    reloadSource();
  };
}
$("[data-target]").onchange = async event => {
  app.target = event.target.value;
  await persist();
  status("");
  showProjects();rebuildPicker();
};
$("[data-settings]").onclick = () => {
  const opening = $("[data-preferences]").hidden;
  $("[data-main]").hidden = opening;
  $("[data-preferences]").hidden = !opening;
  const gear = $("[data-settings]");
  const label = opening ? "返回选集" : "设置";
  gear.setAttribute("aria-label", label);
  gear.title = label;
  gear.setAttribute("aria-expanded", String(opening));
  gear.firstElementChild.textContent = opening ? "←" : "⚙";
  status("");
  if (opening) edit(app.target);
};
$("[data-manage]").onchange = event => edit(event.target.value);
$("[data-add]").onclick = () => {
  edit("");
  $("[data-url]").focus();
};
$("[data-save]").onclick = async () => {
  const name = $("[data-name]").value.trim();
  const saved = app.projects.find(p => p.url === $("[data-manage]").value);
  if (saved) {
    if (!name) { status("请输入项目名称。");return; }
    saved.name = name;
    await persist();
    edit(saved.url);showProjects();status("项目名称已保存。");
    return;
  }
  const save = $("[data-save]");
  try {
    const base = address($("[data-url]").value);
    const origin = new URL(base);
    // The permission prompt is scoped to the hostname the user just entered.
    const granted = await chrome.permissions.request({origins: [origin.protocol + "//" + origin.hostname + "/*"]});
    if (!granted) throw new Error("需要项目站点的访问权限才能连接。");
    save.disabled = true;
    try { await verify(base); }
    catch (error) {
      const login = $("[data-login]");
      login.href = base;login.hidden = false;
      throw error;
    }
    const existing = app.projects.find(p => p.url === base);
    if (!existing) app.projects.push({url: base, name: name || origin.host});
    app.target = base;
    await persist();
    edit(base);showProjects();rebuildPicker();status("项目已连接。");
  } catch (error) { status(error.message); }
  finally { save.disabled = false; }
};
for (const [key, convert] of [["order", String], ["rows", Number]]) {
  const select = $("[data-" + key + "]");
  select.value = String(app[key]);
  select.onchange = async () => {
    app[key] = convert(select.value);
    await persist();rebuildPicker();status("选集偏好已保存。");
  };
}

function publicEpisode(ep) {
  const {url, title, number, duration, video, audio} = ep;
  return {url, title, number, duration, video: video || [], audio: audio || []};
}
async function submit(sendAll) {
  if (app.busy || !current() || !app.catalogue) return;
  const target = current();
  app.busy = true;
  updateButtons();
  try {
    const {title, episodes, movie = false} = app.catalogue;
    const source = app.mode;
    const picks = app.picker.choices();
    let features = [];
    if (sendAll || source === "tv" || movie || (!sendAll && picks.some(pick => pick.video === null))) {
      features = (await jsonRequest(target.url, "/api/parse-assistant")).features || [];
      const required = [
        source === "tv" && "local-tv",
        movie && "movie-title",
        (!sendAll && picks.some(pick => pick.video === null)) && "audio-only",
      ].filter(Boolean);
      if (required.some(feature => !features.includes(feature))) throw new Error("目标项目版本过旧，请更新后再推送该片源。");
    }
    if (sendAll) {
      let finished = 0;
      await Promise.all(episodes.map(async (_, i) => {
        await requestTrack(i);
        status("正在读取正片档位 " + ++finished + " / " + episodes.length);
      }));
      const missing = episodes.filter(ep => !ep.audio?.length).length;
      if (missing === episodes.length) throw new Error(episodes[0].error || "正片暂无可提交的档位。");
      const result = await post(target, "/api/parse-assistant/imports", {title, source, movie, active_episode: app.catalogue.active >= 0 ? app.catalogue.active : null, episodes: episodes.map(publicEpisode)});
      status("已向「" + target.name + "」推送 " + result.count + " 项。" + (missing ? missing + " 项暂无完整档位。" : ""));
      if (features.includes("native-import")) {
        try {
          await chrome.tabs.create({url: target.url + "/?assistant_import=" + encodeURIComponent(result.id)});
        } catch {
          status("列表已推送，请在项目的解析助手面板中打开。");
        }
      }
    } else {
      const result = await post(target, "/api/parse-assistant/jobs", {
        catalogue: {title, source, movie, episodes: picks.map(pick => publicEpisode(episodes[pick.episode]))},
        choices: picks.map((pick, episode) => ({...pick, episode})),
      });
      submitted.get(destinationKey()).push(...picks.map(({episode, video, audio}) => [episode, video, audio]));
      app.picker.refresh();
      status("「" + target.name + "」已接收 " + result.ids.length + " 个下载任务。");
    }
  } catch (error) { status(error.message); }
  finally { app.busy = false;updateButtons(); }
}
$("[data-send]").onclick = () => submit(true);
$("[data-download]").onclick = () => submit(false);
$("[data-retry]").onclick = async () => {
  const retry = app.picker.selected().filter(pick => app.catalogue.episodes[pick.episode].error);
  app.busy = true;
  status("正在重新获取档位…");
  updateButtons();
  try {
    await Promise.all(retry.map(({episode}) => {
      requests.delete(episode);
      return requestTrack(episode);
    }));
    status(app.catalogue.episodes[retry[0].episode].error || "档位已重新读取。");
  } finally {
    app.busy = false;
    app.picker.refresh();
  }
};

showProjects();
try {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  app.request = tabRequest(tab);
  app.catalogue = await readTab(tab.url, app.request);
  if (app.catalogue.activePart) app.catalogue.active = app.catalogue.episodes.findIndex(ep => ep.number === app.catalogue.activePart);
  if (!app.catalogue.episodes.length) throw new Error("页面没有可解析的正片。");
  if (app.catalogue.episodes.length > 600) throw new Error("当前列表超过 600 项，请打开具体分季。");
  $("[data-title]").textContent = app.catalogue.title + " · 正片 " + app.catalogue.episodes.length + " 项";
  rebuildPicker();
} catch (error) {
  app.catalogue = null;
  $("[data-title]").textContent = "暂无解析结果";
  status(error.message);
}
updateButtons();
