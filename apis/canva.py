"""Small Canva REST client: OAuth PKCE, design search, and existing-design exports."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlencode, urlparse

import requests

API = "https://api.canva.com/rest/v1"
SCOPES = "design:meta:read design:content:read"
TOKEN_PATH = Path(__file__).resolve().parents[1] / ".canva_tokens.json"
_lock = threading.RLock()
_pending = {}


class CanvaError(Exception):
    pass


def credentials():
    return os.getenv("CANVA_CLIENT_ID", ""), os.getenv("CANVA_CLIENT_SECRET", "")


def redirect_uri():
    return os.getenv("CANVA_REDIRECT_URI", "").strip() or "http://127.0.0.1:7070/api/canva/callback"


def _read_tokens():
    try:
        data = json.loads(TOKEN_PATH.read_text())
        return data if data.get("client_id") == credentials()[0] else {}
    except (OSError, ValueError):
        return {}


def _save_tokens(data):
    data = dict(data, client_id=credentials()[0], expires_at=time.time() + data.get("expires_in", 3600))
    temporary = TOKEN_PATH.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    temporary.replace(TOKEN_PATH)
    os.chmod(TOKEN_PATH, 0o600)
    return data


def status():
    key, secret = credentials()
    return {"configured": bool(key and secret), "connected": bool(_read_tokens().get("refresh_token")),
            "redirect_uri": redirect_uri()}


def authorization_url():
    key, secret = credentials()
    if not key or not secret:
        raise CanvaError("Add your Canva developer credentials in the Canva setup panel first.")
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with _lock:
        now = time.time()
        for old in list(_pending):
            if _pending[old][1] < now:
                _pending.pop(old)
        _pending[state] = (verifier, now + 600)
    return state, "https://www.canva.com/api/oauth/authorize?" + urlencode({
        "client_id": key, "response_type": "code", "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256", "redirect_uri": redirect_uri(),
    })


def _exchange(data):
    try:
        response = requests.post(API + "/oauth/token", data=data, auth=credentials(), timeout=30)
    except requests.RequestException:
        raise CanvaError("Could not reach Canva. Check the connection and try again.") from None
    if not response.ok:
        raise CanvaError(f"Canva authorization failed (HTTP {response.status_code}). Check the credentials or reconnect.")
    return _save_tokens(response.json())


def finish_authorization(state, code):
    with _lock:
        record = _pending.pop(state, None)
        if not record or record[1] < time.time() or not code:
            raise CanvaError("Canva sign-in expired or was already used. Connect again.")
        _exchange({"grant_type": "authorization_code", "code": code,
                   "code_verifier": record[0], "redirect_uri": redirect_uri()})


def _access_token():
    with _lock:
        tokens = _read_tokens()
        if not tokens.get("refresh_token"):
            raise CanvaError("Connect your Canva account first.")
        if tokens.get("expires_at", 0) <= time.time() + 60:
            tokens = _exchange({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
        return tokens["access_token"]


def _request(method, path, **kwargs):
    token = _access_token()
    try:
        r = requests.request(method, API + path, headers={"Authorization": "Bearer " + token}, timeout=30, **kwargs)
    except requests.RequestException:
        raise CanvaError("Canva did not respond. Check the connection before retrying.") from None
    if not r.ok:
        raise CanvaError(f"Canva request failed (HTTP {r.status_code}). Check access, scopes, and the design's export permissions.")
    return r.json()


def designs(query="", continuation=""):
    params = {"query": query[:255], "sort_by": "modified_descending", "limit": 25}
    if continuation:
        params["continuation"] = continuation
    data = _request("GET", "/designs", params=params)
    return {"items": [{"id": d["id"], "title": d.get("title", "Untitled design")} for d in data.get("items", [])],
            "continuation": data.get("continuation")}


def create_export(design_id, fmt, page):
    if not isinstance(design_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", design_id) or fmt not in ("png", "mp4"):
        raise CanvaError("Choose a Canva design and PNG or MP4 format.")
    if type(page) is not int or not 1 <= page <= 1000:
        raise CanvaError("Choose a page number between 1 and 1000.")
    return _request("POST", "/exports", json={"design_id": design_id, "format": {"type": fmt, "pages": [page]}})["job"]


def export_job(job_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", job_id or ""):
        raise CanvaError("Invalid Canva export job.")
    return _request("GET", "/exports/" + job_id)["job"]


def download_export(url, destination):
    # Only Canva's documented export host. Never send the OAuth token to downloads.
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "export-download.canva.com" or parsed.port not in (None, 443):
        raise CanvaError("Canva returned an unsupported download host.")
    try:
        with requests.get(url, stream=True, timeout=60, allow_redirects=False) as response:
            if response.status_code != 200:
                raise CanvaError("Canva export download failed. Check the export status and retry importing.")
            size = 0
            with open(destination, "wb") as f:
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 250 * 1024 * 1024:
                        raise CanvaError("Canva export exceeds the 250 MB import limit.")
                    f.write(chunk)
    except requests.RequestException:
        raise CanvaError("Canva download was interrupted. Retry importing the existing export.") from None


def disconnect():
    with _lock:
        tokens = _read_tokens()
        if tokens.get("refresh_token"):
            try:
                response = requests.post(API + "/oauth/revoke", data={"token": tokens["refresh_token"]}, auth=credentials(), timeout=30)
                if not response.ok:
                    raise CanvaError("Canva could not revoke access. Try again before removing the connection.")
            except requests.RequestException:
                raise CanvaError("Canva could not be reached to revoke access. Try again.") from None
        TOKEN_PATH.unlink(missing_ok=True)
        _pending.clear()
