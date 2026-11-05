#!/usr/bin/env python3
"""Record each post's main image size (image_w / image_h) in its front matter.

The templates use the size for width/height attributes (no layout shift) and for
og:image:width/height. Run as a script to backfill every post that has an image
but no size yet:

    python scripts/image_meta.py
"""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_io import REPO_ROOT, iter_posts, update_front_matter  # noqa: E402


def image_size(image_path):
    """Return (width, height) for a site-relative path like /assets/images/x.jpg, or None."""
    if not image_path:
        return None
    full = REPO_ROOT / str(image_path).lstrip("/")
    if not full.exists():
        return None
    try:
        with Image.open(full) as img:
            return img.width, img.height
    except Exception:
        return None


def size_updates(image_path):
    size = image_size(image_path)
    return {"image_w": size[0], "image_h": size[1]} if size else {}


def main():
    changed = missing = 0
    for post in iter_posts():
        image = post.meta.get("image")
        if not image or "image_w" in post.meta:
            continue
        updates = size_updates(image)
        if not updates:
            missing += 1
            continue
        if update_front_matter(post.path, updates):
            changed += 1
    print(f"Image sizes: {changed} post(s) updated, {missing} image(s) not found.")


if __name__ == "__main__":
    main()
