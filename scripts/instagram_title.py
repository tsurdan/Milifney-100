#!/usr/bin/env python3
"""Shared Gemini-based Instagram card headline generation.

Post titles come from the original tweet and are sometimes too short/vague to
stand alone on an Instagram card (which has no article body shown under it).
This asks Gemini for a short but informative replacement headline, used only
for the card image — the website, Facebook caption, and Instagram caption all
keep using the original post title untouched.

Results are cached on disk per tweet_id (scripts/.instagram_card_titles.json)
so each post is only sent to Gemini once, even across repeated card
regenerations/previews.
"""

import json
import os
import time
from pathlib import Path

from gemini_tagging import call_gemini

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = REPO_ROOT / "scripts" / ".instagram_card_titles.json"

BATCH_SIZE = 20
SECONDS_BETWEEN_REQUESTS = 5
MAX_BODY_CHARS = 500


def _load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def build_prompt(batch):
    articles_lines = []
    for i, post in enumerate(batch, start=1):
        excerpt = post["body"][:MAX_BODY_CHARS]
        articles_lines.append(f"{i}. כותרת נוכחית: {post['title']}\n   תוכן הידיעה: {excerpt}")
    articles_text = "\n".join(articles_lines)

    return f"""אתה עורך חדשות שכותב כותרות לכרטיסי תמונה באינסטגרם, עבור ידיעות היסטוריות מלפני כמאה שנה.
הכותרות הנוכחיות הגיעו מציוץ בטוויטר/X ולעיתים קצרות או לא מספיק אינפורמטיביות לכרטיס שעומד בפני עצמו (בלי טקסט נוסף מתחתיו).
עבור כל ידיעה, כתוב כותרת חדשה לכרטיס: קצרה אך אינפורמטיבית ככל האפשר, שמעבירה את עיקר המידע (מי/מה/איפה) מתוך תוכן הידיעה, בעברית תקינה וללא קליקבייט, עד כ-90 תווים.
אם הכותרת הנוכחית כבר טובה ומספקת מידע מספיק, אפשר להחזיר אותה כמעט ללא שינוי.

הידיעות:
{articles_text}

החזר אך ורק JSON תקין בפורמט הבא, בלי טקסט נוסף:
{{"1": "הכותרת החדשה", "2": "הכותרת החדשה", ...}}
כל מפתח הוא מספר הידיעה (כמחרוזת)."""


def generate_card_titles(posts, api_key, log=print):
    """posts: list of dicts with 'tweet_id', 'title', 'body'.
    Returns a dict tweet_id -> card headline (falls back to the original
    title if no API key is set or a batch call fails)."""
    cache = _load_cache()
    result = {}
    pending = []
    for post in posts:
        tid = post["tweet_id"]
        if tid in cache:
            result[tid] = cache[tid]
        else:
            pending.append(post)

    if not pending:
        return result

    if not api_key:
        log("GEMINI_API_KEY not set — using original titles for Instagram cards.")
        for post in pending:
            result[post["tweet_id"]] = post["title"]
        return result

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        prompt = build_prompt(batch)

        try:
            parsed = call_gemini(prompt, api_key)
        except Exception as e:
            log(f"  Card-title batch starting at post {start} failed: {e}. Using original titles.")
            for post in batch:
                result[post["tweet_id"]] = post["title"]
            continue

        for i, post in enumerate(batch, start=1):
            new_title = str(parsed.get(str(i), "")).strip()
            result[post["tweet_id"]] = new_title or post["title"]
            cache[post["tweet_id"]] = result[post["tweet_id"]]

        _save_cache(cache)
        log(f"  Generated card titles for {min(start + BATCH_SIZE, len(pending))}/{len(pending)} posts.")
        time.sleep(SECONDS_BETWEEN_REQUESTS)

    return result
