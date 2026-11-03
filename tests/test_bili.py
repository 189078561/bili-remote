from __future__ import annotations

from bili_remote.bili import BiliTvClient


async def test_tv_bangumi_playinfo_uses_get() -> None:
    client = BiliTvClient()
    calls: list[tuple[str, dict]] = []

    async def fake_get(url: str, params: dict) -> dict:
        calls.append((url, params))
        return {
            "code": 0,
            "dash": {
                "video": [{"id": 112, "base_url": "https://example.com/v"}],
                "audio": [],
            },
        }

    client._get = fake_get
    dash = await client.bangumi_playinfo(
        aid=1, cid=2, ep_id=3, access_token="token"
    )
    assert dash["video"][0]["id"] == 112
    assert calls[0][0].endswith("/pgc/player/api/playurltv")
    assert calls[0][1]["ep_id"] == "3"
    assert calls[0][1]["cid"] == "2"
