#!/usr/bin/env python3
"""Shared helpers for reading and editing post front matter.

Front matter is edited line by line so every other field (and the body)
stays byte-for-byte identical. Values are written as JSON, which is valid YAML.
"""

import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
POSTS_DIR = REPO_ROOT / "_posts"
DATA_DIR = REPO_ROOT / "_data"

_FM_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_SAFE_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class Post:
    def __init__(self, path, fm_text, body):
        self.path = path
        self.fm_text = fm_text
        self.body = body
        try:
            self.meta = yaml.safe_load(fm_text) or {}
        except yaml.YAMLError:
            self.meta = {}

    @property
    def published(self):
        return self.meta.get("published", True) is not False

    @property
    def tweet_id(self):
        return str(self.meta.get("tweet_id", ""))

    @property
    def title(self):
        return str(self.meta.get("title", "") or "")

    @property
    def date_str(self):
        """The 100-years-ago date as written in front matter (UTC), YYYY-MM-DD."""
        match = re.search(r"^date:\s*(\d{4}-\d{2}-\d{2})", self.fm_text, re.MULTILINE)
        return match.group(1) if match else ""

    @property
    def time_str(self):
        match = re.search(r"^date:\s*\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})", self.fm_text, re.MULTILINE)
        return match.group(1) if match else "00:00:00"

    @property
    def publish_date_str(self):
        """Real publish date, from the filename (YYYY-MM-DD-<id>.md)."""
        return self.path.name[:10]

    @property
    def slug(self):
        return self.path.stem[11:]

    @property
    def url(self):
        y, m, d = self.date_str.split("-")
        return f"/{y}/{m}/{d}/{self.slug}/"

    @property
    def text(self):
        """Body text without markdown images and thread markers."""
        return clean_body(self.body)

    def tags(self):
        return list(self.meta.get("tags") or [])

    def get_list(self, key):
        value = self.meta.get(key) or []
        return [str(v) for v in value] if isinstance(value, list) else []


def clean_body(body):
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    text = re.sub(r"^>+\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def read_post(path):
    raw = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _FM_RE.match(raw)
    if not match:
        return None
    return Post(Path(path), match.group(1), match.group(2))


def iter_posts(published_only=True):
    for path in sorted(POSTS_DIR.glob("*.md")):
        post = read_post(path)
        if post is None or not post.meta.get("tweet_id"):
            continue
        if published_only and not post.published:
            continue
        yield post


def format_value(value):
    """Render a Python value as a one-line YAML (flow/JSON) scalar."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, str) and _SAFE_SLUG_RE.match(v) for v in value):
            return "[" + ", ".join(value) + "]"
        return json.dumps(list(value), ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def write_text(path, text):
    # Always LF; git (autocrlf) handles the working-tree conversion.
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def update_front_matter(path, updates, remove=()):
    """Set or replace front-matter keys on a post. Returns True if the file changed."""
    raw = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    match = _FM_RE.match(raw)
    if not match:
        return False
    fm_lines = match.group(1).split("\n")
    body = match.group(2)

    for key in remove:
        fm_lines = [ln for ln in fm_lines if not re.match(rf"^{re.escape(key)}\s*:", ln)]

    for key, value in updates.items():
        new_line = f"{key}: {format_value(value)}"
        for i, line in enumerate(fm_lines):
            if re.match(rf"^{re.escape(key)}\s*:", line):
                fm_lines[i] = new_line
                break
        else:
            fm_lines.append(new_line)

    new_raw = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    if new_raw == raw:
        return False
    write_text(path, new_raw)
    return True


def load_yaml(path, default):
    path = Path(path)
    if not path.exists():
        return default
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return default if data is None else data


def dump_yaml(path, data, header=""):
    text = header + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100000, default_flow_style=False)
    path = Path(path)
    if path.exists() and path.read_text(encoding="utf-8").replace("\r\n", "\n") == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, text)
    return True
