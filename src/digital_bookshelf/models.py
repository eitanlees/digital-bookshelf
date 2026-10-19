"""Core data types shared by the parsers, cover lookup, and renderer."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Status(str, Enum):
    """Normalized reading status across all supported export formats."""

    READ = "read"
    READING = "reading"
    TO_READ = "to-read"
    DNF = "dnf"
    OTHER = "other"


@dataclass
class Book:
    """A single book from a reading log."""

    title: str
    authors: list[str] = field(default_factory=list)
    isbn: str | None = None
    status: Status = Status.READ
    #: Every date the book was finished, oldest first. Re-reads add entries.
    finished: list[date] = field(default_factory=list)
    #: Which export format the book came from ("notion", "storygraph", ...).
    source: str = ""

    @property
    def author(self) -> str:
        """The primary author, or an empty string if unknown."""
        return self.authors[0] if self.authors else ""

    @property
    def last_finished(self) -> date | None:
        return self.finished[-1] if self.finished else None

    def finished_in(self, year: int) -> bool:
        return any(d.year == year for d in self.finished)

    @property
    def slug(self) -> str:
        """A filesystem-friendly identifier, used to name manual cover overrides."""
        return slugify(f"{self.title} {self.author}")

    def __str__(self) -> str:
        return f"{self.title} by {self.author}" if self.author else self.title


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:80].rstrip("-")
