#!/usr/bin/env python3
"""Post new articles to Facebook + Instagram via a single Make.com webhook.

Used while Meta Developer App registration is blocked (phone verification
stuck). Make.com's scenario holds the actual Facebook Pages + Instagram for
Business app connections (set up via simple OAuth inside Make — no Meta
developer app needed) and does the real posting; this script just sends it
one JSON payload per new post with both platforms' image URLs and captions.

Both image URLs must be public (raw.githubusercontent.com), since Make fetches
them itself rather than accepting a file upload — generate_instagram_cards.py
commits the branded card before this script runs, so the URL is already live.
"""

import datetime
import os
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
STATE_FILE = REPO_ROOT / "scripts" / ".last_make_post"
CARDS_DIR = REPO_ROOT / "assets" / "images" / "instagram-cards"
SITE_URL = "https://milifney100.com"
RAW_CONTENT_BASE = "https://raw.githubusercontent.com/tsurdan/Milifney-100/main"

HASHTAGS = "#לפני100שנה #היסטוריה #חדשות #מהעיתונות"

WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL", "")
API_KEY = os.environ.get("MAKE_API_KEY", "")


def get_last_posted_timestamp():
    """Get the filename of the last post sent via Make."""
    if STATE_FILE.exists():
        return STATE_FILE.read_text().strip()
    return ""


def save_last_posted_timestamp(filename):
    STATE_FILE.write_text(filename)


def parse_post(filepath):
    """Extract title, date, image, image_alt, tweet_id, and body from a post file."""
    content = filepath.read_text(encoding="utf-8")
    _, front_matter, body_text = content.split("---", 2)
    meta = yaml.safe_load(front_matter) or {}

    date_val = meta.get("date", "")
    date_str = date_val.isoformat() if isinstance(date_val, (datetime.datetime, datetime.date)) else str(date_val)

    body_lines = body_text.split("\n")
    body_text = " ".join(l.strip() for l in body_lines if l.strip() and not l.startswith("!["))

    return {
        "title": str(meta.get("title", "")),
        "date": date_str,
        "image": meta.get("image", "") or "",
        "image_alt": meta.get("image_alt", "") or "",
        "tweet_id": str(meta.get("tweet_id", "")),
        "body": body_text,
    }


def build_post_url(post_data, filepath):
    """Build the website URL for a post from its filename."""
    name = filepath.stem  # 2026-05-25-tweet_id
    parts = name.split("-", 3)
    if len(parts) >= 4:
        slug = parts[3]
        date_str = post_data["date"]
        if date_str and "T" in date_str:
            y, m, d = date_str.split("T")[0].split("-")
            return f"{SITE_URL}/{y}/{m}/{d}/{slug}/"
        return f"{SITE_URL}/{parts[0]}/{parts[1]}/{parts[2]}/{slug}/"
    return SITE_URL


def clean_image_alt(post_data):
    """Strip the ';;'/'::' card-template markers before the alt text is ever
    shown/sent anywhere."""
    return post_data.get("image_alt", "").replace(";;", "").replace("::", "").strip()


def format_facebook_message(post_data, url):
    """Plain-text Facebook caption with a site link — the full image credit
    is shown on the site itself, not repeated inline here."""
    title = post_data["title"]
    body = post_data["body"]
    return f"{title}\n\n{body}\n\nקראו עוד באתר: {url}"


def format_instagram_caption(post_data):
    """Instagram caption (no clickable links — IG doesn't linkify captions).
    The full image alt text is appended at the very end."""
    title = post_data["title"]
    body = post_data["body"]
    image_alt = clean_image_alt(post_data)
    credit = f"\n\n📷 {image_alt}" if image_alt else ""
    return f"{title}\n\n{body}\n\n{HASHTAGS}{credit}"


def main():
    print("=== Facebook + Instagram poster (via Make.com) ===")

    if not WEBHOOK_URL:
        print("No MAKE_WEBHOOK_URL configured. Skipping.")
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
        # First run: only post the latest one (don't spam the backlog)
        new_posts = all_posts[-1:]

    if not new_posts:
        print("No new posts to send.")
        return

    print(f"Found {len(new_posts)} new post(s) to send.")

    for filepath in new_posts:
        post_data = parse_post(filepath)
        tweet_id = post_data.get("tweet_id", "")
        if not tweet_id:
            print(f"  [SKIP] No tweet_id: {filepath.name}")
            continue

        card_path = CARDS_DIR / f"{tweet_id}.jpg"
        if not card_path.exists():
            print(f"  [SKIP] No generated card for: {post_data['title']}")
            save_last_posted_timestamp(filepath.name)
            continue

        instagram_image_url = f"{RAW_CONTENT_BASE}/assets/images/instagram-cards/{tweet_id}.jpg"
        # Facebook uses the original tweet photo when there is one, falling
        # back to the same branded card for text-only posts.
        facebook_image_url = (
            f"{RAW_CONTENT_BASE}{post_data['image']}" if post_data["image"] else instagram_image_url
        )

        url = build_post_url(post_data, filepath)
        payload = {
            "tweet_id": tweet_id,
            "post_url": url,
            "facebook_image_url": facebook_image_url,
            "facebook_image_alt": clean_image_alt(post_data),
            "facebook_message": format_facebook_message(post_data, url),
            "instagram_image_url": instagram_image_url,
            "instagram_caption": format_instagram_caption(post_data),
        }

        print(f"  Sending: {post_data['title']}")
        headers = {"x-make-apikey": API_KEY} if API_KEY else {}
        resp = requests.post(WEBHOOK_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code in (200, 201, 202):
            print("  ✓ Sent!")
            save_last_posted_timestamp(filepath.name)
        else:
            print(f"  ✗ Failed ({resp.status_code}): {resp.text}. Stopping.")
            break


if __name__ == "__main__":
    main()
