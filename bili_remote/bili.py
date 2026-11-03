from __future__ import annotations

import base64
import hashlib
import io
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import qrcode

from .database import Database
from .security import SecretBox


class BiliAPIError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class BiliTVAuthError(BiliAPIError):
    pass


class BiliTVRegionError(BiliAPIError):
    pass


@dataclass(slots=True)
class PendingQR:
    id: str
    qrcode_key: str
    client: httpx.AsyncClient
    created_at: float
    status: str = "waiting"
    message: str = "等待扫码"


@dataclass(slots=True)
class PendingTVQR:
    id: str
    auth_code: str
    created_at: float
    status: str = "waiting"
    message: str = "等待扫码"


class BiliClient:
    generate_url = (
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    )
    poll_url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    nav_url = "https://api.bilibili.com/x/web-interface/nav"
    user_agent = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    )

    def __init__(self, max_sessions: int = 5) -> None:
        self.max_sessions = max_sessions
        self._sessions: dict[str, PendingQR] = {}

    async def create_qr(self) -> dict[str, Any]:
        self._prune_sessions()
        params = {
            "source": "main-fe-header",
            "go_url": "https://www.bilibili.com/",
            "web_location": "333.1007",
        }
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={
                "User-Agent": self.user_agent,
                "Referer": "https://www.bilibili.com/",
            },
        )
        try:
            response = await client.get(self.generate_url, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            await client.aclose()
            raise BiliAPIError("无法生成 B 站登录二维码") from exc
        data = payload.get("data") or {}
        qrcode_url = data.get("url")
        qrcode_key = data.get("qrcode_key")
        if payload.get("code") != 0 or not qrcode_url or not qrcode_key:
            await client.aclose()
            raise BiliAPIError(payload.get("message") or "B 站二维码接口返回异常")

        session_id = secrets.token_urlsafe(24)
        self._sessions[session_id] = PendingQR(
            id=session_id,
            qrcode_key=qrcode_key,
            client=client,
            created_at=time.monotonic(),
        )
        return {
            "session_id": session_id,
            "qr_image": self._qr_data_url(qrcode_url),
            "status": "waiting",
            "message": "请使用哔哩哔哩手机客户端扫码",
        }

    async def poll_qr(self, session_id: str) -> dict[str, Any]:
        pending = self._sessions.get(session_id)
        if not pending:
            raise BiliAPIError("二维码会话不存在或已过期")
        if time.monotonic() - pending.created_at > 180:
            await self._close_session(session_id)
            return {"status": "expired", "message": "二维码已过期，请重新生成"}

        try:
            response = await pending.client.get(
                self.poll_url, params={"qrcode_key": pending.qrcode_key}
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise BiliAPIError("暂时无法查询扫码状态") from exc

        data = payload.get("data") or {}
        code = data.get("code")
        if code == 0:
            cookies = self._extract_cookies(pending.client.cookies, data.get("url"))
            await self._close_session(session_id)
            account = await self.get_account(cookies)
            return {
                "status": "authorized",
                "message": "登录成功",
                "cookies": cookies,
                "account": account,
            }
        if code == 86090:
            pending.status = "scanned"
            pending.message = "已扫码，请在手机上确认"
        elif code == 86038:
            pending.status = "expired"
            pending.message = "二维码已过期，请重新生成"
            await self._close_session(session_id)
        else:
            pending.status = "waiting"
            pending.message = data.get("message") or "等待扫码"
        return {"status": pending.status, "message": pending.message}

    async def get_account(self, cookies: dict[str, str]) -> dict[str, Any]:
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": "https://www.bilibili.com/",
                    "Cookie": cookie_header,
                },
            ) as client:
                response = await client.get(self.nav_url)
                response.raise_for_status()
                payload = response.json()
        except Exception:
            return {}
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            return {}
        return {
            "mid": data.get("mid"),
            "uname": data.get("uname"),
            "vip_status": data.get("vipStatus", 0),
            "vip_due_date": data.get("vipDueDate"),
        }

    async def close(self) -> None:
        for session_id in list(self._sessions):
            await self._close_session(session_id)

    async def _close_session(self, session_id: str) -> None:
        pending = self._sessions.pop(session_id, None)
        if pending:
            await pending.client.aclose()

    def _prune_sessions(self) -> None:
        expired = [
            key
            for key, value in self._sessions.items()
            if time.monotonic() - value.created_at > 180
        ]
        for key in expired:
            pending = self._sessions.pop(key, None)
            if pending:
                try:
                    import asyncio

                    asyncio.create_task(pending.client.aclose())
                except RuntimeError:
                    pass
        while len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions.values(), key=lambda item: item.created_at)
            self._sessions.pop(oldest.id, None)
            try:
                import asyncio

                asyncio.create_task(oldest.client.aclose())
            except RuntimeError:
                pass

    @staticmethod
    def _extract_cookies(
        jar: httpx.Cookies, redirect_url: str | None
    ) -> dict[str, str]:
        cookies = {cookie.name: cookie.value for cookie in jar.jar}
        if redirect_url:
            try:
                query = parse_qs(urlsplit(redirect_url).query)
                for key in (
                    "SESSDATA",
                    "bili_jct",
                    "DedeUserID",
                    "DedeUserID__ckMd5",
                    "sid",
                    "buvid3",
                    "b_nut",
                    "access_key",
                    "refresh_token",
                ):
                    if query.get(key):
                        cookies[key] = query[key][0]
            except ValueError:
                pass
        return cookies

    @staticmethod
    def _qr_data_url(value: str) -> str:
        qr = qrcode.QRCode(version=None, box_size=8, border=2)
        qr.add_data(value)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
            "ascii"
        )


class BiliTvClient:
    appkey = "4409e2ce8ffd12b8"
    appsec = "59b43e04ad6965f34319062b478f83dd"
    auth_code_url = (
        "https://passport.bilibili.com/x/passport-tv-login/qrcode/auth_code"
    )
    poll_url = "https://passport.bilibili.com/x/passport-tv-login/qrcode/poll"
    api_host = "https://api.snm0516.aisee.tv"
    user_agent = (
        "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/134.0.0.0 Mobile Safari/537.36"
    )

    def __init__(self, max_sessions: int = 5) -> None:
        self.max_sessions = max_sessions
        self._sessions: dict[str, PendingTVQR] = {}

    @classmethod
    def _sign(cls, params: dict[str, Any]) -> str:
        query = "&".join(
            f"{key}={params[key]}" for key in sorted(params)
        )
        return hashlib.md5((query + cls.appsec).encode("utf-8")).hexdigest()

    @classmethod
    def _signed_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        signed = {**params, "appkey": cls.appkey, "ts": str(int(time.time()))}
        signed["sign"] = cls._sign(signed)
        return signed

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        signed = self._signed_params(params)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                headers={
                    "User-Agent": self.user_agent,
                    "Referer": "https://www.bilibili.com/",
                },
            ) as client:
                if method == "GET":
                    response = await client.get(url, params=signed)
                else:
                    response = await client.post(url, data=signed)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise BiliAPIError("暂时无法连接 B 站 TV 接口") from exc
        if not isinstance(payload, dict):
            raise BiliAPIError("B 站 TV 接口返回异常")
        return payload

    async def _post(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", url, params)

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request("GET", url, params)

    async def create_qr(self) -> dict[str, Any]:
        self._prune_sessions()
        payload = await self._post(
            self.auth_code_url, {"local_id": "0"}
        )
        data = payload.get("data") or {}
        qr_url = data.get("url")
        auth_code = data.get("auth_code")
        if payload.get("code") != 0 or not qr_url or not auth_code:
            raise BiliAPIError(
                payload.get("message") or "无法生成 TV 登录二维码",
                code=payload.get("code"),
            )
        session_id = secrets.token_urlsafe(24)
        self._sessions[session_id] = PendingTVQR(
            id=session_id,
            auth_code=str(auth_code),
            created_at=time.monotonic(),
        )
        return {
            "session_id": session_id,
            "qr_image": BiliClient._qr_data_url(str(qr_url)),
            "status": "waiting",
            "message": "请使用哔哩哔哩 TV 客户端扫码",
        }

    async def poll_qr(self, session_id: str) -> dict[str, Any]:
        pending = self._sessions.get(session_id)
        if not pending:
            raise BiliAPIError("TV 二维码会话不存在或已过期")
        if time.monotonic() - pending.created_at > 180:
            self._sessions.pop(session_id, None)
            return {"status": "expired", "message": "二维码已过期，请重新生成"}
        payload = await self._post(
            self.poll_url,
            {"auth_code": pending.auth_code, "local_id": "0"},
        )
        data = payload.get("data") or {}
        if payload.get("code") == 0:
            access_token = data.get("access_token")
            if not access_token:
                self._sessions.pop(session_id, None)
                raise BiliAPIError("TV 登录成功但未返回 access_token")
            self._sessions.pop(session_id, None)
            return {
                "status": "authorized",
                "message": "TV 登录成功",
                "access_token": access_token,
                "refresh_token": data.get("refresh_token"),
                "account": {
                    "mid": data.get("mid"),
                    "uname": data.get("uname") or data.get("name"),
                },
            }
        code = payload.get("code")
        if code == 86090:
            pending.status = "scanned"
            pending.message = "已扫码，请在手机上确认"
        elif code == 86038:
            pending.status = "expired"
            pending.message = "二维码已过期，请重新生成"
            self._sessions.pop(session_id, None)
        else:
            pending.status = "waiting"
            pending.message = payload.get("message") or "等待扫码"
        return {"status": pending.status, "message": pending.message}

    async def bangumi_playinfo(
        self, *, aid: int | str, cid: int | str, ep_id: int | str, access_token: str
    ) -> dict[str, Any]:
        return await self._tv_playinfo(
            "/pgc/player/api/playurltv",
            access_token=access_token,
            aid=aid,
            cid=cid,
            ep_id=ep_id,
        )

    async def video_playinfo(
        self, *, aid: int | str, cid: int | str, access_token: str
    ) -> dict[str, Any]:
        return await self._tv_playinfo(
            "/x/tv/playurl",
            access_token=access_token,
            aid=aid,
            cid=cid,
        )

    async def _tv_playinfo(
        self,
        path: str,
        *,
        access_token: str,
        aid: int | str,
        cid: int | str,
        ep_id: int | str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "access_key": access_token,
            "build": "106500",
            "cid": str(cid),
            "device": "android",
            "expire": "0",
            "fnval": "4048",
            "fnver": "0",
            "fourk": "1",
            "mid": "0",
            "mobi_app": "android_tv_yst",
            "object_id": str(aid),
            "platform": "android",
            "playurl_type": "1",
            "qn": "127",
        }
        if ep_id is not None:
            params["ep_id"] = str(ep_id)
        payload = await self._get(self.api_host + path, params)
        code = payload.get("code")
        if code != 0:
            message = payload.get("message") or f"TV 接口返回错误 {code}"
            if code in {-101, -40003, 401}:
                raise BiliTVAuthError(message, code=code)
            if code == -10403 or "地区" in message:
                raise BiliTVRegionError(
                    "该内容受地区限制，当前服务器 IP 无法播放；TV 线路不能解除地区限制",
                    code=code,
                )
            raise BiliAPIError(message, code=code)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        dash = payload.get("dash") or data.get("dash")
        if not isinstance(dash, dict):
            raise BiliAPIError("TV 接口未返回可用的视频流")
        return dash

    def _prune_sessions(self) -> None:
        expired = [
            key
            for key, value in self._sessions.items()
            if time.monotonic() - value.created_at > 180
        ]
        for key in expired:
            self._sessions.pop(key, None)
        while len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions.values(), key=lambda item: item.created_at)
            self._sessions.pop(oldest.id, None)

    async def close(self) -> None:
        self._sessions.clear()


class BiliAuthService:
    def __init__(self, database: Database, secret_box: SecretBox) -> None:
        self.database = database
        self.secret_box = secret_box
        self.client = BiliClient()
        self.tv_client = BiliTvClient()

    def status(self) -> dict[str, Any]:
        auth = self.database.get_auth()
        tv = self.tv_status()
        if not auth:
            return {
                "logged_in": False,
                "account_name": None,
                "vip_status": False,
                "vip_due_date": None,
                "updated_at": None,
                **tv,
            }
        return {
            "logged_in": True,
            "account_name": auth.get("account_name"),
            "account_mid": auth.get("account_mid"),
            "vip_status": bool(auth.get("vip_status")),
            "vip_due_date": auth.get("vip_due_date"),
            "updated_at": auth.get("updated_at"),
            **tv,
        }

    def tv_status(self) -> dict[str, Any]:
        auth = self.database.get_tv_auth()
        if not auth:
            return {
                "tv_logged_in": False,
                "tv_account_name": None,
                "tv_account_mid": None,
                "tv_updated_at": None,
            }
        return {
            "tv_logged_in": True,
            "tv_account_name": auth.get("account_name"),
            "tv_account_mid": auth.get("account_mid"),
            "tv_updated_at": auth.get("updated_at"),
        }

    def save_login(
        self, cookies: dict[str, str], account: dict[str, Any] | None
    ) -> None:
        encrypted = self.secret_box.encrypt_json(json.dumps(cookies))
        self.database.save_auth(encrypted, account)

    def load_cookies(self) -> dict[str, str] | None:
        auth = self.database.get_auth()
        if not auth:
            return None
        decrypted = self.secret_box.decrypt_json(auth["cookies_encrypted"])
        value = json.loads(decrypted)
        return value if isinstance(value, dict) else None

    def logout(self) -> None:
        self.database.clear_auth()

    def save_tv_login(
        self,
        access_token: str,
        refresh_token: str | None,
        account: dict[str, Any] | None,
    ) -> None:
        access_encrypted = self.secret_box.encrypt_json(
            json.dumps({"access_token": access_token})
        )
        refresh_encrypted = (
            self.secret_box.encrypt_json(
                json.dumps({"refresh_token": refresh_token})
            )
            if refresh_token
            else None
        )
        self.database.save_tv_auth(access_encrypted, refresh_encrypted, account)

    def load_tv_token(self) -> str | None:
        auth = self.database.get_tv_auth()
        if not auth:
            return None
        decrypted = self.secret_box.decrypt_json(auth["access_token_encrypted"])
        value = json.loads(decrypted)
        if not isinstance(value, dict):
            return None
        token = value.get("access_token")
        return str(token) if token else None

    def tv_logout(self) -> None:
        self.database.clear_tv_auth()

    async def season_view(
        self, *, season_id: int | str | None = None, ep_id: int | str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if season_id is not None:
            params["season_id"] = str(season_id)
        elif ep_id is not None:
            params["ep_id"] = str(ep_id)
        else:
            raise BiliAPIError("缺少季或集参数")
        payload = await self._web_get(
            "https://api.bilibili.com/pgc/view/web/season", params
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BiliAPIError("B 站季信息为空")
        return result

    async def season_sections(self, season_id: int | str) -> dict[str, Any]:
        payload = await self._web_get(
            "https://api.bilibili.com/pgc/web/season/section",
            {"season_id": str(season_id)},
        )
        result = payload.get("result")
        if not isinstance(result, dict):
            raise BiliAPIError("B 站剧集列表为空")
        return result

    async def video_view(
        self, *, bvid: str | None = None, aid: int | str | None = None
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if bvid:
            params["bvid"] = bvid
        elif aid is not None:
            params["aid"] = str(aid)
        else:
            raise BiliAPIError("缺少视频参数")
        payload = await self._web_get(
            "https://api.bilibili.com/x/web-interface/view", params
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise BiliAPIError("B 站视频信息为空")
        return data

    async def tv_bangumi_playinfo(
        self, *, aid: int | str, cid: int | str, ep_id: int | str
    ) -> dict[str, Any]:
        token = self.load_tv_token()
        if not token:
            raise BiliTVAuthError("请先完成 TV 扫码登录")
        return await self.tv_client.bangumi_playinfo(
            aid=aid, cid=cid, ep_id=ep_id, access_token=token
        )

    async def tv_video_playinfo(
        self, *, aid: int | str, cid: int | str
    ) -> dict[str, Any]:
        token = self.load_tv_token()
        if not token:
            raise BiliTVAuthError("请先完成 TV 扫码登录")
        return await self.tv_client.video_playinfo(
            aid=aid, cid=cid, access_token=token
        )

    async def web_playinfo(
        self,
        *,
        cid: int | str,
        bvid: str | None = None,
        aid: int | str | None = None,
    ) -> dict[str, Any]:
        """Return Web DASH streams, including baseUrl and backupUrl lists."""
        params: dict[str, Any] = {
            "cid": str(cid),
            "fnval": "4048",
            "fnver": "0",
            "fourk": "1",
            "platform": "pc",
            "qn": "127",
        }
        if bvid:
            params["bvid"] = bvid
        elif aid is not None:
            params["avid"] = str(aid)
        else:
            raise BiliAPIError("缺少 Web 播放参数")
        payload = await self._web_get(
            "https://api.bilibili.com/x/player/playurl", params
        )
        return self._extract_dash(payload)

    async def pgc_web_playinfo(
        self,
        *,
        aid: int | str,
        cid: int | str,
        ep_id: int | str,
    ) -> dict[str, Any]:
        payload = await self._web_get(
            "https://api.bilibili.com/pgc/player/web/playurl",
            {
                "avid": str(aid),
                "cid": str(cid),
                "ep_id": str(ep_id),
                "fnval": "4048",
                "fnver": "0",
                "fourk": "1",
                "platform": "pc",
                "qn": "127",
            },
        )
        return self._extract_dash(payload)

    @staticmethod
    def _extract_dash(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        if not isinstance(data, dict):
            data = payload.get("result")
        if not isinstance(data, dict):
            raise BiliAPIError("B 站播放接口未返回可用的视频流")
        dash = data.get("dash")
        if not isinstance(dash, dict):
            raise BiliAPIError("B 站播放接口未返回 DASH 片源")
        return dash

    async def _web_get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        cookies = self.load_cookies() or {}
        cookie_header = "; ".join(
            f"{key}={value}" for key, value in cookies.items()
        )
        headers = {
            "User-Agent": self.client.user_agent,
            "Referer": "https://www.bilibili.com/",
        }
        if cookie_header:
            headers["Cookie"] = cookie_header
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(20.0, connect=10.0),
                follow_redirects=True,
                headers=headers,
            ) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise BiliAPIError("暂时无法读取 B 站页面信息") from exc
        if payload.get("code") != 0:
            raise BiliAPIError(
                payload.get("message") or "B 站接口返回异常",
                code=payload.get("code"),
            )
        return payload

    async def close(self) -> None:
        await self.client.close()
        await self.tv_client.close()

    def write_cookie_file(self, path: Path, cookies: dict[str, str]) -> None:
        lines = [
            "# Netscape HTTP Cookie File",
            "# Generated by Bili Remote. Do not edit or share.",
        ]
        for name, value in cookies.items():
            lines.append(
                "\t".join(
                    (".bilibili.com", "TRUE", "/", "TRUE", "2147483647", name, value)
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
