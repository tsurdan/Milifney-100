#!/usr/bin/env python3
"""Build the "מקור הידיעה" (news source) data for a post from its image caption.

A source is {"paper": <Hebrew name>, "date": "YYYY-MM-DD", "url": <National Library issue page>}.
The date is the issue date written in the caption when there is one; otherwise it is
the post's own 100-years-ago date. Only newspapers whose National Library code has
been verified get a URL: https://www.nli.org.il/he/newspapers/<code>/<YYYY>/<MM>/<DD>/01/

Run as a script to backfill every post that has no `sources` field yet:

    python scripts/source_links.py            # add missing
    python scripts/source_links.py --force    # recompute all
"""

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_io import iter_posts, update_front_matter  # noqa: E402

NLI_ISSUE_URL = "https://www.nli.org.il/he/newspapers/{code}/{y}/{m}/{d}/01/"

# Codes verified against National Library citations (e.g. on Hebrew Wikipedia).
NLI_CODES = {
    "דאר היום": "dhy",
    "הארץ": "haretz",
    "דבר": "dav",
}
PAPER_ALIASES = {"דואר היום": "דאר היום"}

MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
    "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}
MONTH_ALT = "|".join(MONTHS)
HEBREW = "\u0590-\u05FF"

_PAPER_RE = re.compile(rf"(?<![{HEBREW}])ו?(דאר היום|דואר היום|הארץ|דבר)(?![{HEBREW}])")
_ARCHIVE_RE = re.compile(r"ארכי(?:ון|וו) העיתונות")
_NUM_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
_HE_DATE_RE = re.compile(rf"(?<!\d)(\d{{1,2}})\s+ב({MONTH_ALT})(?:[,\s]+(\d{{4}}))?")
_SOURCE_MARKERS = ("מקור הידיעה", "מקורות", "מקור", "להרחבה", "להחרבה", "לידיעה")
_MARKED_PAPER_RE = re.compile(
    rf"(?:מקור הידיעה|מקורות|מקור|להרחבה|להחרבה|לידיעה)\s*:\s*"
    rf"([{HEBREW}'\"״׳]+(?: [{HEBREW}'\"״׳]+){{0,2}})\s*(?:,|\(|;|\.|\d|$)")
_NOT_PAPERS = {"גליון", "ארכיון", "הארכיון", "ויקיפדיה", "ויקיפדה", "הספרייה הלאומית"}

MAX_DAYS_FROM_POST = 40      # ignore dates that are probably photo credits
MAX_GAP_TO_ARCHIVE = 45      # paper name must sit right before "ארכיון העיתונות"
SATURDAY = 5                 # no Hebrew dailies on Shabbat -> no issue page to link to


def _safe_date(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _find_dates(text, post_day):
    """Issue dates mentioned in text, close to the post date."""
    found = []
    for d, m, y in _NUM_DATE_RE.findall(text):
        found.append(_safe_date(int(y), int(m), int(d)))
    for d, month_name, y in _HE_DATE_RE.findall(text):
        month = MONTHS[month_name]
        if y:
            found.append(_safe_date(int(y), month, int(d)))
        else:
            candidates = [_safe_date(post_day.year + off, month, int(d)) for off in (-1, 0, 1)]
            candidates = [c for c in candidates if c]
            if candidates:
                found.append(min(candidates, key=lambda c: abs((c - post_day).days)))
    return [c for c in found if c and abs((c - post_day).days) <= MAX_DAYS_FROM_POST]


def _unknown_paper(caption):
    """Name of an unlisted paper credited right before 'ארכיון העיתונות', if clearly marked."""
    match = _ARCHIVE_RE.search(caption)
    if not match:
        return None
    marked = _MARKED_PAPER_RE.search(caption[: match.start() + 1])
    if marked and marked.group(1).strip() not in _NOT_PAPERS and "פרסומת" not in marked.group(1):
        return marked.group(1).strip()
    head = caption[: match.start()]
    pieces = re.split(r"([:.|;])", head)
    clause = pieces[-1]
    delimiter = pieces[-2] if len(pieces) >= 3 else ""
    before = "".join(pieces[:-2]).strip() if delimiter else ""
    if delimiter and not (delimiter == ":" and before.endswith(_SOURCE_MARKERS)):
        return None
    clause = re.sub(r"\([^)]*\)", "", clause).strip(" ,\"'״׳")
    if "פרסומת" in clause or len(clause.split()) > 3:
        return None
    if re.fullmatch(rf"[{HEBREW}\"'״׳ ]{{2,25}}", clause) and clause not in ("ארכיון", "הארכיון"):
        return clause
    return None


def build_sources(caption, post_date_str):
    """Return the list of source dicts for a caption (may be empty)."""
    caption = (caption or "").replace(";;", " ").replace("::", " ")
    archives = list(_ARCHIVE_RE.finditer(caption))
    if not archives:
        return []
    try:
        post_day = date.fromisoformat(post_date_str)
    except ValueError:
        return []

    papers = [m for m in _PAPER_RE.finditer(caption)
              if any(0 <= a.start() - m.end() <= MAX_GAP_TO_ARCHIVE for a in archives)]
    sources = []

    def add(paper, issue_day):
        paper = PAPER_ALIASES.get(paper, paper)
        if any(s["paper"] == paper and s["date"] == issue_day.isoformat() for s in sources):
            return
        entry = {"paper": paper, "date": issue_day.isoformat()}
        code = NLI_CODES.get(paper)
        if code and issue_day.weekday() != SATURDAY:
            entry["url"] = NLI_ISSUE_URL.format(
                code=code, y=f"{issue_day.year:04d}", m=f"{issue_day.month:02d}", d=f"{issue_day.day:02d}")
        sources.append(entry)

    if papers:
        for i, match in enumerate(papers):
            if len(papers) == 1:
                window = caption
            else:
                end = papers[i + 1].start() if i + 1 < len(papers) else len(caption)
                window = caption[match.start():end]
            dates = _find_dates(window, post_day)
            add(match.group(1), dates[0] if dates else post_day)
    else:
        name = _unknown_paper(caption)
        if name:
            dates = _find_dates(caption, post_day)
            add(name, dates[0] if dates else post_day)
    return sources


def source_updates(caption, post_date_str):
    """Front-matter fields to write for a post."""
    return {"sources": build_sources(caption, post_date_str)}


def main():
    force = "--force" in sys.argv
    changed = linked = total = 0
    for post in iter_posts():
        total += 1
        if "sources" in post.meta and not force:
            continue
        updates = source_updates(post.meta.get("image_alt", ""), post.date_str)
        if update_front_matter(post.path, updates):
            changed += 1
        if any("url" in s for s in updates["sources"]):
            linked += 1
    print(f"Sources: {changed} post(s) updated; {linked} with an issue link (of {total} published).")


if __name__ == "__main__":
    main()
