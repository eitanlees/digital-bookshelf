from datetime import date

import pytest

from digital_bookshelf import ExportFormatError, Status, detect_format, filter_books, load_books
from digital_bookshelf.parsers import _dates_in, clean_isbn, split_authors


def by_title(books):
    return {b.title: b for b in books}


# -- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [("storygraph.csv", "storygraph"), ("goodreads.csv", "goodreads"), ("notion.csv", "csv")],
)
def test_detects_format(fixtures, name, expected):
    assert {b.source for b in load_books(fixtures / name)} == {expected}


def test_detect_format_from_headers():
    assert detect_format(["Name", "Author", "Date Finished"]) == "csv"
    assert detect_format(["Book"]) == "csv"
    with pytest.raises(ExportFormatError, match="title column"):
        detect_format(["foo", "bar"])


def test_explicit_unknown_format(fixtures):
    with pytest.raises(ExportFormatError, match="Unknown format"):
        load_books(fixtures / "notion.csv", "librarything")


def test_empty_file(tmp_path):
    path = tmp_path / "empty.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ExportFormatError, match="empty"):
        load_books(path)


# -- StoryGraph ----------------------------------------------------------------


def test_storygraph(fixtures):
    books = by_title(load_books(fixtures / "storygraph.csv"))
    assert len(books) == 7

    orchard = books["The Glass Orchard"]
    assert orchard.authors == ["Iris Calloway"]
    assert orchard.isbn == "9780000000002"
    assert orchard.status is Status.READ
    assert orchard.finished == [date(2025, 11, 13)]

    assert books["Salt, Smoke, and Sugar"].isbn == "0000000027"
    assert books["A Book I Gave Up On"].status is Status.DNF
    assert books["Undated Favourite"].finished == []
    assert books["No ISBN Here: A Subtitle"].isbn is None


def test_storygraph_rereads_keep_every_finish_date(fixtures):
    book = by_title(load_books(fixtures / "storygraph.csv"))["The Twice-Read Book"]
    assert book.finished == [date(2025, 2, 26), date(2026, 1, 10)]
    assert book.finished_in(2025) and book.finished_in(2026)


# -- Goodreads -----------------------------------------------------------------


def test_goodreads(fixtures):
    books = by_title(load_books(fixtures / "goodreads.csv"))
    garden = books["The Clockmaker's Garden"]
    assert garden.isbn == "9780000000064"  # prefers ISBN13, strips ="..."
    assert garden.finished == [date(2025, 11, 13)]

    trains = books["Night Trains (Rails, #1)"]
    assert trains.isbn is None
    assert trains.authors == ["Rosa Bell", "Sam Teller", "Uma Vance"]

    assert books["Someday Maybe"].status is Status.TO_READ
    assert books["Halfway There"].status is Status.READING


# -- Generic CSV (Notion, spreadsheets, ...) ------------------------------------


def test_notion_style_export(fixtures):
    books = by_title(load_books(fixtures / "notion.csv"))  # file has a UTF-8 BOM
    assert len(books) == 7
    assert books["The Lighthouse Ledger"].status is Status.READING
    assert books["Maps of Nowhere"].status is Status.TO_READ
    assert books["An Unfinished Symphony"].status is Status.DNF

    kitchen = books["Small Gods of the Kitchen"]
    assert kitchen.status is Status.READ
    assert kitchen.authors == ["Dev Patel-Ruiz", "Emma Stone-Wright"]
    assert kitchen.finished == [date(2025, 12, 23)]
    assert kitchen.isbn is None


def write_csv(tmp_path, text):
    path = tmp_path / "books.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_csv_alternate_columns(tmp_path):
    path = write_csv(
        tmp_path,
        "Name,Authors,Date Finished,ISBN\n"
        'A Book,Some One,"May 2, 2025 → May 9, 2025",978-0-00-000000-2\n'
        "Unread Book,Someone Else,,\n",
    )
    first, second = load_books(path)
    assert first.title == "A Book"
    assert first.finished == [date(2025, 5, 9)]
    assert first.isbn == "9780000000002"
    assert first.status is Status.READ
    assert second.status is Status.READ  # no status column: everything was read
    assert second.finished == []


def test_csv_with_only_titles_and_authors(tmp_path):
    path = write_csv(tmp_path, "Book,Author\nDune,Frank Herbert\nEmma,Jane Austen\n")
    books = filter_books(load_books(path))  # default filter keeps "read" books
    assert [b.title for b in books] == ["Dune", "Emma"]


def test_csv_empty_status_cell(tmp_path):
    path = write_csv(
        tmp_path,
        "Title,Status,Date Read\nFinished,,2025-03-01\nUnknown,,\nQueued,TBR,\n",
    )
    statuses = {b.title: b.status for b in load_books(path)}
    assert statuses == {"Finished": Status.READ, "Unknown": Status.OTHER, "Queued": Status.TO_READ}


def test_csv_year_only_column(tmp_path):
    path = write_csv(tmp_path, "Title,Author,Year Read\nDune,Frank Herbert,2024\n")
    [book] = load_books(path)
    assert book.finished_in(2024)


# -- filtering -----------------------------------------------------------------


def test_filter_by_year_and_status(fixtures):
    books = load_books(fixtures / "storygraph.csv")
    titles = [b.title for b in filter_books(books, year=2025)]
    assert titles == [
        "The Glass Orchard",
        "Winter Tenants",
        "The Twice-Read Book",
        "No ISBN Here: A Subtitle",
    ]
    assert len(filter_books(books, statuses=None)) == 7
    assert [b.title for b in filter_books(books, statuses=[Status.DNF])] == ["A Book I Gave Up On"]


def test_csv_year_filter_counts_by_finish_date(fixtures):
    books = load_books(fixtures / "notion.csv")
    assert {b.title for b in filter_books(books, year=2025)} == {
        "Small Gods of the Kitchen",
        "The Long Quiet",
        "Counting Sheep: A History of Sleep",
    }


# -- field helpers -------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Alan Moore, Dave Gibbons", ["Alan Moore", "Dave Gibbons"]),
        ("Neil Smith (Translator)", ["Neil Smith"]),
        ("Martin Luther King, Jr.", ["Martin Luther King, Jr."]),
        ("", []),
    ],
)
def test_split_authors(text, expected):
    assert split_authors(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('="9780446675505"', "9780446675505"),
        ('="080708369X"', "080708369X"),
        ("978-0-446-67550-5", "9780446675505"),
        ('=""', None),
        ("storygraph-uid-123", None),
        (None, None),
    ],
)
def test_clean_isbn(text, expected):
    assert clean_isbn(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025/11/13", [date(2025, 11, 13)]),
        ("December 23, 2025", [date(2025, 12, 23)]),
        ("Sept 3, 2025", [date(2025, 9, 3)]),
        ("12/23/2025", [date(2025, 12, 23)]),
        ("2025-12-23", [date(2025, 12, 23)]),
        ("", []),
    ],
)
def test_dates_in(text, expected):
    assert _dates_in(text) == expected


def test_date_ranges_keep_the_end():
    text = "2025/02/24-2025/02/26, 2026/01/02-2026/01/10"
    assert _dates_in(text, last_of_each_range=True) == [date(2025, 2, 26), date(2026, 1, 10)]
