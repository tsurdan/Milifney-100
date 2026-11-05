#!/usr/bin/env python3
"""Backfill standfirsts, SEO titles, entities and stories with the Gemini API.

Run locally (not in CI) with a personal free-tier key:

    $env:GEMINI_API_KEY = "..."            # PowerShell
    python scripts/enrich_posts.py --limit 20 --dry-run   # preview the first 20
    python scripts/enrich_posts.py                         # write everything

The same logic also runs inside fetch_tweets.py for each newly fetched post.
After enriching, run scripts/generate_site_data.py to refresh counts, hub pages
and related posts.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_enrichment import enrich_pending_posts  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="only process this many pending posts")
    parser.add_argument("--dry-run", action="store_true", help="print results without writing files")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        print("Set GEMINI_API_KEY first (free key: https://aistudio.google.com/apikey).")
        sys.exit(1)

    written = enrich_pending_posts(api_key, limit=args.limit, dry_run=args.dry_run)
    print(f"Done. Enriched {written} post(s).")


if __name__ == "__main__":
    main()
