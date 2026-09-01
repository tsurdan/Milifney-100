#!/usr/bin/env python3
"""One-time local backfill CLI: assign 1-3 tags (from the fixed vocabulary in
_data/tags.yml) to every existing post, using the Gemini API.

Run locally (not in CI) since it needs a personal free-tier API key:

    $env:GEMINI_API_KEY = "..."          # PowerShell
    python scripts/tag_posts.py

See gemini_tagging.py for the shared logic — it's also used by
fetch_tweets.py to tag posts as part of the regular cron run.
"""

import os
import sys

from gemini_tagging import tag_pending_posts


def main():
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("Set GEMINI_API_KEY before running (get a free key at https://aistudio.google.com/apikey).")
        sys.exit(1)

    tagged_count = tag_pending_posts(api_key)
    print(f"Done. Tagged {tagged_count} post(s).")


if __name__ == "__main__":
    main()
