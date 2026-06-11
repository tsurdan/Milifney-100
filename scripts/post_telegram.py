#!/usr/bin/env python3
"""Post new articles to Telegram channel after tweet fetch."""

import os
import json
import requests
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
STATE_FILE = REPO_ROOT / "scripts" / ".last_telegram_post"
SITE_URL = "https://tsurdan.github.io/Milifney-100"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def get_last_posted_timestamp():
    """Get the timestamp of the last post sent to Telegram."""
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip()
    return ""


def save_last_posted_timestamp(ts):
    """Save the latest posted timestamp."""
    STATE_FILE.write_text(ts)


def get_new_posts(since_filename):
    """Get posts newer than the last posted one, sorted by date."""
    posts = []
    for f in sorted(POSTS_DIR.glob("*.md")):
        if f.name > since_filename:
            posts.append(f)
    return posts


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
    # Truncate
    if len(body_text) > 300:
        body_text = body_text[:297] + "..."

    return {
        "title": meta.get("title", ""),
        "date": meta.get("date", ""),
        "image": meta.get("image", ""),
        "tweet_id": meta.get("tweet_id", ""),
        "body": body_text,
    }


def build_post_url(filepath):
    """Build the website URL for a post from its filename."""
    # Filename: 2026-05-25-tweet_id.md
    name = filepath.stem  # 2026-05-25-tweet_id
    parts = name.split("-", 3)  # ['2026', '05', '25', 'tweet_id']
    if len(parts) >= 4:
        year, month, day, slug = parts[0], parts[1], parts[2], parts[3]
        # Posts use 1926 dates in URLs (100 years back)
        # Read actual date from front matter
        post_data = parse_post(filepath)
        date_str = post_data["date"]
        if date_str and "T" in date_str:
            date_part = date_str.split("T")[0]
            y, m, d = date_part.split("-")
            return f"{SITE_URL}/{y}/{m}/{d}/{slug}/"
        return f"{SITE_URL}/{year}/{month}/{day}/{slug}/"
    return SITE_URL


def send_telegram_message(text, image_url=None):
    """Send a message to the Telegram channel."""
    if not BOT_TOKEN or not CHAT_ID:
        print("  [SKIP] No TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID set")
        return False

    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}"

    # Try to send with local image file
    local_image = None
    if image_url and image_url.startswith("/"):
        local_path = REPO_ROOT / image_url.lstrip("/")
        if local_path.exists():
            local_image = local_path

    if local_image:
        # Upload image file directly
        with open(local_image, "rb") as f:
            resp = requests.post(f"{api_url}/sendPhoto", data={
                "chat_id": CHAT_ID,
                "caption": text,
                "parse_mode": "HTML",
            }, files={"photo": f}, timeout=30)
    else:
        # Send text only
        resp = requests.post(f"{api_url}/sendMessage", data={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }, timeout=30)

    if resp.status_code == 200:
        return True
    else:
        print(f"  [ERROR] Telegram API: {resp.status_code} - {resp.text}")
        return False


def format_message(post_data, url):
    """Format the Telegram message."""
    title = post_data["title"]
    body = post_data["body"]

    msg = f"<b>{title}</b>\n\n{body}"

    return msg


def main():
    print("=== Telegram poster ===")

    if not BOT_TOKEN or not CHAT_ID:
        print("No TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID configured. Skipping.")
        return

    last_filename = get_last_posted_timestamp()
    print(f"Last posted file: {last_filename or '(none)'}")

    # Get all post files sorted
    all_posts = sorted(POSTS_DIR.glob("*.md"))
    if not all_posts:
        print("No posts found.")
        return

    # Filter new posts
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

        success = send_telegram_message(msg, image if image else None)
        if success:
            print(f"  ✓ Sent!")
            save_last_posted_timestamp(filepath.name)
        else:
            print(f"  ✗ Failed, stopping.")
            break


if __name__ == "__main__":
    main()
