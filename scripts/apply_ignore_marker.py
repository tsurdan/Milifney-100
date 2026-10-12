#!/usr/bin/env python3
"""Exclude posts marked for removal from the live website.

A post is "ignored" when its image_alt front-matter field contains '**'
(manually added by the editor). This adds `published: false` to such posts -
Jekyll's native mechanism for excluding a post from site.posts/output while
keeping the file in the repo - so it stops appearing anywhere on the site.

Posting scripts (post_telegram.py, generate_instagram_cards.py,
post_via_make.py) each separately skip these posts too, since they read
front matter directly and don't consult Jekyll's `published` flag.

Idempotent and safe to run every workflow cycle.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_via_make import parse_post  # robust YAML front-matter parsing

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
IGNORE_MARKER = "**"


def main():
    updated = 0
    for filepath in sorted(POSTS_DIR.glob("*.md")):
        post = parse_post(filepath)
        if IGNORE_MARKER not in post.get("image_alt", ""):
            continue

        content = filepath.read_text(encoding="utf-8")
        _, front_matter, body = content.split("---", 2)
        if "published: false" in front_matter or "published:false" in front_matter:
            continue

        new_front_matter = front_matter.rstrip("\n") + "\npublished: false\n"
        filepath.write_text(f"---{new_front_matter}---{body}", encoding="utf-8")
        print(f"  Marked as unpublished: {filepath.name}")
        updated += 1

    print(f"Done. {updated} post(s) newly marked unpublished.")


if __name__ == "__main__":
    main()
