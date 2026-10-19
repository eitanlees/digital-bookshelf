"""Turn a StoryGraph, Goodreads, or any CSV reading log into a grid of book covers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("digital-bookshelf")
except PackageNotFoundError:  # pragma: no cover - running from a source checkout
    __version__ = "0.0.0"

from .covers import CoverFinder, CoverResult
from .models import Book, Status
from .parsers import ExportFormatError, detect_format, filter_books, load_books
from .render import RenderOptions, render_bookshelf

__all__ = [
    "Book",
    "CoverFinder",
    "CoverResult",
    "ExportFormatError",
    "RenderOptions",
    "Status",
    "__version__",
    "detect_format",
    "filter_books",
    "load_books",
    "render_bookshelf",
]
