#!/usr/bin/env python3
"""Fetch tweets from @Milifney100 via FxTwitter API (free, no key required)."""

import re
import time
import requests
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "Milifney100"
API_BASE = "https://api.fxtwitter.com/2/profile"

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
IMAGES_DIR = REPO_ROOT / "assets" / "images" / "tweets"
STATE_FILE = REPO_ROOT / "scripts" / ".last_fetch_timestamp"

# Pagination settings — be polite to FxTwitter
PAGE_DELAY = 4       # seconds between API requests
MAX_PAGES = 120      # safety cap (~10 tweets/page → ~1200 max)


def fetch_timeline(since_timestamp=None):
    """Fetch tweets with pagination. Waits between pages to be gentle."""
    url = f"{API_BASE}/{USERNAME}/statuses"
    all_tweets = []
    cursor = None

    for page in range(MAX_PAGES):
        params = {}
        if since_timestamp:
            params["since"] = since_timestamp
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code == 204:
            break
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        all_tweets.extend(results)
        print(f"  Page {page + 1}: +{len(results)} tweets (total: {len(all_tweets)})")

        next_cursor = data.get("cursor", {}).get("bottom")
        if not next_cursor:
            break
        cursor = next_cursor

        # Wait between pages — don't hammer the API
        time.sleep(PAGE_DELAY)

    return all_tweets


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


def collect_images(tweet):
    """Get all photo URLs from a tweet."""
    media = tweet.get("media", {})
    return [p["url"] for p in media.get("photos", []) if p.get("url")]


def clean_text(text):
    """Remove t.co URLs that Twitter appends for media."""
    return re.sub(r"\s*https://t\.co/\S+", "", text).strip()


def is_self_reply(tweet):
    """Check if a tweet is a reply to the same account (thread continuation)."""
    replying_to = tweet.get("replying_to")
    if not replying_to:
        return False
    return replying_to.get("screen_name", "").lower() == USERNAME.lower()


def is_reply_to_other(tweet):
    """Check if a tweet is a reply to a different user."""
    replying_to = tweet.get("replying_to")
    if not replying_to:
        return False
    return replying_to.get("screen_name", "").lower() != USERNAME.lower()


def merge_threads(tweets):
    """Group self-reply threads into single posts.

    Returns a list of 'merged' tweet dicts. Each thread head absorbs the text
    and images of its self-reply children, oldest to newest.
    """
    by_id = {t["id"]: t for t in tweets}
    children = set()  # IDs that are thread continuations

    # Find which tweets are thread replies and map them to parents
    for t in tweets:
        if is_self_reply(t):
            parent_id = t["replying_to"].get("status")
            if parent_id and parent_id in by_id:
                children.add(t["id"])

    # Build threads: for each head tweet, walk the chain forward
    # First build a forward map: parent_id -> list of child tweets
    child_map = {}
    for t in tweets:
        if t["id"] in children:
            parent_id = t["replying_to"]["status"]
            child_map.setdefault(parent_id, []).append(t)

    # Sort children by timestamp
    for kids in child_map.values():
        kids.sort(key=lambda t: t.get("created_timestamp", 0))

    merged = []
    for t in tweets:
        if t["id"] in children:
            continue  # skip, will be merged into parent
        if t.get("reposted_by"):
            continue
        if is_reply_to_other(t):
            continue

        # Collect text and images from the thread chain
        chain_texts = [clean_text(t.get("text", ""))]
        chain_images = collect_images(t)

        # Walk forward through the thread
        current_id = t["id"]
        while current_id in child_map:
            for child in child_map[current_id]:
                child_text = clean_text(child.get("text", ""))
                if child_text:
                    chain_texts.append(child_text)
                chain_images.extend(collect_images(child))
                current_id = child["id"]
            if current_id == t["id"]:
                break  # no more children

        merged.append({
            "id": t["id"],
            "created_timestamp": t.get("created_timestamp", 0),
            "tweet_id": t["id"],
            "merged_text": "\n\n".join(chain_texts),
            "images": chain_images,
        })

    return merged


def create_post(item):
    """Create a Jekyll markdown post from a merged tweet. Returns True if new."""
    text = item["merged_text"]
    if not text:
        return False

    created_ts = item["created_timestamp"]
    created = datetime.fromtimestamp(created_ts, tz=timezone.utc)

    # First line = title, rest = body
    lines = text.split("\n", 1)
    title = lines[0].strip()
    body = lines[1].strip() if len(lines) > 1 else ""

    # Avoid duplicate posts
    POSTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = created.strftime("%Y-%m-%d")
    tweet_id = item["tweet_id"]
    filename = f"{date_str}-{tweet_id}.md"
    filepath = POSTS_DIR / filename
    if filepath.exists():
        return False

    # Download first image
    image_path = ""
    if item["images"]:
        image_path = download_image(item["images"][0], tweet_id)

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

    # Build body — include additional images as markdown
    parts = []
    if body:
        parts.append(body)
    for img_url in item["images"][1:]:
        local = download_image(img_url, f"{tweet_id}_{item['images'].index(img_url)}")
        parts.append(f"\n![]({local})")
    parts.append("")

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

    # Merge threads and process oldest-first
    items = merge_threads(tweets)
    items.sort(key=lambda t: t.get("created_timestamp", 0))
    count = sum(1 for t in items if create_post(t))

    # Save the latest timestamp for next run
    if tweets:
        latest_ts = max(t.get("created_timestamp", 0) for t in tweets)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(str(latest_ts), encoding="utf-8")

    print(f"Done. Created {count} new post(s).")


if __name__ == "__main__":
    main()
