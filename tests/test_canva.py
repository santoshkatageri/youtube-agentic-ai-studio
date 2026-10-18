"""Canva integration tests use mocked HTTP; they never contact Canva."""
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

import config
import gui
from apis import canva


@pytest.fixture
def connection(tmp_path, monkeypatch):
    monkeypatch.setenv("CANVA_CLIENT_ID", "test-client")
    monkeypatch.setenv("CANVA_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("CANVA_REDIRECT_URI", "http://127.0.0.1:7070/api/canva/callback")
    monkeypatch.setattr(canva, "TOKEN_PATH", tmp_path / ".tokens.json")
    monkeypatch.setattr(canva, "_pending", {})
    monkeypatch.setattr(config, "OUTPUT_DIR", str(tmp_path))
    for key, value in {"running": False, "script": {"title":"Test", "video_type":"shorts", "sections":[{"id":1,"narration":"Inspect logs."}]},
                       "stages": dict(gui._state["stages"])}.items():
        monkeypatch.setitem(gui._state, key, value)
    for key in ["video_type", "draft_ready", "library_id", "video_path", "thumb_path", "audio_path", "description", "yt_url"]:
        monkeypatch.setitem(gui._state, key, gui._state.get(key))
    return gui.app.test_client(), tmp_path


def token_response(**overrides):
    values = {"access_token":"access", "refresh_token":"refresh", "expires_in":3600}
    return SimpleNamespace(ok=True, json=lambda: values | overrides)


def test_pkce_exchange_and_private_token_storage(connection, monkeypatch):
    _, path = connection
    calls = []
    def post(url, **kw):
        calls.append((url, kw))
        return token_response()
    monkeypatch.setattr(canva.requests, "post", post)
    state, url = canva.authorization_url()
    params = parse_qs(urlparse(url).query)
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["design:meta:read design:content:read"]
    verifier = canva._pending[state][0]
    assert verifier not in url
    canva.finish_authorization(state, "code")
    assert calls[0][1]["data"]["code_verifier"] == verifier
    assert canva.status()["connected"]
    assert canva.TOKEN_PATH.stat().st_mode & 0o777 == 0o600
    assert "access_token" not in canva.status()
    with pytest.raises(canva.CanvaError):
        canva.finish_authorization(state, "code")


def test_oauth_callback_rejects_unrelated_browser_state(connection, monkeypatch):
    client, _ = connection
    monkeypatch.setattr(canva, "finish_authorization", lambda *a: pytest.fail("Unverified callback"))
    assert client.get("/api/canva/callback?state=forged&code=x").status_code == 400
    with client.session_transaction() as session:
        session["canva_state"] = "expected"
    assert client.get("/api/canva/callback?state=other&code=x").status_code == 400


def test_refresh_rotates_token_without_exposing_it(connection, monkeypatch):
    canva._save_tokens({"access_token":"old", "refresh_token":"old-refresh", "expires_in":-10})
    captured = []
    monkeypatch.setattr(canva.requests, "post", lambda url, **kw: captured.append(kw) or token_response(refresh_token="rotated"))
    assert canva._access_token() == "access"
    assert captured[0]["data"] == {"grant_type":"refresh_token", "refresh_token":"old-refresh"}
    assert canva._read_tokens()["refresh_token"] == "rotated"


def test_export_requests_one_page_without_generation(connection, monkeypatch):
    calls = []
    monkeypatch.setattr(canva, "_request", lambda *a, **kw: calls.append((a,kw)) or {"job":{"id":"job-1","status":"in_progress"}})
    canva.create_export("D123", "png", 2)
    assert calls == [(("POST", "/exports"), {"json":{"design_id":"D123", "format":{"type":"png","pages":[2]}}})]
    with pytest.raises(canva.CanvaError):
        canva.create_export("../credentials", "png", 1)


def test_export_download_rejects_other_hosts(connection, monkeypatch):
    _, path = connection
    monkeypatch.setattr(canva.requests, "get", lambda *a, **kw: pytest.fail("Unexpected download"))
    for url in ["http://127.0.0.1/file", "https://example.com/file", "https://export-download.canva.com.evil.test/file"]:
        with pytest.raises(canva.CanvaError):
            canva.download_export(url, path / "asset.png")


def test_api_import_attaches_once_and_rejects_changed_drafts(connection, monkeypatch):
    client, path = connection
    monkeypatch.setattr(canva, "create_export", lambda *a: {"id":"job-1"})
    monkeypatch.setattr(canva, "export_job", lambda *a: {"status":"success", "urls":["https://export-download.canva.com/asset"]})
    downloads = []
    def download(url, destination):
        downloads.append(destination)
        Image.new("RGB", (80,40), "teal").save(destination)
    monkeypatch.setattr(canva, "download_export", download)
    body = {"scene_id":1,"design_id":"D123","format":"png","page":1}
    assert client.post("/api/canva/exports", json=body).json["job_id"] == "job-1"
    assert client.post("/api/canva/exports/job-1", json={}).json["ok"]
    assert client.post("/api/canva/exports/job-1", json={}).json["ok"]
    assert len(downloads) == 1
    section = json.loads((path / "script.json").read_text())["sections"][0]
    assert Path(section["visual_asset"]).is_file()
    assert section["visual_source"]["provider"] == "Canva"
    # A fresh export cannot silently attach to a different draft.
    client.post("/api/canva/exports", json=body)
    gui._state["script"]["sections"][0]["narration"] = "Changed script"
    response = client.post("/api/canva/exports/job-1", json={})
    assert response.status_code == 400 and "draft changed" in response.json["error"]
    assert len(downloads) == 1


def test_auth_errors_do_not_include_provider_payload_or_tokens(connection, monkeypatch):
    monkeypatch.setattr(canva, "_access_token", lambda: "private-token")
    monkeypatch.setattr(canva.requests, "request", lambda *a, **kw: SimpleNamespace(ok=False, status_code=403, text="private-token secret details"))
    with pytest.raises(canva.CanvaError) as error:
        canva.designs()
    assert "403" in str(error.value) and "private-token" not in str(error.value)


def test_connect_callback_is_bound_to_the_same_browser(connection, monkeypatch):
    client, _ = connection
    calls = []
    monkeypatch.setattr(canva, "finish_authorization", lambda state, code: calls.append((state, code)))
    response = client.get("/api/canva/connect", base_url="http://127.0.0.1:7070")
    assert response.status_code == 302
    params = parse_qs(urlparse(response.location).query)
    state = params["state"][0]
    response = client.get("/api/canva/callback", query_string={"state":state,"code":"auth-code"}, base_url="http://127.0.0.1:7070")
    assert response.location == "/#scenes" and calls == [(state,"auth-code")]
    assert client.get("/api/canva/callback", query_string={"state":state,"code":"auth-code"}, base_url="http://127.0.0.1:7070").status_code == 400


def test_setup_writes_credentials_only_to_local_env(connection, monkeypatch):
    client, path = connection
    env = path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", str(env))
    monkeypatch.setattr(config, "save_env", lambda values: env.write_text("\n".join(k+"="+v for k,v in values.items())))
    body = {"client_id":"new-client","client_secret":"new-secret","redirect_uri":"http://127.0.0.1:7070/api/canva/callback"}
    response = client.post("/api/canva/settings", json=body)
    assert response.json == {"ok":True}
    assert "new-secret" in env.read_text() and env.stat().st_mode & 0o777 == 0o600
    assert "new-secret" not in client.get("/api/canva/status").text
    body["client_secret"] = "secret\nINJECTED=value"
    assert client.post("/api/canva/settings", json=body).status_code == 400
    assert "INJECTED" not in env.read_text()


def test_cross_origin_forms_cannot_change_settings(connection):
    client, _ = connection
    response = client.post("/api/canva/settings", json={}, headers={"Origin":"https://another-site.example"})
    assert response.status_code == 403
    response = client.post("/api/run", json={}, headers={"Origin":"https://another-site.example"})
    assert response.status_code == 403
