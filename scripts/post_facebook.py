#!/usr/bin/env python3
"""Post new articles to the Facebook page after tweet fetch."""

import os
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
STATE_FILE = REPO_ROOT / "scripts" / ".last_facebook_post"
SITE_URL = "https://milifney100.com"
GRAPH_API_VERSION = "v21.0"

PAGE_ID = os.environ.get("FACEBOOK_PAGE_ID", "")
ACCESS_TOKEN = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")


def get_last_posted_timestamp():
    """Get the filename of the last post sent to Facebook."""
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip()
    return ""


def save_last_posted_timestamp(filename):
    STATE_FILE.write_text(filename)


def parse_post(filepath):
    """Extract title, date, image, and excerpt from a post file."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Parse front matter
    meta = {}
    in_front = False
    body_start = 0
    for i, line in enumerate(lines):
        if line.strip() == "---":
            if not in_front:
                in_front = True
            else:
                body_start = i + 1
                break
        elif in_front:
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip().strip('"').strip("'")

    # Get body text (first meaningful paragraph)
    body_lines = lines[body_start:]
    body_text = " ".join(l.strip() for l in body_lines if l.strip() and not l.startswith("!["))

    return {
        "title": meta.get("title", ""),
        "date": meta.get("date", ""),
        "image": meta.get("image", ""),
        "image_alt": meta.get("image_alt", ""),
        "tweet_id": meta.get("tweet_id", ""),
        "body": body_text,
    }


def build_post_url(filepath):
    """Build the website URL for a post from its filename."""
    name = filepath.stem  # 2026-05-25-tweet_id
    parts = name.split("-", 3)
    if len(parts) >= 4:
        slug = parts[3]
        post_data = parse_post(filepath)
        date_str = post_data["date"]
        if date_str and "T" in date_str:
            date_part = date_str.split("T")[0]
            y, m, d = date_part.split("-")
            return f"{SITE_URL}/{y}/{m}/{d}/{slug}/"
        return f"{SITE_URL}/{parts[0]}/{parts[1]}/{parts[2]}/{slug}/"
    return SITE_URL


def format_message(post_data, url):
    """Format the Facebook post text (plain text, no HTML)."""
    title = post_data["title"]
    body = post_data["body"]
    return f"{title}\n\n{body}\n\nקראו עוד באתר: {url}"


def send_facebook_post(text, image_path=None, image_alt=None):
    """Post to the Facebook page. Uploads the local image file when available."""
    if not PAGE_ID or not ACCESS_TOKEN:
        print("  [SKIP] No FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN set")
        return False

    api_url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}"

    local_image = None
    if image_path and image_path.startswith("/"):
        local_path = REPO_ROOT / image_path.lstrip("/")
        if local_path.exists():
            local_image = local_path

    if local_image:
        # Upload the file directly rather than linking its site URL, since
        # GitHub Pages may not have finished rebuilding yet at this point.
        data = {
            "caption": text,
            "access_token": ACCESS_TOKEN,
        }
        if image_alt:
            data["alt_text_custom"] = image_alt
        with open(local_image, "rb") as f:
            resp = requests.post(f"{api_url}/photos", data=data, files={"source": f}, timeout=30)
    else:
        resp = requests.post(f"{api_url}/feed", data={
            "message": text,
            "access_token": ACCESS_TOKEN,
        }, timeout=30)

    if resp.status_code == 200:
        return True
    else:
        print(f"  [ERROR] Facebook API: {resp.status_code} - {resp.text}")
        return False


def main():
    print("=== Facebook poster ===")

    if not PAGE_ID or not ACCESS_TOKEN:
        print("No FACEBOOK_PAGE_ID or FACEBOOK_PAGE_ACCESS_TOKEN configured. Skipping.")
        return

    last_filename = get_last_posted_timestamp()
    print(f"Last posted file: {last_filename or '(none)'}")

    all_posts = sorted(POSTS_DIR.glob("*.md"))
    if not all_posts:
        print("No posts found.")
        return

    if last_filename:
        new_posts = [p for p in all_posts if p.name > last_filename]
    else:
        # First run: only post the latest one (don't spam 800+ posts)
        new_posts = all_posts[-1:]

    if not new_posts:
        print("No new posts to send.")
        return

    print(f"Found {len(new_posts)} new post(s) to send.")

    for filepath in new_posts:
        post_data = parse_post(filepath)
        url = build_post_url(filepath)

        print(f"  Sending: {post_data['title']}")
        msg = format_message(post_data, url)
        image = post_data.get("image", "")
        image_alt = post_data.get("image_alt", "")

        success = send_facebook_post(msg, image if image else None, image_alt if image_alt else None)
        if success:
            print(f"  ✓ Sent!")
            save_last_posted_timestamp(filepath.name)
        else:
            print(f"  ✗ Failed, stopping.")
            break


if __name__ == "__main__":
    main()
