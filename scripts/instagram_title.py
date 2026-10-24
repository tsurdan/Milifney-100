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
import re
import time
from pathlib import Path

from gemini_tagging import call_gemini

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = REPO_ROOT / "scripts" / ".instagram_card_titles.json"

BATCH_SIZE = 20
SECONDS_BETWEEN_REQUESTS = 5
MAX_BODY_CHARS = 500
MAX_TITLE_ATTEMPTS = 3

# Non-Hebrew letters that sometimes slip into generated titles (Arabic script
# or stray English words/names) — such titles are rejected in favor of the
# original tweet title.
_FOREIGN_LETTERS_RE = re.compile(r"[A-Za-z\u0600-\u06FF]")


def _has_foreign_letters(text):
    return bool(_FOREIGN_LETTERS_RE.search(text))


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

כלל קריטי - דיוק עובדתי: אסור לשנות, להמציא, להחליף או לעגל מספרים, תאריכים, שמות אנשים ומקומות לעומת מה שכתוב במפורש בתוכן הידיעה. לפני שתכתוב את הכותרת הסופית, ודא שכל מספר/תאריך/שם בה מדויק ובהקשר הנכון; אם אינך בטוח - עדיף להשמיט את הפרט מאשר לנחש.

כלל קריטי - שפה: הכותרת חייבת להיות כתובה כולה באותיות עבריות בלבד (מותרים גם ספרות וסימני פיסוק). אסור באותיות ערביות ואסור במילים או שמות באנגלית - גם שמות אנשים/מקומות/ארגונים זרים חייבים להיכתב בתעתיק עברי, לא בכתב המקור.

לפני שאתה מסיים, קרא שוב את כל כותרת שכתבת: ודא שהיא משפט תקין ומובן בעברית, ללא מילים מוזרות, שבורות או חסרות משמעות, ושהיא נשמעת כמו כותרת חדשותית אמיתית. אם משהו לא נשמע טבעי - נסח מחדש.

הידיעות:
{articles_text}

החזר אך ורק JSON תקין בפורמט הבא, בלי טקסט נוסף:
{{"1": "הכותרת החדשה", "2": "הכותרת החדשה", ...}}
כל מפתח הוא מספר הידיעה (כמחרוזת)."""


def _generate_batch_titles(batch, api_key, log, attempt=1):
    """Calls Gemini for one batch and re-asks (up to MAX_TITLE_ATTEMPTS) for any
    titles that came back empty or containing non-Hebrew letters, instead of
    silently falling back to the original title on the first bad result."""
    prompt = build_prompt(batch)

    try:
        parsed = call_gemini(prompt, api_key)
    except Exception as e:
        log(f"  Card-title batch failed: {e}. Using original titles.")
        return {post["tweet_id"]: post["title"] for post in batch}

    titles = {}
    retry_posts = []
    for i, post in enumerate(batch, start=1):
        new_title = str(parsed.get(str(i), "")).strip()
        if new_title and not _has_foreign_letters(new_title):
            titles[post["tweet_id"]] = new_title
        else:
            retry_posts.append(post)

    if retry_posts:
        if attempt < MAX_TITLE_ATTEMPTS:
            log(f"  {len(retry_posts)} title(s) had non-Hebrew letters — retrying (attempt {attempt + 1}/{MAX_TITLE_ATTEMPTS}).")
            time.sleep(SECONDS_BETWEEN_REQUESTS)
            titles.update(_generate_batch_titles(retry_posts, api_key, log, attempt + 1))
        else:
            log(f"  Giving up on {len(retry_posts)} title(s) with non-Hebrew letters after {MAX_TITLE_ATTEMPTS} attempts — using original titles.")
            for post in retry_posts:
                titles[post["tweet_id"]] = post["title"]

    return titles


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
        batch_titles = _generate_batch_titles(batch, api_key, log)

        for post in batch:
            tid = post["tweet_id"]
            result[tid] = batch_titles[tid]
            cache[tid] = batch_titles[tid]

        _save_cache(cache)
        log(f"  Generated card titles for {min(start + BATCH_SIZE, len(pending))}/{len(pending)} posts.")
        time.sleep(SECONDS_BETWEEN_REQUESTS)

    return result
