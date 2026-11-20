const BILI_API = "https://api.bilibili.com";
const BILI_PAGE = "https://www.bilibili.com";
const positive = value => Number.isSafeInteger(+value) && +value > 0 ? +value : null;

// executeScript serializes this function into the active Bilibili document.
async function fetchInBiliTab(expected, endpoint) {
  const current = new URL(location.href);
  const source = new URL(expected);
  if (current.origin !== "https://www.bilibili.com" || current.origin !== source.origin ||
      current.pathname !== source.pathname || current.searchParams.get("p") !== source.searchParams.get("p")) {
    return {error: "B 站标签页已切换，请重新打开解析助手。"};
  }
  try {
    const response = await fetch(endpoint, {
      credentials: "include", cache: "no-store", redirect: "error",
      signal: AbortSignal.timeout(12000),
    });
    return response.ok ? {body: await response.json()} : {error: "B 站请求失败（HTTP " + response.status + "）"};
  } catch (error) {
    return {error: error.name === "TimeoutError" ? "B 站播放接口请求超时。" : "当前标签页无法读取 B 站接口，请刷新页面后重试。"};
  }
}

export function tabRequest(tab) {
  if (!tab?.id || !tab.url) throw new Error("请在 B 站视频或番剧页面打开解析助手。");
  return async (path, params) => {
    const url = BILI_API + path + "?" + new URLSearchParams(params);
    const [injection] = await chrome.scripting.executeScript({
      target: {tabId: tab.id}, world: "MAIN", func: fetchInBiliTab, args: [tab.url, url],
    }).catch(() => {
      throw new Error("无法读取当前 B 站标签页。请刷新视频页面，再从该标签页打开解析助手。");
    });
    const result = injection?.result;
    if (!result || result.error) throw new Error(result?.error || "B 站标签页未返回解析数据。");
    const payload = result.body;
    if (!payload || typeof payload !== "object") throw new Error("B 站接口未返回有效数据。");
    if (payload.code !== 0) throw new Error((payload.message || "B 站解析失败") + "（" + payload.code + "）");
    const data = payload.result || payload.data;
    if (!data || typeof data !== "object") throw new Error("B 站接口未返回有效数据。");
    return data;
  };
}

export async function readTab(href, request) {
  const url = new URL(href);
  if (url.origin !== BILI_PAGE) throw new Error("请在 B 站视频或番剧页面打开解析助手。");
  const season = /^\/bangumi\/play\/(ep|ss)(\d{1,20})\/?$/.exec(url.pathname);
  if (season) {
    const key = season[1] === "ep" ? "ep_id" : "season_id";
    const show = await request("/pgc/view/web/season", {[key]: season[2]});
    const sections = await request("/pgc/web/season/section", {season_id: show.season_id});
    const details = new Map((show.episodes || []).map(ep => [String(ep.ep_id || ep.id), ep]));
    const episodes = (sections.main_section?.episodes || []).flatMap(item => {
      const id = positive(item.id || item.ep_id);
      const row = {...details.get(String(id)), ...item};
      if (!id || !positive(row.cid) || !positive(row.aid)) return [];
      return [{
        url: BILI_PAGE + "/bangumi/play/ep" + id,
        title: [row.title, row.long_title].filter(Boolean).join(" ").slice(0, 300) || "EP " + id,
        number: /^(?:第\s*)?\d{1,5}(?:\s*[集话話])?$/.test(String(row.title).trim())
          ? positive(String(row.title).match(/\d+/)?.[0]) : null,
        duration: positive(Math.round(Number(row.duration) / 1000)),
        params: {ep_id: id, cid: row.cid, avid: row.aid}, pgc: true,
      }];
    });
    if (!episodes.length) throw new Error("当前页面没有可解析的正片。");
    return {title: show.title.slice(0, 300), episodes, active: episodes.findIndex(ep => ep.params.ep_id === Number(season[2]))};
  }
  const id = /^\/video\/(BV[A-Za-z0-9]{6,20}|av\d{1,20})\/?$/.exec(url.pathname)?.[1];
  if (!id) throw new Error("请打开 B 站视频或番剧正片页面。");
  const key = id.startsWith("BV") ? "bvid" : "aid";
  const show = await request("/x/web-interface/view", {[key]: key === "aid" ? id.slice(2) : id});
  return {
    title: show.title.slice(0, 300), activePart: positive(url.searchParams.get("p")) || 1,
    episodes: (show.pages || []).flatMap(part => {
      const index = positive(part.page);
      if (!index || !positive(part.cid) || /^(?:【|\[)?(?:花絮|预告|先导预告|片花|预告片)(?:】|\]|[：:·\s]|$)/.test(String(part.part).trim())) return [];
      return [{
        url: BILI_PAGE + "/video/" + id + "?p=" + index,
        title: String(part.part || show.title).slice(0, 300),
        number: index, duration: positive(part.duration), params: {bvid: show.bvid, cid: part.cid},
      }];
    }),
  };
}

function track(raw, isVideo) {
  if (marked(raw.has_drm) || marked(raw.is_drm) || marked(raw.drm) || (raw.drm && typeof raw.drm === "object")) return null;
  const url = [raw.baseUrl, raw.base_url, ...(raw.backupUrl || raw.backup_url || [])].find(value =>
    typeof value === "string" && /^https:\/\/[a-z0-9][a-z0-9.-]*\.bilivideo\.(?:com|cn|net)\/[A-Za-z0-9_/-]+\.(?:m4s|mp4)(?:\?[^#\s]*)?$/.test(value));
  const codec = raw.codecs || "";
  if (!url || !positive(raw.id) || !(isVideo ? /^(avc[13]|hev1|hvc1|av01)(\.|$)/ : /^(mp4a|ec-3|ac-3|flac|fLaC|opus)(\.|$)/).test(codec)) return null;
  const [frames, seconds = 1] = String(raw.frameRate || raw.frame_rate || 0).split("/");
  const fps = Number(frames) / Number(seconds);
  return {url, codec, quality: +raw.id, bandwidth: positive(raw.bandwidth) || 0,
    width: isVideo ? positive(raw.width) || 0 : 0, height: isVideo ? positive(raw.height) || 0 : 0,
    fps: isVideo && fps > 0 && fps <= 300 ? fps : null, size: positive(raw.size)};
}

function marked(value) {
  return value === true || value === 1 || value === "1" || value === "true";
}

export async function loadTracks(ep, request) {
  if (ep.video?.length && ep.audio?.length) return;
  const result = await request(ep.pgc ? "/pgc/player/web/playurl" : "/x/player/playurl", {
    ...ep.params, qn: 127, fnval: 4048, fnver: 0, fourk: 1, platform: "pc",
  });
  const data = result.video_info || result;
  const dash = data.dash;
  const streamDuration = Number(dash?.duration) || 0;
  if ([result, data].some(info => marked(info.is_drm) || marked(info.drm) || (info.drm && typeof info.drm === "object"))) {
    throw new Error("B 站返回了受保护的流媒体。");
  }
  const clipSeconds = (Array.isArray(data.durl) ? data.durl : []).reduce((total, clip) => total + (Number(clip.length) || 0) / 1000, 0);
  if ([result, data].some(info => marked(info.is_preview) || marked(info.isPreview) || marked(info.preview)) ||
      (ep.duration && (streamDuration || clipSeconds) && (streamDuration || clipSeconds) + 8 < ep.duration)) {
    const length = streamDuration || clipSeconds;
    throw new Error("当前页面会话只取得试看" + (length ? "（约 " + Math.max(1, Math.round(length / 60)) + " 分钟）" : "") +
      "。请在 B 站确认登录，并拖到试看时长之后确认正片能播放，再重试。");
  }
  if (!dash) {
    if (Array.isArray(data.durl) && data.durl.length) throw new Error("B 站返回了单文件播放格式，解析助手目前需要 DASH 视频和音轨。");
    throw new Error("B 站未给当前页面会话返回 DASH 档位，请确认本页可播放正片。");
  }
  ep.video = (dash.video || []).map(row => track(row, true)).filter(row => row?.height).slice(0, 40);
  ep.audio = (dash.audio || []).map(row => track(row, false)).filter(Boolean).slice(0, 10);
  if (!ep.video.length || !ep.audio.length) {
    throw new Error("DASH 片源缺少可用的" + (!ep.video.length && !ep.audio.length ? "视频和音轨" : !ep.video.length ? "视频轨" : "音轨") + "。");
  }
}
