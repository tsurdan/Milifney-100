#!/usr/bin/env python3
"""Generate a branded, professional-looking Instagram card from a post.

Produces a 1080x1350 (4:5) JPEG styled like mainstream news Instagram
accounts: full-bleed source photo, gently darkened (gradient only, no blur)
towards the bottom, with the bold wrapped headline in full-width accent-color
bars and the logo + site name at the very bottom — mirroring how outlets like
N12 lay out their text-on-image posts.

Requires a Hebrew-capable TTF on the system (e.g. apt package
`fonts-noto-core`, which ships Noto Sans Hebrew) — Pillow cannot fall back to
fontconfig lookups on its own.
"""

import glob
from pathlib import Path

from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont, ImageOps

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGO_PATH = REPO_ROOT / "assets" / "images" / "branding" / "avatar.jpg"

CARD_SIZE = (1080, 1350)  # 4:5 — standard news-card ratio (Ynet/N12/Mako/CNN/BBC all use this for text-on-image posts)
ACCENT = (222, 24, 24)        # vivid "fluorescent" red, like the reference news-card template
WHITE = (255, 255, 255)

FONT_SEARCH_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts")]

HEBREW_MONTHS = {
    1: "בינואר", 2: "בפברואר", 3: "במרץ", 4: "באפריל", 5: "במאי", 6: "ביוני",
    7: "ביולי", 8: "באוגוסט", 9: "בספטמבר", 10: "באוקטובר", 11: "בנובמבר", 12: "בדצמבר",
}


def _find_hebrew_font_files():
    matches = []
    for base in FONT_SEARCH_DIRS:
        matches.extend(glob.glob(f"{base}/**/*NotoSansHebrew*", recursive=True))
    return matches


def load_hebrew_font(size, bold=False):
    """Load a Hebrew-capable font at the given size. Picks a static weight file
    if available, otherwise tries to set the weight axis on a variable font."""
    candidates = _find_hebrew_font_files()
    if not candidates:
        raise RuntimeError(
            "No Hebrew font found on this system. Install one first, e.g.: "
            "sudo apt-get install -y fonts-noto-core"
        )
    weight_word = "bold" if bold else "regular"
    static_match = next((f for f in candidates if weight_word in f.lower()), None)
    font_path = static_match or candidates[0]
    # Force the legacy "basic" layout engine — when raqm is available (as it
    # is in Pillow's official Linux wheels, unlike this project's Windows
    # dev setups), Pillow applies its own bidi reordering during draw.text(),
    # which double-reverses text we've already reordered via get_display().
    font = ImageFont.truetype(font_path, size, layout_engine=ImageFont.Layout.BASIC)
    if static_match is None:
        try:
            font.set_variation_by_axes([700 if bold else 400])
        except Exception:
            pass
    return font


FALLBACK_FONT_PATHS = {
    True: "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    False: "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
}


def load_fallback_font(size, bold=False):
    """Font for glyphs the Hebrew font's subset doesn't cover — Noto Sans
    Hebrew only ships ~149 Hebrew-script glyphs, no digits or Latin
    punctuation. DejaVu Sans ships by default on Ubuntu/GitHub Actions; if
    missing (e.g. local Windows previews), falls back to load_hebrew_font,
    which already covers these characters there (e.g. Arial)."""
    path = FALLBACK_FONT_PATHS[bold]
    if not Path(path).exists():
        return load_hebrew_font(size, bold=bold)
    return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.BASIC)


def _needs_fallback_font(ch):
    """True for characters outside the Hebrew Unicode block — digits, Latin
    punctuation, etc. — which the Hebrew font subset doesn't include."""
    return ch != " " and not ("\u0590" <= ch <= "\u05FF")


def _split_font_runs(text, main_font, fallback_font):
    """Split (already bidi-reordered) text into consecutive runs paired with
    the font that should render each one."""
    runs = []
    run, run_font = "", None
    for ch in text:
        font = fallback_font if _needs_fallback_font(ch) else main_font
        if run and font is not run_font:
            runs.append((run, run_font))
            run = ""
        run += ch
        run_font = font
    if run:
        runs.append((run, run_font))
    return runs


def measure_mixed_text(draw, text, main_font, fallback_font):
    """Total width of text if drawn with per-character font fallback."""
    return sum(draw.textlength(s, font=f) for s, f in _split_font_runs(text, main_font, fallback_font))


def draw_mixed_text(draw, xy, text, main_font, fallback_font, fill):
    """Draw text left-to-right at xy, substituting fallback_font for glyphs
    main_font doesn't cover (digits, Latin punctuation). All runs are aligned
    to main_font's baseline — different fonts have different ascent/descent
    proportions, so drawing each run at the same top-left y (the default)
    makes the fallback-font characters (e.g. digits) sit noticeably higher
    or lower than the Hebrew text around them."""
    x, y = xy
    baseline_y = y + main_font.getmetrics()[0]
    for s, f in _split_font_runs(text, main_font, fallback_font):
        draw.text((x, baseline_y), s, font=f, fill=fill, anchor="ls")
        x += draw.textlength(s, font=f)


def wrap_text(draw, text, font, max_width):
    """Word-wrap logical (not yet bidi-reordered) text to fit max_width."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fit_headline(draw, text, max_width, max_lines, start_size, min_size, step=4):
    """Shrink the font until the headline fits within max_lines, then truncate
    with an ellipsis as a last resort."""
    size = start_size
    while size > min_size:
        font = load_hebrew_font(size, bold=True)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) <= max_lines:
            return font, lines
        size -= step
    font = load_hebrew_font(min_size, bold=True)
    lines = wrap_text(draw, text, font, max_width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return font, lines


def paste_circular_logo(base, logo_path, diameter, center_x, center_y):
    logo = Image.open(logo_path).convert("RGB")
    logo = ImageOps.fit(logo, (diameter, diameter), Image.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter, diameter), fill=255)
    base.paste(logo, (center_x - diameter // 2, center_y - diameter // 2), mask)


def format_hebrew_date(dt):
    """Format a date as '<day> ב<month> <year>' with Hebrew month name."""
    return f"{dt.day} {HEBREW_MONTHS[dt.month]} {dt.year}"


def add_bottom_gradient(base, height, max_opacity=255, curve=0.45, soft_start=0.32):
    """Darken the bottom `height` px of `base` with an eased gradient (no
    blur) — deepening towards the bottom, close to black, so the headline
    pops clearly against the photo. `soft_start` smooths out the very top of
    the region so the darkening fades in gently instead of starting abruptly."""
    gradient = Image.new("L", (1, height), 0)
    for y in range(height):
        t = y / height
        value = t ** curve
        if soft_start > 0:
            ramp = min(1.0, t / soft_start)
            value *= ramp * ramp * (3 - 2 * ramp)  # smoothstep ease-in
        gradient.putpixel((0, y), int(max_opacity * value))
    gradient = gradient.resize((base.width, height))
    black = Image.new("RGBA", (base.width, height), (0, 0, 0, 255))
    base.paste(black, (0, base.height - height), gradient)


def generate_card(source_image_path, title, historical_date):
    """Build the branded Instagram card. Returns a PIL Image (RGB)."""
    photo = ImageOps.exif_transpose(Image.open(source_image_path)).convert("RGB")
    base = ImageOps.fit(photo, CARD_SIZE, Image.LANCZOS).convert("RGBA")

    # --- Pre-measure the headline + centered logo/name/date stack first, so
    # the gradient and content block are sized to fit them ---
    title = title.rstrip(": ")
    text_side_pad = 60
    measure_draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    headline_font, headline_lines = fit_headline(
        measure_draw, title, max_width=CARD_SIZE[0] - 2 * text_side_pad, max_lines=3, start_size=66, min_size=44
    )
    h_ascent, h_descent = headline_font.getmetrics()
    highlight_v_pad, line_gap = 14, 10
    highlight_h = (h_ascent + h_descent) + 2 * highlight_v_pad
    total_text_h = highlight_h * len(headline_lines) + line_gap * (len(headline_lines) - 1)

    logo_diameter = 68
    brand_font = load_hebrew_font(28, bold=True)
    brand_text = get_display("חדשות מלפני מאה")
    b_ascent, b_descent = brand_font.getmetrics()
    brand_h = b_ascent + b_descent
    kicker_font = load_hebrew_font(22, bold=True)
    kicker_text = get_display(format_hebrew_date(historical_date))
    kicker_fallback_font = load_fallback_font(22, bold=True)
    k_ascent, k_descent = kicker_font.getmetrics()
    kicker_h = k_ascent + k_descent

    gap_top, gap_logo_brand, gap_brand_headline, gap_headline_date, bottom_margin = 40, 16, 30, 24, 40
    stack_h = logo_diameter + gap_logo_brand + brand_h
    content_height = gap_top + stack_h + gap_brand_headline + total_text_h + gap_headline_date + kicker_h + bottom_margin
    add_bottom_gradient(base, content_height + 130)  # extra room above so the fade feels gradual
    draw = ImageDraw.Draw(base)

    # --- Logo + site name, centered, above the headline ---
    block_top = CARD_SIZE[1] - content_height + gap_top
    logo_cx = CARD_SIZE[0] // 2
    logo_cy = block_top + logo_diameter // 2
    paste_circular_logo(base, LOGO_PATH, logo_diameter, logo_cx, logo_cy)
    draw = ImageDraw.Draw(base)
    draw.ellipse(
        (logo_cx - logo_diameter // 2, logo_cy - logo_diameter // 2,
         logo_cx + logo_diameter // 2, logo_cy + logo_diameter // 2),
        outline=WHITE, width=4,
    )

    # --- Divider line flanking the logo, like a classic masthead rule ---
    rule_gap = 24
    draw.line((text_side_pad, logo_cy, logo_cx - logo_diameter // 2 - rule_gap, logo_cy), fill=WHITE, width=2)
    draw.line((logo_cx + logo_diameter // 2 + rule_gap, logo_cy, CARD_SIZE[0] - text_side_pad, logo_cy), fill=WHITE, width=2)

    brand_w = draw.textlength(brand_text, font=brand_font)
    brand_y = logo_cy + logo_diameter // 2 + gap_logo_brand
    draw.text(((CARD_SIZE[0] - brand_w) / 2, brand_y), brand_text, font=brand_font, fill=WHITE)

    # --- Headline: big, bold, centered — each line "marker-highlighted" in
    # semi-transparent accent color, sized to that line's own text width ---
    y = brand_y + brand_h + gap_brand_headline
    highlight_pad_x = 20
    headline_fallback_font = load_fallback_font(headline_font.size, bold=True)
    highlight_overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(highlight_overlay)
    line_ys = []
    for line in headline_lines:
        visual = get_display(line)
        w = measure_mixed_text(draw, visual, headline_font, headline_fallback_font)
        box_left = (CARD_SIZE[0] - w) / 2 - highlight_pad_x
        box_right = (CARD_SIZE[0] + w) / 2 + highlight_pad_x
        overlay_draw.rounded_rectangle((box_left, y, box_right, y + highlight_h), radius=12, fill=(*ACCENT, 220))
        line_ys.append((y, w, visual))
        y += highlight_h + line_gap
    base.alpha_composite(highlight_overlay)
    draw = ImageDraw.Draw(base)
    for y, w, visual in line_ys:
        draw_mixed_text(draw, ((CARD_SIZE[0] - w) / 2, y + highlight_v_pad), visual, headline_font, headline_fallback_font, fill=WHITE)

    # --- Date, centered, below the headline ---
    kicker_w = measure_mixed_text(draw, kicker_text, kicker_font, kicker_fallback_font)
    kicker_y = line_ys[-1][0] + highlight_h + gap_headline_date
    draw_mixed_text(draw, ((CARD_SIZE[0] - kicker_w) / 2, kicker_y), kicker_text, kicker_font, kicker_fallback_font, fill=(220, 220, 220))

    return base.convert("RGB")


def generate_full_image_card(source_image_path, title, historical_date):
    """Build a card for images that must be shown uncropped — e.g. scanned
    documents/clippings with their own embedded text, where the usual
    full-bleed crop (generate_card) would cut off or illegibly shrink it.
    Black background, white masthead (logo + brand name), the full image
    letterboxed inside a white frame (never cropped), then the headline and
    date below — selected when a post's image_alt contains ";;"."""
    base = Image.new("RGB", CARD_SIZE, (0, 0, 0)).convert("RGBA")
    draw = ImageDraw.Draw(base)

    # --- Masthead: logo + brand name + a rule, all in white ---
    logo_diameter = 80
    top_margin = 50
    logo_cx = CARD_SIZE[0] // 2
    logo_cy = top_margin + logo_diameter // 2
    paste_circular_logo(base, LOGO_PATH, logo_diameter, logo_cx, logo_cy)
    draw = ImageDraw.Draw(base)
    draw.ellipse(
        (logo_cx - logo_diameter // 2, logo_cy - logo_diameter // 2,
         logo_cx + logo_diameter // 2, logo_cy + logo_diameter // 2),
        outline=WHITE, width=3,
    )

    brand_font = load_hebrew_font(28, bold=True)
    brand_text = get_display("חדשות מלפני מאה")
    brand_w = draw.textlength(brand_text, font=brand_font)
    b_ascent, b_descent = brand_font.getmetrics()
    brand_y = logo_cy + logo_diameter // 2 + 20
    draw.text((logo_cx - brand_w / 2, brand_y), brand_text, font=brand_font, fill=WHITE)

    rule_y = brand_y + (b_ascent + b_descent) + 26
    draw.line((CARD_SIZE[0] // 2 - 50, rule_y, CARD_SIZE[0] // 2 + 50, rule_y), fill=WHITE, width=3)

    # --- Pre-measure the headline + footer, so the image area can fill
    # whatever vertical space is left between the masthead and them ---
    title = title.rstrip(": ")
    side_pad = 90
    measure_draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    headline_font, headline_lines = fit_headline(
        measure_draw, title, max_width=CARD_SIZE[0] - 2 * side_pad, max_lines=3, start_size=48, min_size=34
    )
    h_ascent, h_descent = headline_font.getmetrics()
    highlight_v_pad, line_gap = 10, 8
    highlight_h = (h_ascent + h_descent) + 2 * highlight_v_pad
    total_headline_h = highlight_h * len(headline_lines) + line_gap * (len(headline_lines) - 1)

    footer_font = load_hebrew_font(22, bold=True)
    footer_fallback_font = load_fallback_font(22, bold=True)
    footer_text = get_display(f"{format_hebrew_date(historical_date)}  •  milifney100.com")
    footer_h = sum(footer_font.getmetrics())

    # --- The image itself: contained (never cropped) inside a white frame,
    # centered in whatever space is left ---
    image_top = rule_y + 30
    image_bottom = CARD_SIZE[1] - 40 - footer_h - 20 - total_headline_h - 20
    image_area_w, image_area_h = CARD_SIZE[0] - 2 * 60, image_bottom - image_top

    photo = ImageOps.exif_transpose(Image.open(source_image_path)).convert("RGB")
    fitted = ImageOps.contain(photo, (int(image_area_w), int(image_area_h)), Image.LANCZOS)
    frame_pad = 10
    frame_w, frame_h = fitted.width + 2 * frame_pad, fitted.height + 2 * frame_pad
    frame_x = (CARD_SIZE[0] - frame_w) // 2
    frame_y = int(image_top + (image_area_h - frame_h) / 2)
    draw.rectangle((frame_x, frame_y, frame_x + frame_w, frame_y + frame_h), fill=WHITE)
    base.paste(fitted, (frame_x + frame_pad, frame_y + frame_pad))
    draw = ImageDraw.Draw(base)

    # --- Headline, centered, below the image — same marker-highlight style
    # as the other cards (white boxes, bold accent-red text) ---
    y = frame_y + frame_h + 30
    highlight_pad_x = 20
    headline_fallback_font = load_fallback_font(headline_font.size, bold=True)
    line_ys = []
    for line in headline_lines:
        visual = get_display(line)
        w = measure_mixed_text(draw, visual, headline_font, headline_fallback_font)
        box_left = (CARD_SIZE[0] - w) / 2 - highlight_pad_x
        box_right = (CARD_SIZE[0] + w) / 2 + highlight_pad_x
        draw.rounded_rectangle((box_left, y, box_right, y + highlight_h), radius=10, fill=WHITE)
        line_ys.append((y, w, visual))
        y += highlight_h + line_gap
    for y, w, visual in line_ys:
        draw_mixed_text(draw, ((CARD_SIZE[0] - w) / 2, y + highlight_v_pad), visual, headline_font, headline_fallback_font, fill=ACCENT)

    # --- Footer ---
    footer_w = measure_mixed_text(draw, footer_text, footer_font, footer_fallback_font)
    draw_mixed_text(draw, ((CARD_SIZE[0] - footer_w) / 2, CARD_SIZE[1] - 55),
                     footer_text, footer_font, footer_fallback_font, fill=(170, 170, 170))

    return base.convert("RGB")


def generate_text_card(title, historical_date):
    """Build a card for text-only posts (no tweet image): black background,
    white masthead, and the headline "marker-highlighted" — white boxes
    behind bold accent-red text — echoing the photo-card's highlight style."""
    base = Image.new("RGB", CARD_SIZE, (0, 0, 0)).convert("RGBA")
    draw = ImageDraw.Draw(base)

    # --- Centered masthead: logo + brand name + a rule, all in white ---
    logo_diameter = 92
    top_margin = 90
    logo_cx = CARD_SIZE[0] // 2
    logo_cy = top_margin + logo_diameter // 2
    paste_circular_logo(base, LOGO_PATH, logo_diameter, logo_cx, logo_cy)
    draw = ImageDraw.Draw(base)
    draw.ellipse(
        (logo_cx - logo_diameter // 2, logo_cy - logo_diameter // 2,
         logo_cx + logo_diameter // 2, logo_cy + logo_diameter // 2),
        outline=WHITE, width=3,
    )

    brand_font = load_hebrew_font(34, bold=True)
    brand_text = get_display("חדשות מלפני מאה")
    brand_w = draw.textlength(brand_text, font=brand_font)
    b_ascent, b_descent = brand_font.getmetrics()
    brand_y = logo_cy + logo_diameter // 2 + 26
    draw.text((logo_cx - brand_w / 2, brand_y), brand_text, font=brand_font, fill=WHITE)

    rule_y = brand_y + (b_ascent + b_descent) + 34
    draw.line((CARD_SIZE[0] // 2 - 50, rule_y, CARD_SIZE[0] // 2 + 50, rule_y), fill=WHITE, width=3)

    # --- Headline: large, bold, centered — each line "marker-highlighted"
    # in white, with bold accent-red text, like the photo-card style ---
    title = title.rstrip(": ")
    side_pad = 100
    measure_draw = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    headline_font, headline_lines = fit_headline(
        measure_draw, title, max_width=CARD_SIZE[0] - 2 * side_pad, max_lines=6, start_size=80, min_size=42
    )
    h_ascent, h_descent = headline_font.getmetrics()
    highlight_v_pad, line_gap = 14, 10
    highlight_h = (h_ascent + h_descent) + 2 * highlight_v_pad
    total_h = highlight_h * len(headline_lines) + line_gap * (len(headline_lines) - 1)

    area_top, area_bottom = rule_y + 50, CARD_SIZE[1] - 110
    y = area_top + max(0, (area_bottom - area_top - total_h)) // 2
    highlight_pad_x = 20
    headline_fallback_font = load_fallback_font(headline_font.size, bold=True)
    line_ys = []
    for line in headline_lines:
        visual = get_display(line)
        w = measure_mixed_text(draw, visual, headline_font, headline_fallback_font)
        box_left = (CARD_SIZE[0] - w) / 2 - highlight_pad_x
        box_right = (CARD_SIZE[0] + w) / 2 + highlight_pad_x
        draw.rounded_rectangle((box_left, y, box_right, y + highlight_h), radius=12, fill=WHITE)
        line_ys.append((y, w, visual))
        y += highlight_h + line_gap
    for y, w, visual in line_ys:
        draw_mixed_text(draw, ((CARD_SIZE[0] - w) / 2, y + highlight_v_pad), visual, headline_font, headline_fallback_font, fill=ACCENT)

    # --- Footer ---
    footer_font = load_hebrew_font(24, bold=True)
    footer_fallback_font = load_fallback_font(24, bold=True)
    footer_text = get_display(f"{format_hebrew_date(historical_date)}  •  milifney100.com")
    footer_w = measure_mixed_text(draw, footer_text, footer_font, footer_fallback_font)
    draw_mixed_text(draw, ((CARD_SIZE[0] - footer_w) / 2, CARD_SIZE[1] - 70),
                     footer_text, footer_font, footer_fallback_font, fill=(170, 170, 170))

    return base.convert("RGB")








def parse_post_front_matter(filepath):
    """Extract title, date, image, and tweet_id from a post's front matter."""
    content = filepath.read_text(encoding="utf-8")
    lines = content.split("\n")

    meta = {}
    in_front = False
    for line in lines:
        if line.strip() == "---":
            if not in_front:
                in_front = True
                continue
            break
        if in_front and ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip().strip('"').strip("'").replace('\\"', '"')

    return {
        "title": meta.get("title", ""),
        "date": meta.get("date", ""),
        "image": meta.get("image", ""),
        "tweet_id": meta.get("tweet_id", ""),
    }
