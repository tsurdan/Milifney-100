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
    media = tweet.get("media") or {}
    return [p["url"] for p in media.get("photos", []) if p.get("url")]


def clean_text(text):
    """Remove t.co URLs that Twitter appends for media."""
    return re.sub(r"\s*https://t\.co/\S+", "", text).strip()


def get_reply_info(tweet):
    """Extract reply screen_name and parent status ID from a tweet.

    Handles both formats:
      v2 profile endpoint: replying_to = {"screen_name": "...", "status": "..."}
      single-tweet endpoint: replying_to = "screen_name", replying_to_status = "id"
    """
    rt = tweet.get("replying_to")
    if rt is None:
        return None, None
    if isinstance(rt, dict):
        return rt.get("screen_name"), rt.get("status")
    return str(rt), tweet.get("replying_to_status")


def is_self_reply(tweet):
    """Check if a tweet is a reply to the same account (thread continuation)."""
    screen_name, _ = get_reply_info(tweet)
    if not screen_name:
        return False
    return screen_name.lower() == USERNAME.lower()


def is_reply_to_other(tweet):
    """Check if a tweet is a reply to a different user."""
    screen_name, _ = get_reply_info(tweet)
    if not screen_name:
        return False
    return screen_name.lower() != USERNAME.lower()


def fetch_single_tweet(tweet_id):
    """Fetch a single tweet by ID from the FxTwitter API."""
    url = f"https://api.fxtwitter.com/{USERNAME}/status/{tweet_id}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("tweet")
    except Exception:
        pass
    return None


def merge_threads(tweets):
    """Group self-reply threads into single posts.

    Walks each self-reply chain upward to find the thread root, fetching any
    missing intermediate tweets from the API.  Then groups all chain members
    under their root and merges text + images oldest-to-newest.
    """
    by_id = {t["id"]: t for t in tweets}
    root_cache = {}          # tweet_id -> root_id
    fetch_failures = set()   # IDs we already failed to fetch

    def resolve_root(tweet_id):
        """Walk up the reply chain to the thread root, fetching gaps."""
        if tweet_id in root_cache:
            return root_cache[tweet_id]

        path = []            # IDs visited on the way up
        current_id = tweet_id

        while True:
            if current_id in root_cache:
                root = root_cache[current_id]
                break

            current = by_id.get(current_id)
            if current is None:
                # Missing from batch — try to fetch
                if current_id in fetch_failures:
                    root = current_id
                    break
                fetched = fetch_single_tweet(current_id)
                if fetched:
                    print(f"    Fetched missing thread tweet {current_id}")
                    by_id[current_id] = fetched
                    current = fetched
                    time.sleep(1)
                else:
                    fetch_failures.add(current_id)
                    root = current_id
                    break

            if not is_self_reply(current):
                root = current_id
                break

            _, parent_id = get_reply_info(current)
            if not parent_id or parent_id in path:
                root = current_id
                break

            path.append(current_id)
            current_id = parent_id

        for tid in path:
            root_cache[tid] = root
        root_cache[tweet_id] = root
        return root

    # --- assign every usable tweet to a thread root ---
    groups = {}   # root_id -> set of tweet IDs
    for t in tweets:
        if t.get("reposted_by"):
            continue
        if is_reply_to_other(t):
            continue
        root_id = resolve_root(t["id"])
        groups.setdefault(root_id, set()).add(t["id"])

    # --- add fetched intermediaries into their groups ---
    # Build forward links so we can walk *down* from the root
    child_map = {}
    for tid, t in by_id.items():
        _, parent_id = get_reply_info(t)
        if parent_id and is_self_reply(t):
            child_map.setdefault(parent_id, []).append(tid)

    # For each group walk forward from the root to pick up fetched tweets
    expanded_groups = {}
    for root_id, members in groups.items():
        all_ids = set(members)
        queue = [root_id]
        visited = set()
        while queue:
            cid = queue.pop(0)
            if cid in visited:
                continue
            visited.add(cid)
            if cid in by_id:
                all_ids.add(cid)
            for child_id in child_map.get(cid, []):
                queue.append(child_id)
        expanded_groups[root_id] = all_ids

    # --- merge each thread into one item ---
    merged = []
    for root_id, member_ids in expanded_groups.items():
        thread_tweets = [by_id[mid] for mid in member_ids if mid in by_id]
        thread_tweets.sort(key=lambda t: t.get("created_timestamp", 0))

        chain_texts = []
        chain_images = []
        for t in thread_tweets:
            text = clean_text(t.get("text", ""))
            if text:
                chain_texts.append(text)
            chain_images.extend(collect_images(t))

        head = thread_tweets[0]
        merged.append({
            "id": head["id"],
            "created_timestamp": head.get("created_timestamp", 0),
            "tweet_id": head["id"],
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
