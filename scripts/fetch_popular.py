"""Fetch most-viewed posts from GoatCounter API and write _data/popular.json."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

GOATCOUNTER_URL = "https://milifney100.goatcounter.com"
TOKEN = os.environ.get("GOATCOUNTER_TOKEN", "")
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "_data"
OUTPUT = DATA_DIR / "popular.json"
TOP_N = 10


def api_get(endpoint, params=None):
    """Make an authenticated GET request to GoatCounter API."""
    url = f"{GOATCOUNTER_URL}/api/v0/{endpoint}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"
    req = Request(url)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with urlopen(req) as resp:
        return json.loads(resp.read())


def main():
    if not TOKEN:
        print("GOATCOUNTER_TOKEN not set, skipping popular posts fetch.")
        # Write empty list so the page still renders
        DATA_DIR.mkdir(exist_ok=True)
        OUTPUT.write_text("[]", encoding="utf-8")
        return

    # Fetch stats for the last 30 days
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=30)

    try:
        data = api_get("stats/hits", {
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": "50",
        })
    except HTTPError as e:
        body = e.read().decode() if hasattr(e, 'read') else ''
        print(f"GoatCounter API error: {e.code} {e.reason}")
        print(f"Response: {body}")
        DATA_DIR.mkdir(exist_ok=True)
        OUTPUT.write_text("[]", encoding="utf-8")
        return

    # Filter only post paths (e.g. /Milifney-100/1926/05/24/...)
    posts = []
    for hit in data.get("hits", []):
        path = hit.get("path", "")
        # Post paths match /<baseurl>/YYYY/MM/DD/<id>/
        if "/Milifney-100/" in path and path.count("/") >= 5:
            # Skip search, popular, and homepage
            if "/search" in path or "/popular" in path:
                continue
            if path.rstrip("/") == "/Milifney-100":
                continue
            posts.append({
                "path": path,
                "title": hit.get("title", "").replace(" | חדשות מלפני מאה", "").strip(),
                "count": hit.get("count", 0),
            })

    # Sort by view count descending
    posts.sort(key=lambda p: p["count"], reverse=True)
    top = posts[:TOP_N]

    # Try to enrich with post metadata from _posts
    posts_dir = REPO_ROOT / "_posts"
    if posts_dir.exists():
        for item in top:
            # Extract post ID from path: /Milifney-100/YYYY/MM/DD/ID/
            parts = item["path"].rstrip("/").split("/")
            if len(parts) >= 5:
                post_id = parts[-1]
                # Find matching post file
                matches = list(posts_dir.glob(f"*-{post_id}.md"))
                if matches:
                    content = matches[0].read_text(encoding="utf-8")
                    # Parse front matter
                    if content.startswith("---"):
                        fm_end = content.index("---", 3)
                        fm = content[3:fm_end]
                        for line in fm.split("\n"):
                            if line.startswith("title:"):
                                title = line[6:].strip().strip('"').strip("'")
                                if title:
                                    item["title"] = title
                            elif line.startswith("image:"):
                                img = line[6:].strip().strip('"').strip("'")
                                if img:
                                    item["image"] = img
                            elif line.startswith("date:"):
                                date_str = line[5:].strip().strip('"').strip("'")
                                item["date"] = date_str[:10]

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(top)} popular posts to {OUTPUT}")


if __name__ == "__main__":
    main()
