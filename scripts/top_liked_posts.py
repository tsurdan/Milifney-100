#!/usr/bin/env python3
"""Find the 100 most-liked posts of @Milifney100 of all time (via FxTwitter API).

The profile timeline endpoint only returns the ~100 most recent tweets (no
deep pagination), so instead this queries the single-tweet endpoint for every
tweet_id found in local _posts/*.md front matter, sorts by like count, and
writes the top 100 to _data/top_liked.json.
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

USERNAME = "Milifney100"
HEADERS = {"User-Agent": "Mozilla/5.0"}
WORKERS = 8
TOP_N = 100

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
OUTPUT = REPO_ROOT / "_data" / "top_liked.json"


def fetch_tweet_stats(tweet_id):
    """Return (tweet_id, likes, retweets, views) or None on failure."""
    url = f"https://api.fxtwitter.com/{USERNAME}/status/{tweet_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        if resp.status_code != 200:
            return None
        tweet = resp.json().get("tweet")
        if not tweet:
            return None
        return tweet_id, tweet.get("likes", 0), tweet.get("retweets", 0), tweet.get("views", 0)
    except Exception:
        return None


def build_post_index():
    """Map tweet_id -> {title, path} for every local post, from front matter."""
    index = {}
    for md_file in POSTS_DIR.glob("*.md"):
        content = md_file.read_text(encoding="utf-8")
        if not content.startswith("---"):
            continue
        fm_end = content.index("---", 3)
        fm = content[3:fm_end]
        tweet_id = None
        title = None
        date_str = None
        for line in fm.split("\n"):
            line = line.strip()
            if line.startswith("tweet_id:"):
                tweet_id = line.split(":", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("title:"):
                title = line.split(":", 1)[1].strip().strip('"').strip("'")
            elif line.startswith("date:"):
                date_str = line.split(":", 1)[1].strip().strip('"').strip("'")[:10]
        if tweet_id:
            d = date_str.split("-") if date_str else []
            path = f"/{d[0]}/{d[1]}/{d[2]}/{tweet_id}/" if len(d) == 3 else None
            index[tweet_id] = {"title": title, "path": path, "date": date_str}
    return index


def main():
    post_index = build_post_index()
    tweet_ids = list(post_index.keys())
    print(f"Found {len(tweet_ids)} local posts. Fetching like counts...")

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {executor.submit(fetch_tweet_stats, tid): tid for tid in tweet_ids}
        for future in as_completed(futures):
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(tweet_ids)} fetched...")
            outcome = future.result()
            if outcome:
                results.append(outcome)

    print(f"Successfully fetched {len(results)}/{len(tweet_ids)} tweets.")

    enriched = []
    for tid, likes, retweets, views in results:
        info = post_index[tid]
        enriched.append({
            "tweet_id": tid,
            "likes": likes,
            "retweets": retweets,
            "views": views,
            "title": info.get("title"),
            "path": info.get("path"),
            "date": info.get("date"),
        })

    enriched.sort(key=lambda p: p["likes"], reverse=True)
    top = enriched[:TOP_N]

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote top {len(top)} most-liked posts to {OUTPUT}")

    print("\nTop 20:")
    for i, p in enumerate(top[:20], 1):
        print(f"{i}. ({p['likes']} likes) {p['title']}")


if __name__ == "__main__":
    main()
