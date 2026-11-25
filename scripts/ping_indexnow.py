#!/usr/bin/env python3
"""Tell Bing (and other IndexNow engines) about new pages as soon as they are live.

IndexNow: https://www.indexnow.org/documentation. The key is public by design; it is
served from /<key>.txt at the site root so the engines can verify the site.

First run (no state file): submits every indexable URL once. Later runs: submits posts
added since the last run plus the home page. Pages are only submitted once they return
200 (GitHub Pages deploys a few minutes after the push); the rest wait for the next run.

    python scripts/ping_indexnow.py
"""

import sys
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_io import DATA_DIR, REPO_ROOT, iter_posts, load_yaml  # noqa: E402

SITE_URL = "https://milifney100.com"
HOST = "milifney100.com"
KEY = "cfcfbc2649b10760072d46c39a562bb7"
KEY_LOCATION = f"{SITE_URL}/{KEY}.txt"
ENDPOINT = "https://api.indexnow.org/indexnow"
STATE_FILE = REPO_ROOT / "scripts" / ".last_indexnow_post"
MAX_URLS = 10000


def is_indexable(post):
    robots = str(post.meta.get("robots") or "")
    if post.meta.get("noindex") or "noindex" in robots:
        return False
    return bool(post.text.strip() or post.meta.get("standfirst"))


def is_live(url):
    try:
        return requests.head(url, timeout=15, allow_redirects=True).status_code == 200
    except requests.RequestException:
        return False


def generated_page_urls(folder, only_in_sitemap=True):
    urls = []
    for path in sorted((REPO_ROOT / folder).glob("*.md")):
        front = path.read_text(encoding="utf-8").split("---", 2)[1]
        meta = load_yaml_text(front)
        if meta.get("permalink") and (not only_in_sitemap or meta.get("in_sitemap")):
            urls.append(SITE_URL + meta["permalink"])
    return urls


def load_yaml_text(text):
    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError:
        return {}


def all_site_urls(posts):
    urls = [f"{SITE_URL}/", f"{SITE_URL}/about/", f"{SITE_URL}/popular/", f"{SITE_URL}/tags/"]
    urls += [f"{SITE_URL}/tags/{t['slug']}/" for t in load_yaml(DATA_DIR / "tags.yml", [])]
    urls += generated_page_urls("topics") + generated_page_urls("stories") + generated_page_urls("date-pages")
    urls += [SITE_URL + p.url for p in posts]
    return urls


def submit(urls):
    payload = {"host": HOST, "key": KEY, "keyLocation": KEY_LOCATION, "urlList": urls[:MAX_URLS]}
    resp = requests.post(ENDPOINT, json=payload, timeout=60,
                         headers={"Content-Type": "application/json; charset=utf-8"})
    print(f"IndexNow: {resp.status_code} for {len(payload['urlList'])} URLs")
    return resp.status_code in (200, 202)


def main():
    if not is_live(KEY_LOCATION):
        print("Key file is not live yet; skipping.")
        return

    posts = [p for p in iter_posts() if is_indexable(p)]
    if not posts:
        return
    last = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""

    if not last:
        if submit(all_site_urls(posts)):
            STATE_FILE.write_text(posts[-1].path.name)
        return

    new_posts = [p for p in posts if p.path.name > last]
    live = []
    for post in new_posts:
        if not is_live(SITE_URL + post.url):
            break  # keep order: later posts wait until this one is deployed
        live.append(post)
    if not live:
        print("No new live posts.")
        return
    if submit([f"{SITE_URL}/"] + [SITE_URL + p.url for p in live]):
        STATE_FILE.write_text(live[-1].path.name)


if __name__ == "__main__":
    main()
