#!/usr/bin/env python3
"""Dev-only helper: render Instagram cards for the N most recent posts into
scripts/_preview/, without touching .last_instagram_post or committing
anything. Useful for eyeballing the design across a range of real images.

Usage:
    python scripts/preview_instagram_cards.py [count]

On Linux (with fonts-noto-core installed) it uses the real production font.
On Windows/macOS, where Noto Sans Hebrew usually isn't installed, it falls
back to a system font that supports Hebrew, purely for local previewing.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instagram_card as ic
from instagram_title import generate_card_titles
from post_via_make import parse_post

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
PREVIEW_DIR = REPO_ROOT / "scripts" / "_preview"
FULL_IMAGE_MARKER = ";;"  # in image_alt — picks the uncropped-image template

FALLBACK_FONTS = {
    False: [r"C:\Windows\Fonts\arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"],
    True: [r"C:\Windows\Fonts\arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
}


def _use_fallback_font_if_needed():
    """If no Noto Sans Hebrew is installed (e.g. local Windows/macOS dev
    machine), substitute a system font that supports Hebrew for previewing."""
    if ic._find_hebrew_font_files():
        return
    from PIL import ImageFont

    def fallback_load_font(size, bold=False):
        for path in FALLBACK_FONTS[bold]:
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        raise RuntimeError("No Hebrew-capable font found for local preview either.")

    print("[preview] No Noto Sans Hebrew found — using a system fallback font for preview only.")
    ic.load_hebrew_font = fallback_load_font


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 25

    _use_fallback_font_if_needed()

    all_posts = sorted(POSTS_DIR.glob("*.md"))
    recent_posts = all_posts[-count:]

    posts = [parse_post(filepath) for filepath in recent_posts]
    card_titles = generate_card_titles(
        [{"tweet_id": p["tweet_id"], "title": p["title"], "body": p["body"]} for p in posts if p["tweet_id"]],
        os.environ.get("GEMINI_API_KEY", ""),
    )

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    generated = 0
    for filepath, post in zip(recent_posts, posts):
        card_title = card_titles.get(post["tweet_id"], post["title"])
        out_path = PREVIEW_DIR / f"{filepath.stem}.jpg"
        if card_title != post["title"]:
            print(f"  [title] {post['title']!r} -> {card_title!r}")

        if not post["image"]:
            print(f"  Rendering text-only card: {card_title}")
            card = ic.generate_text_card(card_title, datetime.fromisoformat(post["date"]))
            card.save(out_path, "JPEG", quality=92)
            generated += 1
            continue

        source_path = REPO_ROOT / post["image"].lstrip("/")
        if not source_path.exists():
            print(f"  [SKIP] Missing source image: {filepath.name}")
            continue

        print(f"  Rendering: {card_title}")
        historical_date = datetime.fromisoformat(post["date"])
        if FULL_IMAGE_MARKER in post.get("image_alt", ""):
            card = ic.generate_full_image_card(source_path, card_title, historical_date)
        elif int(post["tweet_id"]) % 2 == 0:
            card = ic.generate_banner_card(source_path, card_title, historical_date)
        else:
            card = ic.generate_card(source_path, card_title, historical_date)
        card.save(out_path, "JPEG", quality=92)
        generated += 1


    print(f"\nDone. {generated} card(s) saved to {PREVIEW_DIR}")


if __name__ == "__main__":
    main()
