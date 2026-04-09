#!/usr/bin/env python3
"""Fetch tweets from @Milifney100 via FxTwitter API (free, no key required)."""

import re
import requests
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "Milifney100"
API_BASE = "https://api.fxtwitter.com/2/profile"

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
IMAGES_DIR = REPO_ROOT / "assets" / "images" / "tweets"
STATE_FILE = REPO_ROOT / "scripts" / ".last_fetch_timestamp"


def fetch_timeline(since_timestamp=None):
    """Fetch recent tweets from the FxTwitter v2 API."""
    url = f"{API_BASE}/{USERNAME}/statuses"
    params = {}
    if since_timestamp:
        params["since"] = since_timestamp
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 204:
        return []
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", [])


def download_image(url, tweet_id):
    """Download a tweet image and return its repo-relative path."""
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    ext = url.rsplit(".", 1)[-1].split("?")[0]
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        ext = "jpg"
    filename = f"{tweet_id}.{ext}"
    filepath = IMAGES_DIR / filename
    if not filepath.exists():
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
    return f"/assets/images/tweets/{filename}"


def clean_text(text):
    """Remove t.co URLs that Twitter appends for media."""
    return re.sub(r"\s*https://t\.co/\S+", "", text).strip()


def is_reply(tweet):
    """Check if a tweet is a reply to another user (not a self-thread)."""
    replying_to = tweet.get("replying_to")
    if not replying_to:
        return False
    return replying_to.get("screen_name", "").lower() != USERNAME.lower()


def create_post(tweet):
    """Create a Jekyll markdown post from a tweet. Returns True if new."""
    # Skip replies to other users and retweets
    if is_reply(tweet):
        return False
    if tweet.get("reposted_by"):
        return False

    text = clean_text(tweet.get("text", ""))
    if not text:
        return False

    created_ts = tweet.get("created_timestamp", 0)
    created = datetime.fromtimestamp(created_ts, tz=timezone.utc)

    # First line = title, rest = body
    lines = text.split("\n", 1)
    title = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ""

    # Avoid duplicate posts
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = created.strftime("%Y-%m-%d")
    tweet_id = tweet["id"]
    filename = f"{date_str}-{tweet_id}.md"
    filepath = POSTS_DIR / filename
    if filepath.exists():
        return False

    # Download first photo if available
    image_path = ""
    media = tweet.get("media", {})
    photos = media.get("photos", [])
    if photos:
        img_url = photos[0].get("url", "")
        if img_url:
            image_path = download_image(img_url, tweet_id)

    # Build front matter
    safe_title = title.replace('"', '\\"')
    fm = [
        "---",
        "layout: post",
        f'title: "{safe_title}"',
        f"date: {created.isoformat()}",
        f'tweet_id: "{tweet_id}"',
    ]
    if image_path:
        fm.append(f"image: {image_path}")
    fm.append("---")
    fm.append("")

    # Build body
    parts = []
    if body:
        parts.append(body)
    parts.append("")  # trailing newline

    filepath.write_text("\n".join(fm) + "\n".join(parts), encoding="utf-8")
    print(f"  Created: {filename}")
    return True


def main():
    # Read last fetch timestamp (to avoid re-fetching)
    since_ts = None
    if STATE_FILE.exists():
        since_ts = STATE_FILE.read_text(encoding="utf-8").strip()

    print(f"Fetching tweets for @{USERNAME} via FxTwitter API...")
    tweets = fetch_timeline(since_ts)

    if not tweets:
        print("No new tweets found.")
        return

    # Process tweets oldest-first
    tweets.sort(key=lambda t: t.get("created_timestamp", 0))
    count = sum(1 for t in tweets if create_post(t))

    # Save the latest timestamp for next run
    if tweets:
        latest_ts = max(t.get("created_timestamp", 0) for t in tweets)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(str(latest_ts), encoding="utf-8")

    print(f"Done. Created {count} new post(s).")


if __name__ == "__main__":
    main()
