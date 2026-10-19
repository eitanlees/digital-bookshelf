import pytest
from PIL import Image

from digital_bookshelf import Book, CoverResult, RenderOptions, render_bookshelf
from digital_bookshelf.render import auto_columns, placeholder_cover


def cover_file(tmp_path, name, size, color):
    path = tmp_path / f"{name}.png"
    Image.new("RGB", size, color).save(path)
    return path


@pytest.fixture
def results(tmp_path):
    red = cover_file(tmp_path, "red", (200, 300), (255, 0, 0))
    wide = cover_file(tmp_path, "wide", (300, 300), (0, 0, 255))
    return [
        CoverResult(Book("Red Book", ["A"]), red, "test"),
        CoverResult(Book("Square Book", ["B"]), wide, "test"),
        CoverResult(Book("No Cover", ["C"]), None, None),
    ]


def test_auto_columns():
    assert auto_columns(0, 1.5) == 1
    assert auto_columns(1, 1.5) == 1
    assert auto_columns(20, 1.5) == 6
    assert auto_columns(64, 1.5) == 10


def test_grid_dimensions(results):
    opts = RenderOptions(columns=2, cover_width=100, gap=10, margin=20, shadow=False)
    img = render_bookshelf(results, opts)
    # 2 columns x 2 rows of 100x150 cells
    assert img.size == (20 + 100 + 10 + 100 + 20, 20 + 150 + 10 + 150 + 20)
    # The red cover fills the first cell (sampled from its centre).
    assert img.getpixel((20 + 50, 20 + 75)) == (255, 0, 0)


def test_contain_vs_fill(results):
    base = dict(columns=2, cover_width=100, gap=10, margin=20, shadow=False, background="#ffffff")
    contained = render_bookshelf(results, RenderOptions(**base))
    filled = render_bookshelf(results, RenderOptions(**base, fit="fill"))
    # The square cover sits on the baseline, so the top of its cell is background...
    top_of_square = (20 + 100 + 10 + 50, 20 + 10)
    assert contained.getpixel(top_of_square) == (255, 255, 255)
    # ...unless it's cropped to fill the cell.
    assert filled.getpixel(top_of_square) == (0, 0, 255)


def test_heading_adds_height(results):
    plain = render_bookshelf(results, RenderOptions(cover_width=100))
    titled = render_bookshelf(results, RenderOptions(cover_width=100, title="2025", subtitle="3"))
    assert titled.height > plain.height
    assert titled.width == plain.width


def test_shelf_style(results):
    img = render_bookshelf(results, RenderOptions(style="shelf", cover_width=100, columns=3))
    assert img.mode == "RGB"


def test_accepts_bare_books():
    img = render_bookshelf([Book("Only a Title")], RenderOptions(cover_width=80))
    assert img.width > 80


def test_unreadable_cover_falls_back_to_placeholder(tmp_path):
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    result = CoverResult(Book("Broken"), bad, "test")
    render_bookshelf([result], RenderOptions(cover_width=80))


def test_placeholder_cover_size():
    img = placeholder_cover(Book("A Very Long Title That Needs Wrapping", ["Author"]), 120, 180)
    assert img.size == (120, 180)


@pytest.mark.parametrize(
    "kwargs",
    [{"style": "spiral"}, {"fit": "stretch"}, {"columns": 0}, {"cover_width": 5}],
)
def test_invalid_options(kwargs):
    with pytest.raises(ValueError):
        RenderOptions(**kwargs)
