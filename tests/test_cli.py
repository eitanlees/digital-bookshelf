import pytest
from PIL import Image

from digital_bookshelf import covers as covers_mod
from digital_bookshelf.cli import main


@pytest.fixture
def offline(monkeypatch):
    """Every lookup comes back empty, so all books get placeholders."""
    monkeypatch.setattr(covers_mod, "get_json", lambda url: {})
    monkeypatch.setattr(covers_mod, "http_get", lambda url, **kw: None)
    monkeypatch.setattr(covers_mod.AppleBooks, "min_interval", 0)


def test_list(fixtures, capsys):
    assert main(["list", str(fixtures / "storygraph.csv"), "--year", "2025"]) == 0
    out, err = capsys.readouterr()
    lines = out.strip().splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("2025-02-11  Winter Tenants")
    assert "skipped 1 book(s) with no finish date: Undated Favourite" in err


def test_list_sort_and_status(fixtures, capsys):
    main(["list", str(fixtures / "notion.csv"), "--status", "all", "--sort", "title"])
    out = capsys.readouterr().out
    titles = [line.split("  ")[1] for line in out.strip().splitlines()]
    assert len(titles) == 7
    assert titles[0] == "Counting Sheep: A History of Sleep"


def test_list_rereads_sort_by_finish_in_selected_year(fixtures, capsys):
    main(["list", str(fixtures / "storygraph.csv"), "-y", "2025", "--sort", "finished"])
    out = capsys.readouterr().out
    titles = [line.split("  ")[1] for line in out.strip().splitlines()]
    assert titles.index("The Twice-Read Book") == 1  # Feb 2025, not its 2026 re-read
    assert "2025-02-26  The Twice-Read Book" in out


def test_render(fixtures, tmp_path, offline, capsys):
    out = tmp_path / "shelf.png"
    code = main(
        [
            "render",
            str(fixtures / "notion.csv"),
            "-y", "2025",
            "-o", str(out),
            "--title", "{year} in books",
            "--subtitle", "{count} books",
            "--cache-dir", str(tmp_path / "cache"),
            "--cover-width", "60",
        ]
    )  # fmt: skip
    assert code == 0
    assert Image.open(out).size[0] > 60
    err = capsys.readouterr().err
    assert "3 book(s) have no cover" in err
    assert "small-gods-of-the-kitchen-dev-patel-ruiz.jpg" in err


def test_render_multiple_exports_dedupes(fixtures, tmp_path, offline, capsys):
    out = tmp_path / "both.jpg"
    args = ["render", str(fixtures / "goodreads.csv"), str(fixtures / "goodreads.csv")]
    assert main([*args, "-o", str(out), "--cache-dir", str(tmp_path / "c")]) == 0
    assert "2 books" in capsys.readouterr().err


def test_render_nothing_matched(fixtures, tmp_path, capsys):
    assert main(["render", str(fixtures / "notion.csv"), "-y", "1999"]) == 1


def test_covers_command(fixtures, tmp_path, monkeypatch, capsys):
    from .conftest import make_image_bytes

    monkeypatch.setattr(covers_mod, "get_json", lambda url: {})
    monkeypatch.setattr(covers_mod, "http_get", lambda url, **kw: make_image_bytes())
    out = tmp_path / "covers"
    args = ["covers", str(fixtures / "storygraph.csv"), "-y", "2025", "-o", str(out)]
    assert main([*args, "--cache-dir", str(tmp_path / "c")]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert "the-glass-orchard-iris-calloway.jpg" in names
    assert len(names) == 3  # the no-ISBN book had no search hits


def test_bad_file(tmp_path, capsys):
    path = tmp_path / "x.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    assert main(["list", str(path)]) == 2
    assert "Could not find a title column" in capsys.readouterr().err


def test_missing_file(tmp_path, capsys):
    assert main(["list", str(tmp_path / "nope.csv")]) == 2


def test_dedupes_across_exports_with_different_subtitles(tmp_path, capsys):
    a = tmp_path / "a.csv"
    a.write_text("Title,Author,Status\nConscious,Annaka Harris,Read\n", encoding="utf-8")
    b = tmp_path / "b.csv"
    b.write_text(
        "Name,Author,Status\nConscious: A Brief Guide,Annaka  Harris,Read\n", encoding="utf-8"
    )
    main(["list", str(a), str(b)])
    assert len(capsys.readouterr().out.strip().splitlines()) == 1


def test_format_flag_accepts_notion_alias(fixtures, capsys):
    assert main(["list", str(fixtures / "notion.csv"), "--format", "notion"]) == 0
    assert main(["list", str(fixtures / "notion.csv"), "--format", "csv"]) == 0
