"""Command-line interface: ``bookshelf render``, ``bookshelf list``, ``bookshelf covers``."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from . import __version__
from .covers import (
    DEFAULT_SOURCES,
    PROVIDERS,
    CoverFinder,
    CoverResult,
    last_name,
    normalize,
    short_title,
)
from .models import Book, Status
from .parsers import FORMATS, ExportFormatError, filter_books, load_books
from .render import FITS, STYLES, RenderOptions, render_bookshelf

SORTS = ("finished", "title", "author", "none")
STATUS_CHOICES = [s.value for s in Status] + ["all"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stderr, "reconfigure"):
        # Progress marks (✓/✗) must not crash on consoles without UTF-8.
        sys.stderr.reconfigure(errors="replace")
    try:
        return args.func(args)
    except (ExportFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_render(args: argparse.Namespace) -> int:
    books = _selected_books(args)
    if not books:
        print("No books matched; nothing to render.", file=sys.stderr)
        return 1
    results = _find_covers(args, books)

    context = {"count": len(books), "year": args.year or ""}
    options = RenderOptions(
        columns=args.columns,
        cover_width=args.cover_width,
        gap=args.gap,
        margin=args.margin,
        background=args.background,
        style=args.style,
        fit=args.fit,
        shadow=not args.no_shadow,
        title=args.title.format(**context) if args.title else None,
        subtitle=args.subtitle.format(**context) if args.subtitle else None,
        font=args.font,
    )
    image = render_bookshelf(results, options)

    output = args.output or Path(f"bookshelf-{args.year}.png" if args.year else "bookshelf.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() in {".jpg", ".jpeg"}:
        image.save(output, quality=92)
    else:
        image.save(output)
    print(f"Saved {output} ({image.width}x{image.height}, {len(books)} books)", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    books = _selected_books(args)
    for book in books:
        when = _finish_for_sort(book, args.year)
        finished = when.isoformat() if when != date.max else "----------"
        isbn = book.isbn or "-"
        print(f"{finished}  {book.title}  —  {book.author or '?'}  [isbn {isbn}]  ({book.slug})")
    print(f"\n{len(books)} book(s)", file=sys.stderr)
    return 0


def cmd_covers(args: argparse.Namespace) -> int:
    books = _selected_books(args)
    results = _find_covers(args, books)
    args.output.mkdir(parents=True, exist_ok=True)
    copied = 0
    for result in results:
        if result.path and result.source != "override":
            shutil.copyfile(result.path, args.output / f"{result.book.slug}{result.path.suffix}")
            copied += 1
    print(f"Copied {copied} cover(s) to {args.output}/", file=sys.stderr)
    print(
        "Replace any you don't like (keep the file names), add missing ones, then run:\n"
        f"  bookshelf render ... --covers {args.output}",
        file=sys.stderr,
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _selected_books(args: argparse.Namespace) -> list[Book]:
    books: list[Book] = []
    for path in args.exports:
        books.extend(load_books(path, args.format))

    wanted = args.status or [Status.READ.value]
    statuses = None if "all" in wanted else [Status(s) for s in wanted]
    selected = filter_books(books, year=args.year, statuses=statuses)

    if args.year is not None:
        undated = [
            b
            for b in filter_books(books, statuses=statuses)
            if not b.finished and b not in selected
        ]
        if undated:
            names = "; ".join(str(b) for b in undated[:5])
            more = f" and {len(undated) - 5} more" if len(undated) > 5 else ""
            print(
                f"note: skipped {len(undated)} book(s) with no finish date: {names}{more}",
                file=sys.stderr,
            )

    selected = _dedupe(selected)
    if args.sort == "finished":
        selected.sort(key=lambda b: _finish_for_sort(b, args.year))
    elif args.sort == "title":
        selected.sort(key=lambda b: re.sub(r"^(the|an?) ", "", b.title.casefold()))
    elif args.sort == "author":
        selected.sort(key=lambda b: (b.author.split()[-1].lower() if b.author else "", b.title))
    if args.reverse:
        selected.reverse()
    return selected


def _finish_for_sort(book: Book, year: int | None) -> date:
    dates = [d for d in book.finished if year is None or d.year == year]
    return dates[-1] if dates else date.max


def _dedupe(books: list[Book]) -> list[Book]:
    seen: set[tuple[str, str]] = set()
    unique = []
    for book in books:
        # Exports disagree on subtitles and initials, so compare loosely.
        key = (normalize(short_title(book.title)), last_name(book.author))
        if key not in seen:
            seen.add(key)
            unique.append(book)
    return unique


def _find_covers(args: argparse.Namespace, books: list[Book]) -> list[CoverResult]:
    finder = CoverFinder(
        cache_dir=args.cache_dir,
        overrides=args.covers,
        sources=args.sources,
        refresh=args.refresh,
    )
    total = len(books)
    done = 0

    def progress(result: CoverResult) -> None:
        nonlocal done
        done += 1
        if result.path:
            mark, note = "✓", result.source
        else:
            mark, note = "✗", result.error or "no cover found"
        print(
            f"[{done:>{len(str(total))}}/{total}] {mark} {result.book}  ({note})", file=sys.stderr
        )

    print(f"Looking up covers for {total} book(s)…", file=sys.stderr)
    results = finder.find_all(books, progress=progress)

    missing = [r for r in results if not r.path]
    if missing:
        where = args.covers or Path("covers")
        print(
            f"\n{len(missing)} book(s) have no cover and will get a placeholder. To supply your "
            f"own, save an image as {where}/<name>.jpg and pass --covers {where}:",
            file=sys.stderr,
        )
        for r in missing:
            print(f"  {r.book.slug}.jpg", file=sys.stderr)
        if any(r.error for r in missing):
            print("Some lookups failed due to network errors; re-run to retry.", file=sys.stderr)
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bookshelf",
        description="Turn a Goodreads, StoryGraph, or Notion reading log into a grid of covers.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    selection = argparse.ArgumentParser(add_help=False)
    group = selection.add_argument_group("choosing books")
    group.add_argument("exports", nargs="+", type=Path, help="CSV export file(s)")
    group.add_argument(
        "-y", "--year", type=int, help="only books finished in this year (e.g. 2025)"
    )
    group.add_argument(
        "--format",
        default="auto",
        choices=("auto", *FORMATS),
        help="export format (default: detect from the CSV header)",
    )
    group.add_argument(
        "--status",
        action="append",
        choices=STATUS_CHOICES,
        help="reading status to include; repeatable (default: read)",
    )
    group.add_argument(
        "--sort", default="finished", choices=SORTS, help="cover order (default: finished)"
    )
    group.add_argument("--reverse", action="store_true", help="reverse the order")

    lookup = argparse.ArgumentParser(add_help=False)
    group = lookup.add_argument_group("cover lookup")
    group.add_argument(
        "--covers",
        type=Path,
        metavar="DIR",
        help="folder of your own cover images named <slug>.jpg or <isbn>.jpg; these always win",
    )
    group.add_argument(
        "--sources",
        type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
        default=list(DEFAULT_SOURCES),
        metavar="LIST",
        help=f"comma-separated cover sources in priority order (default: "
        f"{','.join(DEFAULT_SOURCES)}; available: {', '.join(PROVIDERS)})",
    )
    group.add_argument(
        "--refresh", action="store_true", help="ignore cached lookups and search again"
    )
    group.add_argument("--cache-dir", type=Path, help="where downloaded covers are cached")

    # render
    p = sub.add_parser(
        "render",
        parents=[selection, lookup],
        help="build the bookshelf image",
        description="Look up covers and render them as one image.",
    )
    group = p.add_argument_group("output")
    group.add_argument(
        "-o", "--output", type=Path, help="image file to write (.png, .jpg, or .webp)"
    )
    group.add_argument("--title", help='heading text, e.g. "My {year} in Books"')
    group.add_argument("--subtitle", help='smaller text under the heading, e.g. "{count} books"')
    group.add_argument("--columns", type=int, help="covers per row (default: roughly square)")
    group.add_argument("--cover-width", type=int, default=300, help="pixels (default: 300)")
    group.add_argument("--gap", type=int, default=28, help="space between covers (default: 28)")
    group.add_argument("--margin", type=int, default=72, help="outer margin (default: 72)")
    group.add_argument("--background", default="#f4efe6", help="colour (default: #f4efe6)")
    group.add_argument("--style", default="grid", choices=STYLES, help="layout (default: grid)")
    group.add_argument(
        "--fit",
        default="contain",
        choices=FITS,
        help="contain = show whole covers, fill = crop to equal size (default: contain)",
    )
    group.add_argument("--no-shadow", action="store_true", help="don't draw drop shadows")
    group.add_argument("--font", type=Path, help="path to a .ttf/.otf font for text")
    p.set_defaults(func=cmd_render)

    # list
    p = sub.add_parser(
        "list",
        parents=[selection],
        help="show which books would be included",
        description="Print the books selected from the export(s), with their override names.",
    )
    p.set_defaults(func=cmd_list)

    # covers
    p = sub.add_parser(
        "covers",
        parents=[selection, lookup],
        help="download covers into a folder you can review and edit",
        description="Download covers into a folder (named <slug>.jpg) so you can swap out any "
        "you don't like, then render with --covers DIR.",
    )
    p.add_argument("-o", "--output", type=Path, default=Path("covers"), help="default: covers/")
    p.set_defaults(func=cmd_covers)

    return parser
