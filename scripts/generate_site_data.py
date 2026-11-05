#!/usr/bin/env python3
"""Generate the data files and hub pages the site is built from.

Idempotent and safe to run every workflow cycle: files are only rewritten when
their content changes. Steps:

 1. Fill missing per-post fields (sources, image size) and apply story keyword rules.
 2. Data files in _data/: entities.yml (counts), entity_index.yml, story_pages.yml,
    archive_months.yml, tag_hubs.yml, related.json.
 3. Generated pages: date-pages/ (/1926/, /1926/09/, /1926/09/06/), topics/<entity>/,
    stories/<story>/. Stale generated pages (marked `generated: true`) are removed.

    python scripts/generate_site_data.py
"""

import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image_meta import size_updates  # noqa: E402
from post_io import (  # noqa: E402
    DATA_DIR, REPO_ROOT, dump_yaml, format_value, iter_posts, load_yaml, update_front_matter, write_text,
)
from source_links import source_updates  # noqa: E402

MIN_ENTITY_POSTS = 3
MIN_STORY_POSTS = 3
MIN_INDEXABLE_DAY_POSTS = 3
RELATED_COUNT = 6

HE_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]

DATE_PAGES_DIR = REPO_ROOT / "date-pages"
TOPICS_DIR = REPO_ROOT / "topics"
STORIES_DIR = REPO_ROOT / "stories"
GENERATED_MARK = "generated: true"


# ---------------------------------------------------------------- helpers

def clean_title(title):
    return re.sub(r"\s+", " ", (title or "").strip()).rstrip(":").strip()


def display_date(date_str):
    y, m, d = date_str.split("-")
    return f"{d}.{m}.{y}"


def month_name(date_str):
    return HE_MONTHS[int(date_str.split("-")[1]) - 1]


def day_label(date_str):
    y, m, d = date_str.split("-")
    return f"{int(d)} ב{HE_MONTHS[int(m) - 1]} {y}"


def truncate(text, limit):
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def strip_markdown(text):
    text = re.sub(r"[*_`>#]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def render_page(front, body=""):
    lines = ["---"] + [f"{key}: {format_value(value)}" for key, value in front.items()] + ["---", body]
    return "\n".join(lines).rstrip("\n") + "\n"


def write_if_changed(path, text):
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text)
    return True


def remove_stale(directory, keep):
    """Delete generated stubs in directory that are not in `keep`."""
    removed = 0
    if not directory.exists():
        return removed
    for path in directory.glob("*.md"):
        if path.name in keep:
            continue
        if GENERATED_MARK in path.read_text(encoding="utf-8")[:600]:
            path.unlink()
            removed += 1
    return removed


# ---------------------------------------------------------------- step 1

def ensure_post_fields(posts):
    changed = 0
    for post in posts:
        updates = {}
        if "sources" not in post.meta:
            updates.update(source_updates(post.meta.get("image_alt", ""), post.date_str))
        image = post.meta.get("image")
        if image and "image_w" not in post.meta:
            updates.update(size_updates(image))
        if updates and update_front_matter(post.path, updates):
            changed += 1
    return changed


def apply_story_rules(posts, stories):
    changed = set()
    current = {p.path: p.get_list("stories") for p in posts}
    for slug, info in stories.items():
        keywords = [k for k in info.get("keywords", []) if k]
        excludes = [k for k in info.get("exclude", []) if k]
        if not keywords:
            continue
        matched = []
        for post in posts:
            haystack = f"{post.title} {post.text}"
            if any(k in haystack for k in keywords) and not any(x in haystack for x in excludes):
                matched.append(post)
        existing = {path for path, slugs in current.items() if slug in slugs}
        if len({p.path for p in matched} | existing) < MIN_STORY_POSTS:
            continue
        for post in matched:
            if post.path in existing:
                continue
            current[post.path] = current[post.path] + [slug]
            if update_front_matter(post.path, {"stories": current[post.path]}):
                changed.add(post.path)
    return len(changed)


# ---------------------------------------------------------------- step 2

def build_entities(posts, registry):
    counts = Counter()
    first_last = {}
    for post in posts:
        for slug in post.get_list("entities"):
            counts[slug] += 1
            first_last.setdefault(slug, [post.date_str, post.date_str])
            span = first_last[slug]
            span[0] = min(span[0], post.date_str)
            span[1] = max(span[1], post.date_str)
    result = {}
    for slug, count in counts.items():
        info = registry.get(slug)
        if not info:
            continue
        result[slug] = {
            "name": info.get("name", slug),
            "type": info.get("type", "event"),
            "count": count,
            "page": count >= MIN_ENTITY_POSTS,
        }
    ordered = dict(sorted(result.items(), key=lambda kv: (-kv[1]["count"], kv[1]["name"])))
    return ordered, first_last


def build_related(posts, entities):
    total = len(posts)
    features = []
    index = defaultdict(list)
    df = Counter()
    for i, post in enumerate(posts):
        feats = [("e", s) for s in post.get_list("entities") if s in entities]
        feats += [("s", s) for s in post.get_list("stories")]
        feats += [("t", s) for s in post.tags()]
        features.append(feats)
        for feat in set(feats):
            index[feat].append(i)
            df[feat] += 1

    weights = {"e": 3.0, "s": 6.0, "t": 0.6}
    ordinals = [date.fromisoformat(p.date_str).toordinal() for p in posts]
    related = {}
    for i, post in enumerate(posts):
        scores = defaultdict(float)
        for feat in set(features[i]):
            weight = weights[feat[0]] * math.log(1 + total / df[feat])
            for j in index[feat]:
                if j != i:
                    scores[j] += weight
        if not scores:
            continue
        ranked = sorted(scores, key=lambda j: (-round(scores[j], 3), abs(ordinals[j] - ordinals[i]), -ordinals[j]))
        items = []
        for j in ranked[:RELATED_COUNT]:
            other = posts[j]
            items.append({
                "u": other.url,
                "t": clean_title(other.title) or other.slug,
                "i": other.meta.get("image") or "",
                "d": display_date(other.date_str),
            })
        related[post.tweet_id] = items
    return related


def build_tag_hubs(posts, tags, entities, story_pages):
    hubs = {}
    for tag in tags:
        slug = tag["slug"]
        tagged = [p for p in posts if slug in p.tags()]
        ent_counts = Counter(e for p in tagged for e in p.get_list("entities") if entities.get(e, {}).get("page"))
        story_counts = Counter(s for p in tagged for s in p.get_list("stories") if s in story_pages)
        hubs[slug] = {
            "entities": [e for e, _ in ent_counts.most_common(12)],
            "stories": [s for s, c in story_counts.most_common(6) if c >= 2],
        }
    return hubs


# ---------------------------------------------------------------- step 3

def generate_date_pages(posts, tag_names):
    by_day = defaultdict(list)
    for post in posts:
        by_day[post.date_str].append(post)
    days = sorted(by_day)
    by_month = defaultdict(list)
    for post in posts:
        by_month[post.date_str[:7]].append(post)
    years = sorted({d[:4] for d in days})
    keep = set()
    written = 0

    for idx, key in enumerate(days):
        group = sorted(by_day[key], key=lambda p: p.time_str)
        y, m, d = key.split("-")
        label = day_label(key)
        titles = [clean_title(p.title) for p in group if clean_title(p.title)]
        listing = "; ".join(titles[:3])
        ending = "" if listing[-1:] in (".", "!", "?") else "."
        summary = truncate(f"ב-{label} דווח בעיתונות העברית, בין היתר: {listing}{ending}", 200)
        front = {
            "layout": "archive",
            "generated": True,
            "archive_level": "day",
            "archive_key": key,
            "archive_year": y,
            "archive_month": m,
            "month_name": HE_MONTHS[int(m) - 1],
            "day_num": int(d),
            "title": f"הידיעות של {label}",
            "description": truncate(summary, 155),
            "summary": summary,
            "post_count": len(group),
            "permalink": f"/{y}/{m}/{d}/",
        }
        if idx > 0:
            prev_key = days[idx - 1]
            front["prev_url"] = "/" + prev_key.replace("-", "/") + "/"
            front["prev_label"] = day_label(prev_key)
        if idx + 1 < len(days):
            next_key = days[idx + 1]
            front["next_url"] = "/" + next_key.replace("-", "/") + "/"
            front["next_label"] = day_label(next_key)
        if len(group) >= MIN_INDEXABLE_DAY_POSTS:
            front["in_sitemap"] = True
        else:
            front["robots"] = "noindex, follow"
        name = f"{key}.md"
        keep.add(name)
        written += write_if_changed(DATE_PAGES_DIR / name, render_page(front))

    month_keys = sorted(by_month)
    archive_months = []
    for idx, key in enumerate(month_keys):
        group = by_month[key]
        y, m = key.split("-")
        name_he = HE_MONTHS[int(m) - 1]
        day_count = len({p.date_str for p in group})
        top_tags = [tag_names[t] for t, _ in Counter(t for p in group for t in p.tags() if t in tag_names).most_common(3)]
        summary = f"{len(group)} ידיעות מהעיתונות העברית של {name_he} {y}, ב-{day_count} ימים."
        if top_tags:
            summary += " נושאים מרכזיים: " + ", ".join(top_tags) + "."
        front = {
            "layout": "archive",
            "generated": True,
            "archive_level": "month",
            "archive_key": key,
            "archive_year": y,
            "archive_month": m,
            "month_name": name_he,
            "title": f"{name_he} {y}: כל הידיעות",
            "description": truncate(summary, 155),
            "summary": summary,
            "post_count": len(group),
            "permalink": f"/{y}/{m}/",
            "in_sitemap": True,
        }
        if idx > 0:
            prev_key = month_keys[idx - 1]
            front["prev_url"] = "/" + prev_key.replace("-", "/") + "/"
            front["prev_label"] = f"{HE_MONTHS[int(prev_key[5:]) - 1]} {prev_key[:4]}"
        if idx + 1 < len(month_keys):
            next_key = month_keys[idx + 1]
            front["next_url"] = "/" + next_key.replace("-", "/") + "/"
            front["next_label"] = f"{HE_MONTHS[int(next_key[5:]) - 1]} {next_key[:4]}"
        name = f"{key}.md"
        keep.add(name)
        written += write_if_changed(DATE_PAGES_DIR / name, render_page(front))
        archive_months.append({"key": key, "year": y, "month": m, "name": name_he, "count": len(group)})

    for y in years:
        months = [k for k in month_keys if k.startswith(y)]
        total = sum(len(by_month[k]) for k in months)
        summary = f"{total} ידיעות מהעיתונות העברית של {y}, מ{HE_MONTHS[int(months[0][5:]) - 1]} עד {HE_MONTHS[int(months[-1][5:]) - 1]}."
        front = {
            "layout": "archive",
            "generated": True,
            "archive_level": "year",
            "archive_key": y,
            "archive_year": y,
            "title": f"{y}: ידיעות מהעיתונות העברית",
            "description": truncate(summary, 155),
            "summary": summary,
            "post_count": total,
            "permalink": f"/{y}/",
            "in_sitemap": True,
        }
        name = f"{y}.md"
        keep.add(name)
        written += write_if_changed(DATE_PAGES_DIR / name, render_page(front))

    removed = remove_stale(DATE_PAGES_DIR, keep)
    return archive_months, written, removed


def generate_entity_pages(posts, entities, first_last):
    keep = set()
    written = 0
    for slug, info in entities.items():
        if not info["page"]:
            continue
        first, last = first_last[slug]
        description = f"{info['count']} ידיעות על {info['name']} מהעיתונות העברית של לפני מאה שנה, מ-{display_date(first)} עד {display_date(last)}."
        front = {
            "layout": "hub",
            "generated": True,
            "hub_kind": "entity",
            "hub_slug": slug,
            "title": info["name"],
            "seo_title": f"{info['name']}: כל הידיעות מהעיתונות של לפני מאה שנה",
            "description": truncate(description, 155),
            "permalink": f"/topics/{slug}/",
            "in_sitemap": True,
        }
        name = f"{slug}.md"
        keep.add(name)
        written += write_if_changed(TOPICS_DIR / name, render_page(front))
    removed = remove_stale(TOPICS_DIR, keep)
    return written, removed


def generate_story_pages(posts, stories):
    counts = Counter(s for p in posts for s in p.get_list("stories"))
    story_pages = {}
    keep = set()
    written = 0
    for slug, info in stories.items():
        if counts.get(slug, 0) < MIN_STORY_POSTS:
            continue
        story_pages[slug] = counts[slug]
        intro = strip_markdown(info.get("intro", ""))
        first_sentence = re.split(r"(?<=[.!?])\s", intro, maxsplit=1)[0] if intro else info.get("name", "")
        front = {
            "layout": "hub",
            "generated": True,
            "hub_kind": "story",
            "hub_slug": slug,
            "title": info.get("name", slug),
            "seo_title": f"{info.get('name', slug)}: סיפור מהעיתונות של לפני מאה שנה",
            "description": truncate(first_sentence, 155),
            "permalink": f"/stories/{slug}/",
            "in_sitemap": True,
        }
        name = f"{slug}.md"
        keep.add(name)
        written += write_if_changed(STORIES_DIR / name, render_page(front))
    removed = remove_stale(STORIES_DIR, keep)
    return story_pages, written, removed


# ---------------------------------------------------------------- main

def main():
    posts = list(iter_posts())
    stories = load_yaml(DATA_DIR / "stories.yml", {})
    tags = load_yaml(DATA_DIR / "tags.yml", [])
    tag_names = {t["slug"]: t["name"] for t in tags}
    registry = load_yaml(DATA_DIR / "entities.yml", {})

    fields = ensure_post_fields(posts)
    story_changes = apply_story_rules(posts, stories)
    if fields or story_changes:
        posts = list(iter_posts())  # re-read edited front matter
    print(f"Posts: {len(posts)} | fields filled on {fields} | story links added to {story_changes}")

    entities, first_last = build_entities(posts, registry)
    ent_changed = dump_yaml(
        DATA_DIR / "entities.yml", entities,
        header="# Generated by scripts/generate_site_data.py from post front matter. Names/types come from Gemini enrichment.\n")
    index = [{"slug": s, "name": i["name"], "type": i["type"], "count": i["count"]} for s, i in entities.items() if i["page"]]
    dump_yaml(DATA_DIR / "entity_index.yml", index, header="# Generated: entities that have their own page, most mentioned first.\n")

    story_pages, story_written, story_removed = generate_story_pages(posts, stories)
    dump_yaml(DATA_DIR / "story_pages.yml", story_pages, header="# Generated: stories that have a page, with their post counts.\n")

    archive_months, date_written, date_removed = generate_date_pages(posts, tag_names)
    dump_yaml(DATA_DIR / "archive_months.yml", archive_months, header="# Generated: one entry per month that has posts.\n")

    ent_written, ent_removed = generate_entity_pages(posts, entities, first_last)
    dump_yaml(DATA_DIR / "tag_hubs.yml", build_tag_hubs(posts, tags, entities, story_pages),
              header="# Generated: top entities and stories per topic.\n")

    related = build_related(posts, entities)
    related_text = json.dumps(related, ensure_ascii=False, separators=(",", ":")) + "\n"
    related_path = DATA_DIR / "related.json"
    if not related_path.exists() or related_path.read_text(encoding="utf-8") != related_text:
        write_text(related_path, related_text)

    print(
        f"Entities: {len(entities)} ({len(index)} with a page) | stories with a page: {len(story_pages)} | "
        f"date pages: {date_written} written, {date_removed} removed | "
        f"entity pages: {ent_written} written, {ent_removed} removed | story pages: {story_written} written, {story_removed} removed | "
        f"related for {len(related)} posts | entities.yml changed: {ent_changed}"
    )


if __name__ == "__main__":
    main()
