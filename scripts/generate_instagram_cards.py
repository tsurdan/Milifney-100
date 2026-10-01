#!/usr/bin/env python3
"""Generate branded Instagram card images for posts not yet posted.

Writes JPGs into assets/images/instagram-cards/. The workflow commits and
pushes these before post_via_make.py runs, since both Instagram (direct Graph
API) and the Make.com webhook route need a public image URL
(raw.githubusercontent.com) rather than a direct file upload.
"""

from datetime import datetime
from pathlib import Path

from instagram_card import generate_card, generate_text_card, parse_post_front_matter

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
STATE_FILE = REPO_ROOT / "scripts" / ".last_make_post"
CARDS_DIR = REPO_ROOT / "assets" / "images" / "instagram-cards"


def get_pending_posts():
    last_filename = STATE_FILE.read_text().strip() if STATE_FILE.exists() else ""
    all_posts = sorted(POSTS_DIR.glob("*.md"))
    if not all_posts:
        return []
    if last_filename:
        return [p for p in all_posts if p.name > last_filename]
    # First run: only the latest post, to match the other channels' behavior.
    return all_posts[-1:]


def main():
    pending = get_pending_posts()
    if not pending:
        print("No pending posts for Instagram.")
        return

    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    created = 0
    for filepath in pending:
        post = parse_post_front_matter(filepath)
        if not post["tweet_id"]:
            continue

        card_path = CARDS_DIR / f"{post['tweet_id']}.jpg"
        if card_path.exists():
            continue

        if not post["image"]:
            print(f"  Generating text-only card for: {post['title']}")
            historical_date = datetime.fromisoformat(post["date"])
            card = generate_text_card(post["title"], historical_date)
            card.save(card_path, "JPEG", quality=90)
            created += 1
            continue

        source_path = REPO_ROOT / post["image"].lstrip("/")
        if not source_path.exists():
            print(f"  [SKIP] Source image missing for {filepath.name}")
            continue

        print(f"  Generating card for: {post['title']}")
        historical_date = datetime.fromisoformat(post["date"])
        card = generate_card(source_path, post["title"], historical_date)
        card.save(card_path, "JPEG", quality=90)
        created += 1

    print(f"Done. Generated {created} card(s).")


if __name__ == "__main__":
    main()
