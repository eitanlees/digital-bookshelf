"""Arrange book covers into a single bookshelf image."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

from .covers import CoverResult
from .models import Book

STYLES = ("grid", "shelf")
FITS = ("contain", "fill")

# Muted cloth-binding colours for books without a cover image.
PLACEHOLDER_COLORS = (
    "#3d5a80", "#6b4e71", "#8c2f39", "#2f6f62", "#b5651d",
    "#4a4e69", "#7a6c5d", "#355070", "#6d597a", "#386641",
)  # fmt: skip

Font = ImageFont.FreeTypeFont | ImageFont.ImageFont


@dataclass
class RenderOptions:
    """Knobs for :func:`render_bookshelf`. All sizes are in pixels."""

    #: Number of covers per row. ``None`` picks a roughly square layout.
    columns: int | None = None
    #: Width of each cover slot; its height is ``cover_width * aspect``.
    cover_width: int = 300
    aspect: float = 1.5
    #: Space between covers, and around the edge of the image.
    gap: int = 28
    margin: int = 72
    background: str = "#f4efe6"
    #: ``"grid"`` for a plain grid, ``"shelf"`` to stand books on wooden shelves.
    style: str = "grid"
    #: ``"contain"`` shows each whole cover; ``"fill"`` crops covers to equal size.
    fit: str = "contain"
    shadow: bool = True
    title: str | None = None
    subtitle: str | None = None
    #: Path to a .ttf/.otf font for the heading and placeholder covers.
    font: str | Path | None = None

    def __post_init__(self) -> None:
        if self.style not in STYLES:
            raise ValueError(f"style must be one of {STYLES}, not {self.style!r}")
        if self.fit not in FITS:
            raise ValueError(f"fit must be one of {FITS}, not {self.fit!r}")
        if self.columns is not None and self.columns < 1:
            raise ValueError("columns must be at least 1")
        if self.cover_width < 20:
            raise ValueError("cover_width must be at least 20")


def auto_columns(count: int, aspect: float) -> int:
    """Pick a column count that makes the overall image roughly square."""
    if count <= 0:
        return 1
    return max(1, min(count, math.ceil(math.sqrt(count * aspect))))


def render_bookshelf(
    covers: Sequence[CoverResult | Book], options: RenderOptions | None = None
) -> Image.Image:
    """Render covers (in order) into one image.

    Items may be :class:`CoverResult` objects or bare :class:`Book` objects;
    books without a cover image get a generated placeholder showing the title.
    """
    opts = options or RenderOptions()
    items = [c if isinstance(c, CoverResult) else CoverResult(c, None, None) for c in covers]

    cell_w = opts.cover_width
    cell_h = round(cell_w * opts.aspect)
    cols = opts.columns or auto_columns(len(items), opts.aspect)
    cols = max(1, min(cols, len(items) or 1))
    rows = max(1, math.ceil(len(items) / cols))

    shelf = opts.style == "shelf"
    plank_h = max(10, round(cell_w * 0.09)) if shelf else 0
    h_gap = round(opts.gap * 0.6) if shelf else opts.gap
    row_pitch = cell_h + plank_h + opts.gap

    bg = ImageColor.getrgb(opts.background)
    ink = (34, 30, 26) if _luminance(bg) > 0.5 else (240, 236, 228)

    heading_font = _font(opts.font, round(cell_w * 0.26))
    sub_font = _font(opts.font, round(cell_w * 0.11))
    header_h = 0
    if opts.title:
        header_h += _text_height(heading_font, opts.title) + round(cell_w * 0.1)
    if opts.subtitle:
        header_h += _text_height(sub_font, opts.subtitle) + round(cell_w * 0.04)
    if header_h:
        header_h += round(opts.margin * 0.6)

    grid_w = cols * cell_w + (cols - 1) * h_gap
    width = grid_w + 2 * opts.margin
    height = header_h + rows * row_pitch - opts.gap + 2 * opts.margin
    canvas = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(canvas)

    # Heading
    y = opts.margin
    if opts.title:
        draw.text((width / 2, y), opts.title, font=heading_font, fill=ink, anchor="ma")
        y += _text_height(heading_font, opts.title) + round(cell_w * 0.1)
    if opts.subtitle:
        sub_ink = tuple(round(c * 0.6 + b * 0.4) for c, b in zip(ink, bg, strict=False))
        draw.text((width / 2, y), opts.subtitle, font=sub_font, fill=sub_ink, anchor="ma")

    top = opts.margin + header_h
    placements: list[tuple[Image.Image, tuple[int, int]]] = []
    for row in range(rows):
        row_items = items[row * cols : (row + 1) * cols]
        # Centre a short final row.
        row_w = len(row_items) * cell_w + (len(row_items) - 1) * h_gap
        x0 = opts.margin + (grid_w - row_w) // 2
        y0 = top + row * row_pitch
        for i, item in enumerate(row_items):
            cover = _cover_image(item, cell_w, cell_h, opts)
            x = x0 + i * (cell_w + h_gap) + (cell_w - cover.width) // 2
            y = y0 + cell_h - cover.height  # stand books on a common baseline
            placements.append((cover, (x, y)))

    if opts.shadow:
        _draw_shadows(canvas, placements, cell_w)
    if shelf:
        for row in range(rows):
            _draw_plank(canvas, opts.margin // 2, top + row * row_pitch + cell_h, plank_h)
    for cover, pos in placements:
        canvas.paste(cover, pos, _rounded_mask(cover.size, cell_w))
    return canvas


# ---------------------------------------------------------------------------
# Covers
# ---------------------------------------------------------------------------


def _cover_image(item: CoverResult, cell_w: int, cell_h: int, opts: RenderOptions) -> Image.Image:
    if item.path is not None:
        try:
            with Image.open(item.path) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                if opts.fit == "fill":
                    return ImageOps.fit(img, (cell_w, cell_h), Image.Resampling.LANCZOS)
                return ImageOps.contain(img, (cell_w, cell_h), Image.Resampling.LANCZOS)
        except OSError:
            pass
    return placeholder_cover(item.book, cell_w, cell_h, opts.font)


def placeholder_cover(
    book: Book, width: int, height: int, font: str | Path | None = None
) -> Image.Image:
    """A plain cloth-coloured cover with the title and author printed on it."""
    digest = int(hashlib.md5(book.title.encode()).hexdigest(), 16)
    color = ImageColor.getrgb(PLACEHOLDER_COLORS[digest % len(PLACEHOLDER_COLORS)])
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    light = (245, 240, 230)

    pad = round(width * 0.1)
    rule = max(1, round(width * 0.008))
    draw.rectangle(
        (pad // 2, pad // 2, width - pad // 2, height - pad // 2), outline=light, width=rule
    )

    title_font = _font(font, round(width * 0.11))
    author_font = _font(font, round(width * 0.075))
    title_lines = _wrap(book.title, title_font, width - 2 * pad)[:6]
    author_lines = _wrap(book.author, author_font, width - 2 * pad)[:2] if book.author else []

    line_h = _text_height(title_font, "Ag") * 1.25
    y = height * 0.18
    for line in title_lines:
        draw.text((width / 2, y), line, font=title_font, fill=light, anchor="ma")
        y += line_h
    a_line_h = _text_height(author_font, "Ag") * 1.3
    y = height - pad - a_line_h * len(author_lines)
    for line in author_lines:
        draw.text((width / 2, y), line, font=author_font, fill=light, anchor="ma")
        y += a_line_h
    return img


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------


def _draw_shadows(canvas, placements, cell_w: int) -> None:
    offset = max(2, round(cell_w * 0.015))
    blur = max(2, round(cell_w * 0.025))
    mask = Image.new("L", canvas.size, 0)
    mdraw = ImageDraw.Draw(mask)
    for cover, (x, y) in placements:
        mdraw.rectangle(
            (x + offset, y + offset, x + cover.width + offset, y + cover.height + offset), 110
        )
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    canvas.paste((0, 0, 0), (0, 0), mask)


def _draw_plank(canvas: Image.Image, inset: int, y: int, plank_h: int) -> None:
    draw = ImageDraw.Draw(canvas)
    x0, x1 = inset, canvas.width - inset
    wood, edge, dark = (139, 94, 60), (171, 122, 82), (92, 60, 36)
    draw.rectangle((x0, y, x1, y + plank_h), fill=wood)
    draw.rectangle((x0, y, x1, y + max(2, plank_h // 4)), fill=edge)
    draw.rectangle((x0, y + plank_h - max(2, plank_h // 6), x1, y + plank_h), fill=dark)


def _rounded_mask(size: tuple[int, int], cell_w: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    radius = max(1, round(cell_w * 0.012))
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def _font(path: str | Path | None, size: int) -> Font:
    size = max(8, size)
    if path:
        return ImageFont.truetype(str(path), size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow built without FreeType
        return ImageFont.load_default()


def _text_height(font: Font, text: str) -> int:
    """Height of ``text`` drawn with an ascender anchor ("ma"), as used here."""
    return int(font.getbbox(text, anchor="la")[3])


def _wrap(text: str, font: Font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _luminance(rgb: tuple[int, ...]) -> float:
    r, g, b = rgb[:3]
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
