#!/usr/bin/env python3
"""Apply the post requests queued by the admin panel (_admin_inbox/*.json).

The Cloudflare Worker (newsletter-worker/) commits one JSON request per admin action,
plus any uploaded images, and dispatches the admin-post workflow. This script turns the
requests into post changes using the same code that builds posts from tweets, so a post
written in the admin panel is indistinguishable from one fetched from Twitter:

  create   text (first line = title) + image(s) + caption -> a new post dated now minus 100
           years; tags / standfirst / SEO title / entities / stories / sources are derived
           by the usual pipeline. The post is flagged `manual: true` (website only).
  edit     new text and/or image and/or caption for an existing post; its derived fields
           are cleared so they are regenerated for the new text.
  delete   hides the post (`published: false`), or with `hard` removes it for good.
  restore  un-hides a post.

Run with --enrich to also tag/enrich posts that are missing those fields (needs GEMINI_API_KEY).
Exits non-zero if any request failed; failed requests are moved to _admin_inbox/_failed/.
"""

import json
import os
import re
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_tweets import (  # noqa: E402
    DELETED_IDS_FILE,
    IMAGES_DIR,
    MAX_IMAGE_WIDTH,
    create_post,
    load_deleted_ids,
    optimize_image,
    split_title_body,
)
from gemini_tagging import tag_pending_posts  # noqa: E402
from image_meta import size_updates  # noqa: E402
from post_enrichment import enrich_pending_posts  # noqa: E402
from post_io import POSTS_DIR, REPO_ROOT, read_post, update_body, update_front_matter  # noqa: E402
from source_links import source_updates  # noqa: E402

INBOX = REPO_ROOT / "_admin_inbox"
FAILED = INBOX / "_failed"
CARDS_DIR = REPO_ROOT / "assets" / "images" / "instagram-cards"

ID_RE = re.compile(r"^\d{10,25}$")
IMAGE_RE = re.compile(r"^assets/images/tweets/(\d{10,25})((?:[_-][A-Za-z0-9]+)*)\.(?:jpg|png|gif|webp)$")
MD_IMAGE_LINE_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
# Fields derived from a post's text; cleared on edit so the pipeline rebuilds them.
DERIVED_FIELDS = ("tags", "standfirst", "seo_title", "entities", "mentions", "stories")
MAX_TEXT_CHARS = 20000
ENRICH_LIMIT = 10


class RequestError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise RequestError(message)


def clean_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    require(len(text) <= MAX_TEXT_CHARS, "text is too long")
    return text


def clean_caption(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\\", "")).strip()


def check_id(value):
    value = str(value or "")
    require(ID_RE.match(value), f"invalid post id: {value!r}")
    return value


def check_image(rel_path, tweet_id):
    """Validate an uploaded image (already committed by the worker) and return its site path."""
    rel_path = str(rel_path or "")
    match = IMAGE_RE.match(rel_path)
    require(match and match.group(1) == tweet_id, f"invalid image path: {rel_path!r}")
    full = REPO_ROOT / rel_path
    require(full.is_file(), f"image is missing: {rel_path}")
    data = full.read_bytes()
    try:
        with Image.open(BytesIO(data)) as img:
            width = img.width
            img.verify()
    except Exception as exc:  # noqa: BLE001 - any decode failure means a bad upload
        raise RequestError(f"not a valid image: {rel_path} ({exc})") from exc
    if width > MAX_IMAGE_WIDTH:
        resized, ext = optimize_image(data, full.suffix[1:])
        if ext == full.suffix[1:]:
            full.write_bytes(resized)
    return "/" + rel_path


def find_post(tweet_id):
    matches = [p for p in POSTS_DIR.glob(f"*-{tweet_id}.md") if p.stem[11:] == tweet_id]
    require(matches, f"post not found: {tweet_id}")
    return matches[0]


def image_files(tweet_id):
    pattern = re.compile(rf"^{tweet_id}(?:[_-][A-Za-z0-9]+)*\.\w+$")
    return [p for p in IMAGES_DIR.glob(f"{tweet_id}*") if pattern.match(p.name)]


def build_body(body, image_lines):
    """Body text followed by the extra-image lines, laid out exactly like fetch_tweets.create_post."""
    parts = [body] if body else []
    parts += [f"\n{line.strip()}" for line in image_lines]
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------- actions

def do_create(req):
    tweet_id = check_id(req.get("id"))
    text = clean_text(req.get("text"))
    require(text and split_title_body(text)[0], "text is empty")
    created_ms = int(req.get("created_ms") or 0)
    require(created_ms > 0, "missing created_ms")

    paths = req.get("images") or []
    require(len(paths) <= 6, "too many images")
    images = []
    for index, rel in enumerate(paths):
        check_image(rel, tweet_id)
        expected = tweet_id if index == 0 else f"{tweet_id}_{index}"
        require(Path(rel).stem == expected, f"unexpected image name: {rel}")
        # create_post reuses images already present under the id, so nothing is downloaded.
        images.append((f"local:{rel}", clean_caption(req.get("caption")) if index == 0 else ""))

    item = {
        "id": tweet_id,
        "tweet_id": tweet_id,
        "created_timestamp": created_ms // 1000,
        "merged_text": text,
        "images": images,
    }
    require(create_post(item, manual=True), "post already exists, was deleted, or has no text")
    print(f"  Created post {tweet_id}")


def do_edit(req):
    tweet_id = check_id(req.get("slug"))
    path = find_post(tweet_id)
    post = read_post(path)
    require(post is not None, f"cannot read {path.name}")

    changed = False
    title, body = split_title_body(clean_text(req.get("text")))
    require(title, "text is empty")
    old_lines = post.body.split("\n")
    image_lines = [line for line in old_lines if MD_IMAGE_LINE_RE.match(line)]
    old_body = "\n".join(line for line in old_lines if not MD_IMAGE_LINE_RE.match(line)).strip()

    text_changed = title != post.title or body != old_body
    if text_changed:
        update_front_matter(path, {"title": title}, remove=DERIVED_FIELDS)
        update_body(path, lambda _old: build_body(body, image_lines))
        changed = True

    if req.get("image"):
        new_image = check_image(req["image"], tweet_id)
        old_image = post.meta.get("image") or ""
        update_front_matter(path, {"image": new_image, **size_updates(new_image)})
        old_file = REPO_ROOT / str(old_image).lstrip("/")
        if old_image and old_image != new_image and old_file.is_file() and IMAGES_DIR in old_file.parents:
            old_file.unlink()
        changed = True

    has_image = bool(req.get("image") or post.meta.get("image"))
    if req.get("caption") is not None and has_image:
        caption = clean_caption(req["caption"])
        if caption != str(post.meta.get("image_alt") or ""):
            fields = source_updates(caption, post.date_str, post.meta.get("sources"))
            fields["image_alt"] = fields.get("image_alt", caption)  # '#N' article refs are removed from the caption
            update_front_matter(path, fields)
            changed = True

    print(f"  Edited post {tweet_id}" if changed else f"  No changes for {tweet_id}")


def do_delete(req):
    tweet_id = check_id(req.get("slug"))
    path = find_post(tweet_id)
    if not req.get("hard"):
        update_front_matter(path, {"published": False})
        print(f"  Hid post {tweet_id}")
        return

    path.unlink()
    for image in image_files(tweet_id):
        image.unlink()
    card = CARDS_DIR / f"{tweet_id}.jpg"
    if card.exists():
        card.unlink()
    deleted = load_deleted_ids() | {tweet_id}
    DELETED_IDS_FILE.write_text("\n".join(sorted(deleted)) + "\n", encoding="utf-8")
    print(f"  Deleted post {tweet_id} permanently")


def do_restore(req):
    tweet_id = check_id(req.get("slug"))
    update_front_matter(find_post(tweet_id), {}, remove=("published",))
    print(f"  Restored post {tweet_id}")


ACTIONS = {"create": do_create, "edit": do_edit, "delete": do_delete, "restore": do_restore}


# ---------------------------------------------------------------- main

def process_request(path):
    req = json.loads(path.read_text(encoding="utf-8"))
    action = req.get("action")
    require(action in ACTIONS, f"unknown action: {action!r}")
    print(f"{path.name}: {action}")
    ACTIONS[action](req)


def main():
    failures = 0
    for path in sorted(INBOX.glob("*.json")) if INBOX.exists() else []:
        try:
            process_request(path)
            path.unlink()
        except (RequestError, ValueError, TypeError, AttributeError, OSError) as exc:
            failures += 1
            print(f"::error::{path.name} failed: {exc}")
            FAILED.mkdir(parents=True, exist_ok=True)
            path.rename(FAILED / path.name)

    if "--enrich" in sys.argv:
        api_key = os.environ.get("GEMINI_API_KEY", "")
        tag_pending_posts(api_key)
        try:
            enrich_pending_posts(api_key, limit=ENRICH_LIMIT)
        except Exception as exc:  # noqa: BLE001 - best effort, retried on the next run
            print(f"Enrichment skipped: {exc}")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
