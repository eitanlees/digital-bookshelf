"""Readers for reading-log CSV exports.

Supported formats:

* **StoryGraph** - "Manage Account → Export StoryGraph Library".
* **Goodreads** - "My Books → Import and export → Export Library".
* **Any other CSV** (``"csv"``) - a book log you keep yourself, exported
  from Notion, Airtable, Google Sheets, Excel, and so on. There is no fixed
  schema; this reader looks for commonly named columns (``Title``, ``Author``,
  ``Status``, ``Date Finished`` ...). Only a title column is required.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime
from pathlib import Path

from .models import Book, Status

FORMATS = ("storygraph", "goodreads", "csv")
#: Older names still accepted for ``fmt`` ("notion" was the generic format in 0.1.0).
FORMAT_ALIASES = {"notion": "csv", "generic": "csv"}

Row = dict[str, str]


class ExportFormatError(ValueError):
    """Raised when an export cannot be read or its format cannot be determined."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_books(path: str | Path, fmt: str = "auto") -> list[Book]:
    """Read every book from a CSV export.

    ``fmt`` is one of ``"auto"``, ``"storygraph"``, ``"goodreads"`` or
    ``"csv"`` (any other book log). With ``"auto"`` the format is detected from
    the CSV header.
    """
    headers, rows = _read_csv(Path(path))
    fmt = FORMAT_ALIASES.get(fmt, fmt)
    if fmt == "auto":
        fmt = detect_format(headers)
    try:
        parser = _PARSERS[fmt]
    except KeyError:
        raise ExportFormatError(
            f"Unknown format {fmt!r}; expected one of: auto, {', '.join(FORMATS)}"
        ) from None
    return [book for row in rows if (book := parser(row)) is not None]


def detect_format(headers: Iterable[str]) -> str:
    """Guess the export format from a CSV header row."""
    keys = {_key(h) for h in headers}
    if {"read status", "isbn/uid"} <= keys:
        return "storygraph"
    if {"book id", "exclusive shelf"} <= keys:
        return "goodreads"
    if keys & set(_TITLE_COLUMNS):
        return "csv"
    raise ExportFormatError(
        "Could not find a title column in this CSV. Name it 'Title' (or 'Name' or "
        "'Book'); 'Author', 'Status', and 'Date Finished' columns are also used if present."
    )


def filter_books(
    books: Iterable[Book],
    *,
    year: int | None = None,
    statuses: Iterable[Status] | None = (Status.READ,),
) -> list[Book]:
    """Keep books with a matching status that (optionally) were finished in ``year``."""
    allowed = set(statuses) if statuses is not None else None
    result = []
    for book in books:
        if allowed is not None and book.status not in allowed:
            continue
        if year is not None and not book.finished_in(year):
            continue
        result.append(book)
    return result


# ---------------------------------------------------------------------------
# Format-specific parsers
# ---------------------------------------------------------------------------


def parse_storygraph_row(row: Row) -> Book | None:
    title = _get(row, "title")
    if not title:
        return None
    finished = _dates_in(_get(row, "dates read"), last_of_each_range=True)
    finished += _dates_in(_get(row, "last date read"))
    return Book(
        title=title,
        authors=split_authors(_get(row, "authors")),
        isbn=clean_isbn(_get(row, "isbn/uid")),
        status=_status_from_text(_get(row, "read status")),
        finished=sorted(set(finished)),
        source="storygraph",
    )


def parse_goodreads_row(row: Row) -> Book | None:
    title = _get(row, "title")
    if not title:
        return None
    authors = [_get(row, "author")] if _get(row, "author") else []
    authors += split_authors(_get(row, "additional authors"))
    return Book(
        title=title,
        authors=authors,
        isbn=clean_isbn(_get(row, "isbn13")) or clean_isbn(_get(row, "isbn")),
        status=_status_from_text(_get(row, "exclusive shelf")),
        finished=_dates_in(_get(row, "date read")),
        source="goodreads",
    )


# Column names recognised in a generic CSV, matched case-insensitively. The first
# non-empty one wins.
_TITLE_COLUMNS = ("title", "name", "book", "book title", "book name")
_AUTHOR_COLUMNS = ("author", "authors", "author(s)", "author name", "writer", "by")
_STATUS_COLUMNS = ("status", "read status", "reading status", "state", "shelf")
_FINISH_COLUMNS = (
    "date finished", "finished", "finish", "finish date", "finished on", "date read",
    "read date", "read on", "date completed", "completed", "end date", "end", "date",
)  # fmt: skip
_YEAR_COLUMNS = ("year read", "year finished", "read in")
_ISBN_COLUMNS = ("isbn", "isbn13", "isbn 13", "isbn-13", "isbn10", "isbn 10", "isbn-10")


def parse_generic_row(row: Row) -> Book | None:
    """Parse a row from any book log with recognisable column names."""
    title = _first(row, _TITLE_COLUMNS)
    if not title:
        return None
    finished = _dates_in(_first(row, _FINISH_COLUMNS), last_of_each_range=True)
    if not finished and (year := re.fullmatch(r"\d{4}", _first(row, _YEAR_COLUMNS))):
        # Only the year is known; Jan 1 keeps it in the right year for --year.
        finished = [date(int(year[0]), 1, 1)]

    if status_text := _first(row, _STATUS_COLUMNS):
        status = _status_from_text(status_text)
    elif any(key in row for key in _STATUS_COLUMNS):
        status = Status.READ if finished else Status.OTHER
    else:
        # A plain list with no status column is a list of books read.
        status = Status.READ

    return Book(
        title=title,
        authors=split_authors(_first(row, _AUTHOR_COLUMNS)),
        isbn=clean_isbn(_first(row, _ISBN_COLUMNS)),
        status=status,
        finished=finished,
        source="csv",
    )


_PARSERS: dict[str, Callable[[Row], Book | None]] = {
    "storygraph": parse_storygraph_row,
    "goodreads": parse_goodreads_row,
    "csv": parse_generic_row,
}


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "ph.d.", "md", "m.d."}


def split_authors(text: str) -> list[str]:
    """Split ``"Alan Moore, Dave Gibbons"`` into names, keeping "Jr."-style suffixes.

    Role annotations such as ``"Neil Smith (Translator)"`` are dropped.
    """
    names: list[str] = []
    for part in re.split(r"\s*[,;]\s*", text or ""):
        part = re.sub(r"\s*\([^)]*\)\s*$", "", part).strip()
        if not part:
            continue
        if names and part.lower() in _SUFFIXES:
            names[-1] = f"{names[-1]}, {part}"
        else:
            names.append(part)
    return names


def clean_isbn(text: str | None) -> str | None:
    """Normalise an ISBN-10/13, handling Goodreads' ``="978..."`` quoting."""
    if not text:
        return None
    digits = re.sub(r"[^0-9Xx]", "", text).upper()
    if len(digits) == 13 and digits.isdigit():
        return digits
    if len(digits) == 10 and digits[:9].isdigit():
        return digits
    return None


_DATE_PATTERNS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], date]]] = []


def _pattern(regex: str):
    def register(func: Callable[[re.Match[str]], date]):
        _DATE_PATTERNS.append((re.compile(regex, re.IGNORECASE), func))
        return func

    return register


@_pattern(r"\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b")
def _ymd(m: re.Match[str]) -> date:
    return date(int(m[1]), int(m[2]), int(m[3]))


@_pattern(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
def _mdy(m: re.Match[str]) -> date:
    return date(int(m[3]), int(m[1]), int(m[2]))


@_pattern(r"\b([A-Z][a-z]+)\.? (\d{1,2}), (\d{4})\b")
def _month_name(m: re.Match[str]) -> date:
    month = m[1][:3].title()
    return datetime.strptime(f"{month} {m[2]} {m[3]}", "%b %d %Y").date()


def _dates_in(text: str, *, last_of_each_range: bool = False) -> list[date]:
    """Extract dates from ``text``.

    Handles single dates and ranges such as ``"2025/11/08-2025/11/13"`` or
    ``"May 2, 2025 → May 9, 2025"``. With ``last_of_each_range`` only the end of
    each comma-separated range is kept (that is the date the book was finished).
    """
    if not text or not text.strip():
        return []
    # Comma-separated ranges are used by StoryGraph for re-reads. Month-name
    # dates contain a comma themselves, so only split when it is unambiguous.
    has_month_names = re.search(r"[A-Za-z]{3,}", text) is not None
    chunks = [text] if has_month_names else text.split(",")
    found: list[date] = []
    for chunk in chunks:
        matches: list[tuple[int, date]] = []
        for regex, build in _DATE_PATTERNS:
            for m in regex.finditer(chunk):
                try:
                    matches.append((m.start(), build(m)))
                except ValueError:
                    continue
        matches.sort(key=lambda item: item[0])
        dates = [d for _, d in matches]
        if last_of_each_range and dates:
            found.append(dates[-1])
        else:
            found.extend(dates)
    return sorted(set(found))


def _status_from_text(text: str) -> Status:
    s = (text or "").strip().lower().replace("_", "-")
    if not s:
        return Status.OTHER
    if any(k in s for k in ("did-not-finish", "did not finish", "dnf", "stop", "abandon", "quit")):
        return Status.DNF
    if any(k in s for k in ("want", "to-read", "to read", "tbr", "wishlist", "queue", "up next")):
        return Status.TO_READ
    if any(k in s for k in ("currently", "reading", "in progress", "started", "paused")):
        return Status.READING
    if s in {"read", "finished", "done", "complete", "completed"} or s.startswith("read"):
        return Status.READ
    return Status.OTHER


def _key(header: str) -> str:
    return header.strip().strip("﻿").strip().lower()


def _get(row: Row, key: str) -> str:
    return (row.get(key) or "").strip()


def _first(row: Row, keys: Iterable[str]) -> str:
    for key in keys:
        if value := _get(row, key):
            return value
    return ""


def _read_csv(path: Path) -> tuple[list[str], list[Row]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            headers = next(reader, None)
            if not headers:
                raise ExportFormatError(f"{path} is empty")
            keys = [_key(h) for h in headers]
            rows = [
                dict(zip(keys, values, strict=False))
                for values in reader
                if any(v.strip() for v in values)
            ]
    except UnicodeDecodeError as exc:
        raise ExportFormatError(f"{path} is not a UTF-8 CSV file") from exc
    except csv.Error as exc:
        raise ExportFormatError(f"{path} is not a valid CSV file: {exc}") from exc
    return headers, rows
