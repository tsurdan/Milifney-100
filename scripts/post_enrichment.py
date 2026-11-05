#!/usr/bin/env python3
"""Gemini-based enrichment: a modern-Hebrew standfirst, an optional SEO title,
named entities (people, places, events, organizations) and story membership.

Posts keep their newspaper-style headline; this adds the layer a modern news
site has under the headline, using today's spellings and names so readers and
search engines can match the story.

Posts without a `standfirst` front-matter field are "pending". Best effort: a
failed batch is logged and retried on the next run.
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_io import DATA_DIR, dump_yaml, iter_posts, load_yaml, update_front_matter  # noqa: E402

MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

BATCH_SIZE = 10
SECONDS_BETWEEN_REQUESTS = 5
MAX_BODY_CHARS = 700
MAX_REGISTRY_IN_PROMPT = 400
MAX_ENTITIES_PER_POST = 5

ENTITIES_FILE = DATA_DIR / "entities.yml"
STORIES_FILE = DATA_DIR / "stories.yml"
ENTITY_TYPES = {"person", "place", "event", "org"}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def load_registry():
    return load_yaml(ENTITIES_FILE, {})


def load_stories():
    return load_yaml(STORIES_FILE, {})


def pending_posts():
    return [p for p in iter_posts() if "standfirst" not in p.meta]


def _excerpt(post):
    text = re.sub(r"https?://\S+", "", post.text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_BODY_CHARS]


def build_prompt(batch, registry, stories):
    top = sorted(registry.items(), key=lambda kv: -int(kv[1].get("count", 0) or 0))[:MAX_REGISTRY_IN_PROMPT]
    registry_lines = "\n".join(f"- {slug}: {info.get('name', '')} ({info.get('type', '')})" for slug, info in top) or "(ריק)"
    story_lines = "\n".join(
        f"- {slug}: {info.get('name', '')}" for slug, info in stories.items()
    ) or "(אין)"
    items = []
    for i, post in enumerate(batch, start=1):
        caption = re.sub(r"\s+", " ", str(post.meta.get("image_alt", "") or "")).replace(";;", "").replace("::", "")
        items.append(
            f"{i}. כותרת: {post.title}\n   תאריך בעיתון: {post.date_str}\n"
            f"   תוכן: {_excerpt(post) or '(אין טקסט, רק תמונה)'}\n   כיתוב התמונה: {caption[:200] or '(אין)'}"
        )
    articles = "\n".join(items)

    return f"""אתה עורך באתר חדשות עברי שמפרסם ידיעות אמיתיות מהעיתונות העברית של לפני מאה שנה, כאילו קרו היום.
עבור כל ידיעה מהרשימה כתוב את השדות הבאים, בעברית מודרנית ותקנית (כתיב ושמות של ימינו, למשל "ווינה" ולא "וינא", "איראן" ולא "פרס", "רזא שאה" ולא "ריזא ח'אן").

1. standfirst: משפט או שניים (עד 170 תווים) שמסכמים את הידיעה כמו כותרת משנה באתר חדשות מודרני: מי, מה, איפה, ואם הטקסט מציין זמן - בניסוח מוחלט ("ב-31 באוקטובר 1926") ולא יחסי ("אמש"). אל תוסיף עובדות שאינן בכותרת, בטקסט או בכיתוב. בלי אימוג'ים ובלי האשטאגים. אם אין טקסט, הסתמך רק על הכותרת והכיתוב.
2. seo_title: רק אם הכותרת לא מציינת מי או מה הנושא (למשל כותרת כללית או כותרת שמסתיימת בנקודתיים), כתוב כותרת חיפוש תיאורית של 35 עד 65 תווים. אחרת החזר null.
3. entities: עד {MAX_ENTITIES_PER_POST} אישים, מקומות, אירועים או ארגונים מרכזיים בידיעה, כל אחד כאובייקט {{"name": שם בעברית מודרנית, "slug": מזהה באנגלית באותיות קטנות עם מקפים, "type": person|place|event|org}}.
   - העדף מזהים קיימים מהרשימה למטה כשמדובר באותה ישות. אל תיצור מזהה חדש לישות שכבר קיימת.
   - אירועים ופרשות בשם מלא וברור (למשל "משפט רודולף האס"), אישים בשם המלא, מקומות בשם המוכר היום.
   - בלי מושגים כלליים (ממשלה, עיתון, עם) ובלי ישויות שהן רק רקע שולי.
4. stories: רשימת מזהים מתוך רשימת הסיפורים הקבועה למטה שהידיעה משתייכת אליהם בבירור, או רשימה ריקה. אסור להמציא מזהים.

ישויות קיימות (מזהה: שם):
{registry_lines}

סיפורים קבועים (מזהה: שם):
{story_lines}

הידיעות:
{articles}

החזר אך ורק JSON תקין בפורמט:
{{"1": {{"standfirst": "...", "seo_title": null, "entities": [{{"name": "...", "slug": "...", "type": "person"}}], "stories": []}}, "2": {{...}}}}"""


def call_gemini(prompt, api_key):
    url = API_URL_TEMPLATE.format(model=MODEL)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }
    resp = requests.post(url, params={"key": api_key}, json=body, timeout=120)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def _clean_sentence(value):
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s+", " ", value).strip().strip("-–— ")
    return value


def normalize_entities(raw_entities, registry):
    """Validate model output, reuse existing slugs, and add new ones to the registry."""
    name_to_slug = {info.get("name"): slug for slug, info in registry.items()}
    slugs = []
    if not isinstance(raw_entities, list):
        return slugs
    for item in raw_entities:
        if not isinstance(item, dict):
            continue
        name = _clean_sentence(item.get("name"))
        slug = str(item.get("slug", "")).strip().lower()
        etype = str(item.get("type", "")).strip().lower()
        if not name or etype not in ENTITY_TYPES:
            continue
        if name in name_to_slug:
            slug = name_to_slug[name]
        elif slug in registry:
            pass  # trust the existing registry entry for this slug
        elif SLUG_RE.match(slug):
            registry[slug] = {"name": name, "type": etype, "count": 0, "page": False}
            name_to_slug[name] = slug
        else:
            continue
        if slug not in slugs:
            slugs.append(slug)
        if len(slugs) >= MAX_ENTITIES_PER_POST:
            break
    return slugs


def build_updates(entry, registry, valid_stories):
    standfirst = _clean_sentence(entry.get("standfirst")) if isinstance(entry, dict) else ""
    if len(standfirst) < 15:
        return None
    updates = {"standfirst": standfirst[:240]}
    seo_title = _clean_sentence(entry.get("seo_title"))
    if 20 <= len(seo_title) <= 90:
        updates["seo_title"] = seo_title
    updates["entities"] = normalize_entities(entry.get("entities"), registry)
    stories = entry.get("stories") if isinstance(entry.get("stories"), list) else []
    updates["stories"] = [s for s in dict.fromkeys(str(x) for x in stories) if s in valid_stories]
    return updates


def enrich_pending_posts(api_key, log=print, limit=None, dry_run=False):
    """Enrich every post missing a standfirst. Returns the number of posts written."""
    if not api_key:
        log("GEMINI_API_KEY not set - skipping enrichment.")
        return 0

    pending = pending_posts()
    if limit:
        pending = pending[:limit]
    if not pending:
        return 0
    log(f"{len(pending)} post(s) need a standfirst.")

    registry = load_registry()
    stories = load_stories()
    written = 0
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        try:
            result = call_gemini(build_prompt(batch, registry, stories), api_key)
        except Exception as exc:  # noqa: BLE001 - best effort, retried next run
            log(f"  Enrichment batch at {start} failed: {exc}. Will retry next run.")
            time.sleep(SECONDS_BETWEEN_REQUESTS)
            continue

        for i, post in enumerate(batch, start=1):
            updates = build_updates(result.get(str(i)), registry, stories)
            if not updates:
                log(f"  No usable result for {post.path.name}")
                continue
            if dry_run:
                log(f"  [dry-run] {post.path.name}: {json.dumps(updates, ensure_ascii=False)}")
            elif update_front_matter(post.path, updates):
                written += 1
        if not dry_run:
            dump_yaml(ENTITIES_FILE, registry)
        log(f"  Enriched {min(start + BATCH_SIZE, len(pending))}/{len(pending)} posts.")
        time.sleep(SECONDS_BETWEEN_REQUESTS)

    return written
