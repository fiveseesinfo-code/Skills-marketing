"""Publish due posts from posts/queue.yml to LinkedIn, Facebook, X and Bluesky.

A platform is enabled only when its environment variables are set. Results are
written back into the queue file (posted: {platform: id}) so nothing is posted
twice, and a platform that fails is retried on the next run.
"""

import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests
import yaml

QUEUE = os.environ.get("QUEUE_FILE", "posts/queue.yml")
TZ = ZoneInfo(os.environ.get("POST_TIMEZONE", "America/Moncton"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
TIMEOUT = 30


def env(*names):
    vals = [os.environ.get(n, "").strip() for n in names]
    return vals if all(vals) else None


def full_text(post):
    text = post["text"].strip()
    link = post.get("link")
    return f"{text}\n\n{link}" if link and link not in text else text


# ---------------- platforms ----------------

def post_linkedin(post, creds):
    token, author = creds
    version = os.environ.get("LINKEDIN_VERSION") or "202510"
    body = {
        "author": author,
        "commentary": full_text(post),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    r = requests.post(
        "https://api.linkedin.com/rest/posts",
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.headers.get("x-restli-id", "ok")


def post_facebook(post, creds):
    page_id, token = creds
    data = {"message": post["text"].strip(), "access_token": token}
    if post.get("link"):
        data["link"] = post["link"]
    r = requests.post(
        f"https://graph.facebook.com/v21.0/{page_id}/feed", data=data, timeout=TIMEOUT
    )
    r.raise_for_status()
    return r.json().get("id", "ok")


def _oauth1_header(method, url, key, key_secret, token, token_secret):
    q = lambda s: urllib.parse.quote(str(s), safe="~")
    params = {
        "oauth_consumer_key": key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    base_params = "&".join(f"{q(k)}={q(v)}" for k, v in sorted(params.items()))
    base = "&".join([method, q(url), q(base_params)])
    signing_key = f"{q(key_secret)}&{q(token_secret)}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base.encode(), hashlib.sha1).digest()
    ).decode()
    params["oauth_signature"] = sig
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(params.items()))


def post_x(post, creds):
    text = full_text(post)
    if len(text) > 280:
        raise ValueError(f"too long for X ({len(text)}/280 chars)")
    url = "https://api.x.com/2/tweets"
    r = requests.post(
        url,
        json={"text": text},
        headers={"Authorization": _oauth1_header("POST", url, *creds)},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["data"]["id"]


def post_bluesky(post, creds):
    handle, password = creds
    text = full_text(post)
    if len(text) > 300:
        raise ValueError(f"too long for Bluesky ({len(text)}/300 chars)")
    s = requests.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=TIMEOUT,
    )
    s.raise_for_status()
    session = s.json()

    # Make URLs clickable (Bluesky needs byte-offset "facets").
    facets = []
    raw = text.encode()
    for m in re.finditer(rb"https?://[^\s]+", raw):
        facets.append({
            "index": {"byteStart": m.start(), "byteEnd": m.end()},
            "features": [{"$type": "app.bsky.richtext.facet#link", "uri": m.group().decode()}],
        })
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if facets:
        record["facets"] = facets
    r = requests.post(
        "https://bsky.social/xrpc/com.atproto.repo.createRecord",
        json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
        headers={"Authorization": f"Bearer {session['accessJwt']}"},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json().get("uri", "ok")


PLATFORMS = {
    "linkedin": (post_linkedin, ("LINKEDIN_ACCESS_TOKEN", "LINKEDIN_AUTHOR_URN")),
    "facebook": (post_facebook, ("FACEBOOK_PAGE_ID", "FACEBOOK_PAGE_TOKEN")),
    "x": (post_x, ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET")),
    "bluesky": (post_bluesky, ("BLUESKY_HANDLE", "BLUESKY_APP_PASSWORD")),
}


# ---------------- queue ----------------

def parse_date(value):
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def main():
    with open(QUEUE) as f:
        queue = yaml.safe_load(f) or {}
    posts = queue.get("posts") or []

    enabled = {name: env(*names) for name, (_, names) in PLATFORMS.items()}
    active = [n for n, c in enabled.items() if c]
    print(f"Platforms configured: {', '.join(active) or 'none'}" + (" (DRY RUN)" if DRY_RUN else ""))

    now = datetime.now(TZ)
    failures = 0
    changed = False

    for post in posts:
        pid = post.get("id", "?")
        if not post.get("text") or not post.get("date"):
            print(f"[{pid}] skipped: needs 'text' and 'date'")
            continue
        if parse_date(post["date"]) > now:
            continue
        wanted = post.get("platforms") or list(PLATFORMS)
        done = post.setdefault("posted", {}) or {}
        post["posted"] = done

        for name in wanted:
            if name in done:
                continue
            if name not in PLATFORMS:
                print(f"[{pid}] unknown platform '{name}'")
                continue
            creds = enabled[name]
            if not creds:
                print(f"[{pid}] {name}: not configured, skipping")
                continue
            if DRY_RUN:
                print(f"[{pid}] {name}: would post:\n{full_text(post)}\n")
                continue
            try:
                result = PLATFORMS[name][0](post, creds)
                done[name] = str(result)
                changed = True
                print(f"[{pid}] {name}: posted ({result})")
            except Exception as e:  # keep going with other platforms
                failures += 1
                detail = getattr(getattr(e, "response", None), "text", "")[:300]
                print(f"[{pid}] {name}: FAILED - {e} {detail}")

        if not post["posted"]:
            del post["posted"]

    if changed:
        with open(QUEUE, "w") as f:
            yaml.safe_dump(queue, f, sort_keys=False, allow_unicode=True, width=1000)

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
