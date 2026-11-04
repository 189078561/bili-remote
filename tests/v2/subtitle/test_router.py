import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from bili_remote.v2.subtitle import create_router


def app_with_router():
    calls = {"auth": 0, "csrf": 0}

    async def auth():
        calls["auth"] += 1

    async def csrf(request: Request):
        calls["csrf"] += 1
        if request.headers.get("x-csrf-token") != "valid":
            raise HTTPException(status_code=403, detail="csrf")

    app = FastAPI()
    app.include_router(create_router(require_authenticated=auth, require_csrf=csrf))
    return app, calls


def test_render_endpoint_returns_live_render_and_no_store():
    app, _ = app_with_router()
    context = {"revision": 27, "metadata": {"title_zh": "即时标题", "genres": []},
               "episode": {"episode": 8}, "cast": []}
    response = TestClient(app).post(
        "/api/v2/subtitles/render", headers={"x-csrf-token": "valid"},
        json={"context": context, "rule": {"template_id": "tv_episode"}},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "即时标题 第08集"
    assert response.json()["context_revision"] == 27
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_render_endpoint_enforces_auth_and_csrf_dependencies():
    app, calls = app_with_router()
    client = TestClient(app)
    body = {"context": {"metadata": {"title_zh": "x"}}, "rule": {}}
    assert client.post("/api/v2/subtitles/render", json=body).status_code == 403
    assert client.post("/api/v2/subtitles/render", headers={"x-csrf-token": "valid"}, json=body).status_code == 200
    assert calls == {"auth": 2, "csrf": 2}


def test_render_endpoint_rejects_unknown_request_fields_and_bad_rules():
    app, _ = app_with_router()
    client = TestClient(app)
    headers = {"x-csrf-token": "valid"}
    response = client.post("/api/v2/subtitles/render", headers=headers,
                           json={"context": {}, "rule": {}, "subtitle_text": "saved draft"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"
    bad_rule = client.post("/api/v2/subtitles/render", headers=headers,
                           json={"context": {"revision": 1}, "rule": {"platform": "streamer"}})
    assert bad_rule.status_code == 422
    assert bad_rule.json()["detail"]["fields"] == ["platform"]


def test_render_endpoint_limits_payload_size():
    app, _ = app_with_router()
    body = json.dumps({"context": {}, "rule": {}, "extra": "x" * (4 * 1024 * 1024)}).encode()
    response = TestClient(app).post(
        "/api/v2/subtitles/render", headers={"x-csrf-token": "valid", "content-type": "application/json"},
        content=body,
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_openapi_describes_context_rule_envelope_and_platform_exclusion():
    app, _ = app_with_router()
    operation = app.openapi()["paths"]["/api/v2/subtitles/render"]["post"]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["context", "rule"]
    assert schema["additionalProperties"] is False
    assert "platform is not supported" in schema["properties"]["rule"]["description"]
