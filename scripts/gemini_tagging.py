#!/usr/bin/env python3
"""Shared Gemini-based auto-tagging logic.

Used by both scripts/tag_posts.py (one-time local backfill CLI) and
scripts/fetch_tweets.py (tags each newly fetched post as part of the regular
cron run). Posts are matched against the fixed vocabulary in _data/tags.yml;
the model is never allowed to invent new tags.
"""

import json
import os
import re
import time
from pathlib import Path

import requests
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
TAGS_FILE = REPO_ROOT / "_data" / "tags.yml"

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

BATCH_SIZE = 25          # posts per request — keeps token usage far under the 250K TPM limit
SECONDS_BETWEEN_REQUESTS = 5   # 15 rpm limit == 1 request per 4s; 5s adds margin
MAX_BODY_CHARS = 600     # per-post excerpt sent to the model, to control tokens

FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def load_tag_vocabulary():
    tags = yaml.safe_load(TAGS_FILE.read_text(encoding="utf-8"))
    return {t["slug"]: t["name"] for t in tags}


def load_pending_posts():
    """Return one entry per post whose front matter has no `tags:` line yet."""
    pending = []
    for path in sorted(POSTS_DIR.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        match = FRONT_MATTER_RE.match(raw)
        if not match:
            continue
        fm_text, body_text = match.groups()
        already_tagged = any(line.strip().startswith("tags:") for line in fm_text.splitlines())
        if already_tagged:
            continue
        title_match = re.search(r'^title:\s*"?(.*?)"?\s*$', fm_text, re.MULTILINE)
        title = title_match.group(1) if title_match else ""
        excerpt = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body_text)  # drop markdown images
        excerpt = re.sub(r"https?://\S+", "", excerpt).strip()
        excerpt = excerpt[:MAX_BODY_CHARS]
        pending.append({"path": path, "fm_text": fm_text, "title": title, "excerpt": excerpt})
    return pending


def build_prompt(vocab, batch):
    vocab_lines = "\n".join(f"- {slug}: {name}" for slug, name in vocab.items())
    articles_lines = []
    for i, post in enumerate(batch, start=1):
        articles_lines.append(f"{i}. כותרת: {post['title']}\n   תוכן: {post['excerpt']}")
    articles_text = "\n".join(articles_lines)

    return f"""אתה עוזר לתייג ידיעות חדשותיות היסטוריות (מלפני כמאה שנה) באתר עברי.
עבור כל ידיעה מהרשימה למטה, בחר 1 עד 3 תגיות מתוך רשימת התגיות הקבועה בלבד (אסור להמציא תגיות חדשות).
השתמש רק ב-slug (המילה האנגלית לפני הנקודתיים) של התגיות שבחרת.

רשימת התגיות המותרות (slug: שם):
{vocab_lines}

הידיעות:
{articles_text}

החזר אך ורק JSON תקין בפורמט הבא, בלי טקסט נוסף:
{{"1": ["slug-a", "slug-b"], "2": ["slug-c"], ...}}
כל מפתח הוא מספר הידיעה (כמחרוזת), והערך הוא רשימת ה-slug-ים שנבחרו לה."""


def call_gemini(prompt, api_key):
    url = API_URL_TEMPLATE.format(model=MODEL)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    resp = requests.post(url, params={"key": api_key}, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def write_tags(post, tags):
    new_fm = post["fm_text"].rstrip("\n") + "\ntags: [" + ", ".join(tags) + "]"
    raw = post["path"].read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(raw)
    body_text = match.group(2)
    post["path"].write_text(f"---\n{new_fm}\n---\n{body_text}", encoding="utf-8")


def tag_pending_posts(api_key, log=print):
    """Tag every post currently missing a `tags:` front-matter line.

    Best-effort: a failed batch (rate limit, network error, bad JSON, ...) is
    logged and left untagged rather than raising, since `load_pending_posts`
    will simply pick it up again on the next run. Returns the number of posts
    successfully tagged.
    """
    if not api_key:
        log("GEMINI_API_KEY not set — skipping auto-tagging.")
        return 0

    vocab = load_tag_vocabulary()
    pending = load_pending_posts()
    if not pending:
        return 0
    log(f"{len(pending)} post(s) need tags.")

    tagged_count = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        prompt = build_prompt(vocab, batch)

        try:
            result = call_gemini(prompt, api_key)
        except Exception as e:
            log(f"  Tagging batch starting at post {start} failed: {e}. Will retry next run.")
            time.sleep(SECONDS_BETWEEN_REQUESTS)
            continue

        for i, post in enumerate(batch, start=1):
            tags = result.get(str(i), [])
            tags = [t for t in dict.fromkeys(tags) if t in vocab][:3]  # valid, deduped, max 3
            if not tags:
                log(f"  No matching tag found for: {post['title']}")
            write_tags(post, tags)
            tagged_count += 1

        log(f"  Tagged {min(start + BATCH_SIZE, len(pending))}/{len(pending)} posts.")
        time.sleep(SECONDS_BETWEEN_REQUESTS)

    return tagged_count
