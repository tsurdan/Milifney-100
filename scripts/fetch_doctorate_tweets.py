#!/usr/bin/env python3
"""Search all of X for tweets (any account) starting with a phrase, via FxTwitter.

Uses the public (undocumented, unauthenticated) /2/search endpoint of the
FxTwitter API - the same API this repo already relies on elsewhere. No login
or credentials needed. X's search only matches the phrase anywhere in the
tweet, so results are filtered client-side to text that actually starts with
PHRASE. Still unofficial and could break if FxTwitter changes/removes it.

Usage:
    python scripts/fetch_doctorate_tweets.py
    python scripts/fetch_doctorate_tweets.py --since 2026-09-01 --until 2026-10-01
"""

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PHRASE = "דוקטורט:"
HEADERS = {"User-Agent": "Mozilla/5.0"}
SEARCH_URL = "https://api.fxtwitter.com/2/search"
PAGE_DELAY = 1.5  # seconds between pages, be polite to FxTwitter
MAX_PAGES = 100

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = REPO_ROOT / "_data" / "doctorate_tweets.json"


def previous_month_range(today=None):
    """Return (since, until) ISO dates covering the previous full calendar month."""
    today = today or datetime.now(timezone.utc).date()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return first_of_prev_month.isoformat(), first_of_this_month.isoformat()


def search_all(since, until):
    query = f'"{PHRASE}" since:{since} until:{until}'
    cursor = None
    found = {}  # id -> record, de-duplicated across pages

    for page in range(MAX_PAGES):
        params = {"q": query, "feed": "latest", "count": "100"}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            break

        for tweet in results:
            text = tweet.get("text", "")
            if text.startswith(PHRASE):
                found[tweet["id"]] = {
                    "id": tweet["id"],
                    "url": tweet.get("url"),
                    "username": tweet.get("author", {}).get("screen_name"),
                    "text": text,
                    "created_at": tweet.get("created_at"),
                    "likes": tweet.get("likes", 0),
                    "retweets": tweet.get("reposts", 0),
                }

        print(f"  Page {page + 1}: {len(results)} results, {len(found)} matches so far")

        next_cursor = data.get("cursor", {}).get("bottom")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(PAGE_DELAY)

    return list(found.values())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="YYYY-MM-DD, defaults to the start of last month")
    parser.add_argument("--until", help="YYYY-MM-DD, defaults to the start of this month")
    args = parser.parse_args()

    default_since, default_until = previous_month_range()
    since = args.since or default_since
    until = args.until or default_until

    print(f"Searching X for tweets starting with \"{PHRASE}\" from {since} to {until}...")
    results = search_all(since, until)
    results.sort(key=lambda r: r.get("created_at") or "", reverse=True)

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} matching tweets to {OUTPUT}")

    for r in results[:20]:
        print(f"- @{r['username']} ({r['likes']} likes): {r['text'][:70]}")


if __name__ == "__main__":
    main()
