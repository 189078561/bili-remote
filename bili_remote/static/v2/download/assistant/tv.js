import "./md5.min.js";
import qrcode from "./qrcode.mjs";

const passport = "https://passport.bilibili.com/x/passport-tv-login/qrcode/";
const playback = "https://api.snm0516.aisee.tv";
const origins = ["https://passport.bilibili.com/*", playback + "/*"];
const authErrors = new Set([-101, -40003, 401]);

let requestRules;
function prepareTvRequests() {
  // The TV client endpoints expect a native-client request. Chrome adds its
  // extension Origin to POSTs. Scope the adjustment to this extension and these endpoints.
  requestRules ??= chrome.declarativeNetRequest.updateSessionRules({
    removeRuleIds: [1, 2, 3, 4],
    addRules: [passport + "auth_code", passport + "poll", playback + "/pgc/player/api/playurltv?", playback + "/x/tv/playurl?"]
      .map((endpoint, index) => ({
        id: index + 1,
        condition: {
          urlFilter: "|" + endpoint + (index < 2 ? "|" : ""),
          initiatorDomains: [chrome.runtime.id],
          resourceTypes: ["xmlhttprequest"],
        },
        action: {
          type: "modifyHeaders",
          requestHeaders: [{header: "origin", operation: "remove"}],
        },
      })),
  }).catch(() => {
    requestRules = null;
    throw new Error("无法设置本机 TV 请求。请检查扩展权限。");
  });
  return requestRules;
}

async function signedRequest(endpoint, params, method = "GET") {
  await prepareTvRequests();
  const fields = {...params, appkey: "4409e2ce8ffd12b8", ts: String(Math.floor(Date.now() / 1000))};
  const query = Object.keys(fields).sort().map(key => key + "=" + fields[key]).join("&");
  fields.sign = globalThis.md5(query + "59b43e04ad6965f34319062b478f83dd");
  const body = new URLSearchParams(fields);
  const response = await fetch(method === "GET" ? endpoint + "?" + body : endpoint, {
    method, body: method === "POST" ? body : undefined, credentials: "omit",
    cache: "no-store", redirect: "error", signal: AbortSignal.timeout(12000),
  }).catch(() => {throw new Error("本机无法连接 B 站 TV 接口，请检查网络后重试。");});
  if (response.status === 401) return {code: 401};
  if (!response.ok) throw new Error("B 站 TV 接口请求失败（HTTP " + response.status + "）。");
  return response.json().catch(() => {throw new Error("B 站 TV 接口返回异常。");});
}

export async function tvPlayInfo(episode) {
  const {localTvLogin: login} = await chrome.storage.local.get("localTvLogin");
  if (!login || (login.expires && login.expires <= Date.now())) throw new Error("请在扩展内扫码登录本机 TV。");
  const {avid, aid, cid, ep_id} = episode.params;
  if (!avid && !aid) throw new Error("当前条目缺少 TV 播放参数。");
  const result = await signedRequest(playback + (episode.pgc ? "/pgc/player/api/playurltv" : "/x/tv/playurl"), {
    access_key: login.token, build: "106500", cid, device: "android", expire: "0",
    fnval: "4048", fnver: "0", fourk: "1", mid: "0", mobi_app: "android_tv_yst",
    object_id: avid || aid, platform: "android", playurl_type: "1", qn: "127",
    ...(ep_id ? {ep_id} : {}),
  });
  if (authErrors.has(result.code)) {
    // A stale request must not clear a newly scanned login.
    const current = (await chrome.storage.local.get("localTvLogin")).localTvLogin;
    if (current?.token === login.token) await chrome.storage.local.remove("localTvLogin");
    throw new Error("本机 TV 登录已失效，请重新扫码。");
  }
  if (result.code === -10403 || /地区/.test(result.message || "")) throw new Error("本机网络所在地区无法访问该 TV 片源。");
  if (result.code !== 0) throw new Error("TV 档位读取失败（" + result.code + "）。");
  const data = result.data || result.result || result;
  const dash = result.dash || data.dash;
  if (Array.isArray(dash?.video) && !dash.video.length) {
    throw new Error("本机 TV 未提供正片档位。可切换到本机 Web 重新解析。");
  }
  return {...data, dash, is_preview: result.is_preview || data.is_preview, is_drm: result.is_drm || data.is_drm};
}

export async function mountLocalTv(root, onChange) {
  const button = root.querySelector("button");
  const image = root.querySelector("img");
  const status = root.querySelector("small");
  let {localTvLogin: login, localTvQr: qr} = await chrome.storage.local.get(["localTvLogin", "localTvQr"]);
  let revision = 0;
  let timer;
  function draw() {
    const authorized = login && (!login.expires || login.expires > Date.now());
    button.textContent = authorized ? "退出本机 TV 登录" : "生成 TV 登录二维码";
    status.textContent = authorized ? "本机 TV 已登录" : "请使用哔哩哔哩客户端扫码";
    image.hidden = !qr;
    if (qr) {
      const code = qrcode(0, "M");
      code.addData(qr.url);
      code.make();
      image.src = code.createDataURL(4, 12);
    } else image.removeAttribute("src");
  }
  async function poll(token) {
    if (token !== revision || !qr) return;
    if (Date.now() > qr.expires) {
      qr = null;
      await chrome.storage.local.remove("localTvQr");
      draw();
      status.textContent = "二维码已过期，请重新生成。";
      return;
    }
    try {
      const result = await signedRequest(passport + "poll", {local_id: "0", auth_code: qr.code}, "POST");
      if (token !== revision) return;
      if (result.code === 0 && typeof result.data?.access_token === "string" && result.data.access_token) {
        const seconds = Number(result.data.expires_in);
        login = {token: result.data.access_token, expires: seconds > 0 ? Date.now() + seconds * 1000 : null};
        qr = null;
        await chrome.storage.local.set({localTvLogin: login});
        await chrome.storage.local.remove("localTvQr");
        draw();
        onChange();
        return;
      }
      if (result.code === 86038) {
        qr.expires = 0;
        await poll(token);
        return;
      }
      status.textContent = result.code === 86090 ? "已扫码，请在手机上确认。" : "等待扫码…";
    } catch (error) {
      if (token === revision) status.textContent = error.message;
    }
    if (token === revision) timer = setTimeout(() => poll(token), 2500);
  }
  button.onclick = async () => {
    const token = ++revision;
    clearTimeout(timer);
    button.disabled = true;
    try {
      if (login && (!login.expires || login.expires > Date.now())) {
        login = null;
        await chrome.storage.local.remove(["localTvLogin", "localTvQr"]);
        qr = null;
        draw();
        onChange();
        return;
      }
      if (!await chrome.permissions.request({origins})) throw new Error("需要允许访问 B 站 TV 接口才能登录。");
      const result = await signedRequest(passport + "auth_code", {local_id: "0"}, "POST");
      if (result.code !== 0 || !/^https?:\/\//.test(result.data?.url || "") || !result.data?.auth_code) {
        throw new Error("无法生成 TV 登录二维码（" + result.code + "）。");
      }
      qr = {code: result.data.auth_code, url: result.data.url, expires: Date.now() + 180000};
      await chrome.storage.local.set({localTvQr: qr});
      draw();
      void poll(token);
    } catch (error) { status.textContent = error.message; }
    finally { button.disabled = false; }
  };
  draw();
  if (qr) void poll(revision);
  return async () => {
    login = (await chrome.storage.local.get("localTvLogin")).localTvLogin;
    if (!qr) draw();
  };
}
