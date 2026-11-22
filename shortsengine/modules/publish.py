"""Optional YouTube publishing via the official YouTube Data API v3 with OAuth 2.0
(installed-app flow) for the user's own channel.

* client_secret.json path comes from YOUTUBE_CLIENT_SECRETS (.env); never committed.
* the OAuth token is stored in data/secrets/youtube_token.json (gitignored).
* uploads default to PRIVATE; public publishing requires explicit settings; scheduled
  publishing uses status.publishAt (video must be private until then).
No cookies, scraping or credential extraction are used."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db, logs
from ..config import data_dir, load_settings, secret

log = logs.get("publish")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/yt-analytics.readonly"]


class PublishError(RuntimeError):
    pass


def token_path() -> Path:
    d = data_dir() / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    return d / "youtube_token.json"


def authorize(port: int = 8765, open_browser: bool = False):
    """Run the OAuth consent flow once (prints a URL to open). Stores a refreshable token."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    cs = secret("YOUTUBE_CLIENT_SECRETS")
    if not cs or not Path(cs).exists():
        raise PublishError("set YOUTUBE_CLIENT_SECRETS in .env to your OAuth client_secret.json path")
    flow = InstalledAppFlow.from_client_secrets_file(cs, SCOPES)
    creds = flow.run_local_server(port=port, open_browser=open_browser, prompt="consent")
    token_path().write_text(creds.to_json())
    return creds


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    p = token_path()
    if not p.exists():
        raise PublishError("YouTube is not authorised yet: run `python -m shortsengine youtube-auth`")
    creds = Credentials.from_authorized_user_file(str(p), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            p.write_text(creds.to_json())
        else:
            raise PublishError("YouTube token invalid; re-run youtube-auth")
    return creds


def youtube():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=credentials(), cache_discovery=False)


def status() -> dict:
    cs = secret("YOUTUBE_CLIENT_SECRETS")
    return {"client_secrets_configured": bool(cs and Path(cs).exists()), "authorized": token_path().exists(),
            "auto_publish": load_settings().automation.auto_publish,
            "default_privacy": load_settings().automation.publish_privacy}


def request_body(meta: dict, privacy: str, publish_at: datetime | None) -> dict:
    st = {"privacyStatus": privacy, "selfDeclaredMadeForKids": meta.get("made_for_kids", False),
          "containsSyntheticMedia": meta.get("contains_synthetic_media", True)}
    if publish_at:
        st["privacyStatus"] = "private"  # required by the API for scheduled publishing
        st["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return {"snippet": {"title": meta["title"], "description": meta["description"], "tags": meta["tags"],
                        "categoryId": meta.get("category_id", "22"),
                        "defaultLanguage": meta.get("default_language", "en")},
            "status": st}


def next_publish_slot(hour_utc: int) -> datetime:
    now = datetime.now(timezone.utc)
    slot = now.replace(hour=hour_utc, minute=5, second=0, microsecond=0)
    return slot if slot > now + timedelta(minutes=20) else slot + timedelta(days=1)


def upload(video_row_id: int, privacy: str | None = None, schedule: bool = False, client=None) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    v = db.q1("SELECT * FROM videos WHERE id=?", (video_row_id,))
    if not v:
        raise PublishError(f"video {video_row_id} not found")
    if v["youtube_id"]:
        return {"youtube_id": v["youtube_id"], "status": v["publish_status"], "skipped": "already uploaded"}
    a = load_settings().automation
    privacy = privacy or a.publish_privacy
    meta = {"title": v["title"], "description": v["description"], "tags": json.loads(v["tags"] or "[]"),
            **json.loads(v["features"] or "{}").get("metadata_flags", {})}
    publish_at = next_publish_slot(a.publish_hour_utc) if (schedule and privacy == "public") else None
    body = request_body(meta, privacy, publish_at)
    db.ex("UPDATE videos SET publish_status='uploading', publish_error=NULL WHERE id=?", (video_row_id,))
    try:
        yt = client or youtube()
        media = MediaFileUpload(v["path"], mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp, tries = None, 0
        while resp is None:
            try:
                _, resp = req.next_chunk()
            except HttpError as e:
                if e.resp.status in (500, 502, 503, 504) and tries < 5:
                    tries += 1
                    time.sleep(2 ** tries)
                    continue
                raise
        yid = resp["id"]
        st = "scheduled" if publish_at else "uploaded"
        db.ex("UPDATE videos SET youtube_id=?, privacy=?, publish_status=?, published_at=? WHERE id=?",
              (yid, body["status"]["privacyStatus"], st, time.time(), video_row_id))
        log.info("uploaded video %s -> %s (%s)", video_row_id, yid, st)
        return {"youtube_id": yid, "status": st, "publish_at": body["status"].get("publishAt")}
    except Exception as e:
        db.ex("UPDATE videos SET publish_status='failed', publish_error=? WHERE id=?", (str(e)[:1000], video_row_id))
        log.error("upload failed for video %s: %s", video_row_id, e)
        raise PublishError(str(e)) from e
