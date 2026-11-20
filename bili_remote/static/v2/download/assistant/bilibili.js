const BILI_API = "https://api.bilibili.com";
const BILI_PAGE = "https://www.bilibili.com";
const positive = value => Number.isSafeInteger(+value) && +value > 0 ? +value : null;

async function bili(path, params) {
  const result = await fetch(BILI_API + path + "?" + new URLSearchParams(params), {
    credentials: "include", cache: "no-store", redirect: "error",
    signal: AbortSignal.timeout(12000),
  });
  if (!result.ok) throw new Error("B 站接口未返回可用结果");
  const data = await result.json();
  if (data.code !== 0) throw new Error(data.message || "B 站解析失败");
  return data.result || data.data;
}

export async function readTab(href) {
  const url = new URL(href);
  if (url.origin !== BILI_PAGE) throw new Error("请在 B 站视频或番剧页面打开解析助手。");
  const season = /^\/bangumi\/play\/(ep|ss)(\d{1,20})\/?$/.exec(url.pathname);
  if (season) {
    const key = season[1] === "ep" ? "ep_id" : "season_id";
    const show = await bili("/pgc/view/web/season", {[key]: season[2]});
    const sections = await bili("/pgc/web/season/section", {season_id: show.season_id});
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
  const show = await bili("/x/web-interface/view", {[key]: key === "aid" ? id.slice(2) : id});
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
  if (raw.has_drm || raw.drm) return null;
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

export async function loadTracks(ep) {
  if (ep.video && ep.audio) return;
  const data = await bili(ep.pgc ? "/pgc/player/web/playurl" : "/x/player/playurl", {
    ...ep.params, qn: 127, fnval: 4048, fnver: 0, fourk: 1, platform: "pc",
  });
  const dash = data.dash;
  if (!dash || data.is_preview || data.isPreview || data.preview || data.drm
      || (ep.duration && dash.duration && dash.duration + 8 < ep.duration)) {
    throw new Error(ep.title + " 暂无完整可用片源");
  }
  ep.video = (dash.video || []).map(row => track(row, true)).filter(row => row?.height).slice(0, 40);
  ep.audio = (dash.audio || []).map(row => track(row, false)).filter(Boolean).slice(0, 10);
}
