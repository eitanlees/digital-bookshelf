"""Find and cache book cover images.

Covers are looked up from free public sources, in order:

1. **Open Library by ISBN** - the exact edition, when the export has an ISBN.
2. **Apple Books** - title/author search via the iTunes Search API. Covers are
   high resolution and usually the current edition.
3. **Open Library search** - good coverage of older and niche books.
4. **Google Books** - only when ``GOOGLE_BOOKS_API_KEY`` is set (the anonymous
   quota is shared and usually exhausted).

Downloaded covers are cached on disk so re-rendering a bookshelf is instant and
does not hit the network. You can always supply your own image for a book by
dropping it in an overrides folder named after the book's slug or ISBN.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from . import __version__
from .models import Book

USER_AGENT = f"digital-bookshelf/{__version__} (+https://github.com/eitanlees/digital-bookshelf)"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MIN_COVER_SIZE = 60  # pixels; anything smaller is a placeholder or a broken image


class ProviderError(RuntimeError):
    """A cover source could not be reached (network error, rate limit, ...)."""


@dataclass
class CoverResult:
    book: Book
    #: Local path to the cover image, or ``None`` if no cover was found.
    path: Path | None
    #: Where the cover came from: a provider name, ``"override"``, or ``None``.
    source: str | None
    #: Set when a provider failed (so the miss is not cached and can be retried).
    error: str | None = None


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def http_get(url: str, *, timeout: float = 20, retries: int = 2) -> bytes | None:
    """GET ``url``, returning ``None`` on 404 and raising ProviderError on failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            retryable = exc.code == 429 or exc.code >= 500
            if not retryable or attempt == retries:
                raise ProviderError(f"HTTP {exc.code} from {_host(url)}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == retries:
                raise ProviderError(f"could not reach {_host(url)}: {exc}") from exc
        time.sleep(1.5 * (attempt + 1))
    return None  # pragma: no cover


def get_json(url: str) -> dict:
    data = http_get(url)
    if data is None:
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"invalid JSON from {_host(url)}") from exc


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"^(the|a|an) ", "", text.strip())
    return re.sub(r"\s+", " ", text).strip()


def short_title(title: str) -> str:
    """Drop subtitles and series info: ``"Dune (Dune, #1)"`` → ``"Dune"``."""
    title = re.sub(r"\s*[(\[].*?[)\]]\s*", " ", title)
    title = re.split(r"\s*[:;]\s+|\s+[-\u2013\u2014]\s+", title)[0]
    return title.strip() or title


def title_similarity(wanted: str, candidate: str) -> float:
    a, b = normalize(short_title(wanted)), normalize(short_title(candidate))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    score = SequenceMatcher(None, a, b).ratio()
    # "Wager" vs "The Wager: A Tale of ..." style matches.
    if a in b or b in a:
        score = max(score, 0.9 if min(len(a), len(b)) >= 4 else score)
    return score


def author_matches(book: Book, candidates: Iterable[str]) -> bool:
    if not book.authors:
        return True
    wanted = {last_name(a) for a in book.authors} - {""}
    for name in candidates:
        for word in normalize(name).split():
            # Tolerate small typos in hand-typed logs ("Beckman" vs "Beckmann").
            if any(word == w or SequenceMatcher(None, word, w).ratio() >= 0.8 for w in wanted):
                return True
    return False


def last_name(author: str) -> str:
    words = [w for w in normalize(author).split() if w not in {"jr", "sr", "ii", "iii", "phd"}]
    return words[-1] if words else ""


def match_score(book: Book, title: str, authors: Iterable[str]) -> float:
    """Score a search hit; 0 means "not this book"."""
    t = title_similarity(book.title, title)
    if t < 0.85 or not author_matches(book, list(authors)):
        return 0.0
    return t


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class Provider:
    """A source of cover image URLs for a book.

    Lookups happen in two passes: first every provider's ``by_isbn`` (exact
    edition matches), then every provider's ``search`` (title/author matches).
    Both yield image URLs, best first, and raise ProviderError if the source is
    unreachable.
    """

    name = "provider"
    #: Minimum seconds between API calls, shared across threads.
    min_interval = 0.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_call = 0.0

    def by_isbn(self, book: Book) -> Iterator[str]:
        return iter(())

    def search(self, book: Book) -> Iterator[str]:
        return iter(())

    def _get_json(self, base: str, params: dict) -> dict:
        if self.min_interval:
            with self._lock:
                wait = self._last_call + self.min_interval - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                self._last_call = time.monotonic()
        return get_json(f"{base}?{urllib.parse.urlencode(params)}")


def _ranked(book: Book, hits: Iterable[tuple[str, Iterable[str], str]]) -> list[str]:
    """Order ``(title, authors, url)`` hits by match quality, dropping non-matches."""
    scored = [
        (match_score(book, title, authors), i, url) for i, (title, authors, url) in enumerate(hits)
    ]
    urls = [url for score, _, url in sorted(scored, key=lambda s: (-s[0], s[1])) if score]
    return list(dict.fromkeys(urls))


class OpenLibrary(Provider):
    name = "openlibrary"
    SEARCH = "https://openlibrary.org/search.json"
    COVER = "https://covers.openlibrary.org/b/{kind}/{value}-L.jpg?default=false"

    def by_isbn(self, book: Book) -> Iterator[str]:
        if book.isbn:
            yield self.COVER.format(kind="isbn", value=book.isbn)

    def search(self, book: Book) -> Iterator[str]:
        queries = [f"{book.title} {book.author}".strip()]
        if short_title(book.title) != book.title:
            queries.append(f"{short_title(book.title)} {book.author}".strip())
        if book.author:
            # Last resort for misspelled authors; results still need a close author match.
            queries.append(short_title(book.title))
        for q in dict.fromkeys(queries):
            params = {"q": q, "limit": 10, "fields": "title,author_name,cover_i"}
            docs = self._get_json(self.SEARCH, params).get("docs", [])
            urls = _ranked(
                book,
                (
                    (
                        d.get("title", ""),
                        d.get("author_name", []),
                        self.COVER.format(kind="id", value=d["cover_i"]),
                    )
                    for d in docs
                    if d.get("cover_i")
                ),
            )
            if urls:
                yield from urls
                return


class AppleBooks(Provider):
    name = "apple"
    SEARCH = "https://itunes.apple.com/search"
    # The iTunes Search API allows roughly 20 calls per minute.
    min_interval = 3.0

    def search(self, book: Book) -> Iterator[str]:
        term = f"{short_title(book.title)} {book.author}".strip()
        params = {"term": term, "media": "ebook", "limit": 10}
        results = self._get_json(self.SEARCH, params).get("results", [])
        yield from _ranked(
            book,
            (
                (r.get("trackName", ""), [r.get("artistName", "")], self._art(r))
                for r in results
                if r.get("artworkUrl100")
            ),
        )

    @staticmethod
    def _art(result: dict) -> str:
        # Apple serves any size up to the original; "bb" keeps the aspect ratio.
        return re.sub(r"/\d+x\d+bb\.", "/1200x1200bb.", result["artworkUrl100"])


class GoogleBooks(Provider):
    """Google Books. Only used when an API key is configured."""

    name = "google"
    SEARCH = "https://www.googleapis.com/books/v1/volumes"

    def __init__(self, api_key: str | None = None):
        super().__init__()
        self.api_key = api_key or os.environ.get("GOOGLE_BOOKS_API_KEY")

    def by_isbn(self, book: Book) -> Iterator[str]:
        if self.api_key and book.isbn:
            for info in self._volumes(f"isbn:{book.isbn}"):
                if url := self._image(info):
                    yield url

    def search(self, book: Book) -> Iterator[str]:
        if not self.api_key:
            return
        q = f"intitle:{short_title(book.title)}"
        if book.author:
            q += f" inauthor:{book.author.split()[-1]}"
        yield from _ranked(
            book,
            (
                (info.get("title", ""), info.get("authors", []), url)
                for info in self._volumes(q)
                if (url := self._image(info))
            ),
        )

    def _volumes(self, query: str) -> list[dict]:
        params = {"q": query, "maxResults": 10, "key": self.api_key}
        items = self._get_json(self.SEARCH, params).get("items", [])
        return [item.get("volumeInfo", {}) for item in items]

    @staticmethod
    def _image(info: dict) -> str | None:
        links = info.get("imageLinks", {})
        url = links.get("thumbnail") or links.get("smallThumbnail")
        if not url:
            return None
        url = url.replace("http://", "https://").replace("&edge=curl", "")
        return f"{url}&fife=w800-h1200"


PROVIDERS: dict[str, Callable[[], Provider]] = {
    "openlibrary": OpenLibrary,
    "apple": AppleBooks,
    "google": GoogleBooks,
}
#: Default order. ISBN lookups always try Open Library first since it is
#: unthrottled and returns the exact edition; searches then follow this order.
DEFAULT_SOURCES = ("apple", "openlibrary", "google")


# ---------------------------------------------------------------------------
# Cache + finder
# ---------------------------------------------------------------------------


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "digital-bookshelf"


def cache_key(book: Book) -> str:
    raw = f"{book.isbn or ''}|{normalize(book.title)}|{normalize(book.author)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


class CoverFinder:
    """Look up covers for books, using an on-disk cache and optional overrides.

    ``overrides`` is a folder of images you supply yourself. A file named after a
    book's slug (``book.slug``, e.g. ``the-wager-david-grann.jpg``) or ISBN
    always wins over anything found online.
    """

    def __init__(
        self,
        *,
        cache_dir: str | Path | None = None,
        overrides: str | Path | None = None,
        sources: Iterable[str] = DEFAULT_SOURCES,
        refresh: bool = False,
        workers: int = 4,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.overrides = Path(overrides) if overrides else None
        unknown = set(sources) - set(PROVIDERS)
        if unknown:
            raise ValueError(f"Unknown cover source(s): {', '.join(sorted(unknown))}")
        self.providers = [PROVIDERS[name]() for name in sources]
        self.refresh = refresh
        self.workers = max(1, workers)
        self._index_path = self.cache_dir / "index.json"
        self._lock = threading.Lock()
        self._index: dict[str, dict] = self._load_index()

    # -- public -------------------------------------------------------------

    def find_all(
        self, books: list[Book], progress: Callable[[CoverResult], None] | None = None
    ) -> list[CoverResult]:
        """Find covers for many books concurrently, preserving input order."""
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = []
            for result in pool.map(self.find, books):
                if progress:
                    progress(result)
                results.append(result)
        self.save()
        return results

    def find(self, book: Book) -> CoverResult:
        """Find a cover for one book (override → cache → online sources)."""
        if path := self._override_for(book):
            return CoverResult(book, path, "override")

        key = cache_key(book)
        entry = self._index.get(key)
        if entry and not self.refresh:
            path = self.cache_dir / "covers" / entry["file"] if entry.get("file") else None
            if path is None or path.exists():
                return CoverResult(book, path, entry.get("source"))

        errors = []
        isbn_order = sorted(self.providers, key=lambda p: p.name != "openlibrary")
        passes = [(p, p.by_isbn) for p in isbn_order] + [(p, p.search) for p in self.providers]
        for provider, lookup in passes:
            try:
                for url in lookup(book):
                    data = http_get(url)
                    if data and (suffix := _image_suffix(data)):
                        path = self.cache_dir / "covers" / f"{key}{suffix}"
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                        self._remember(key, book, provider.name, url, path.name)
                        return CoverResult(book, path, provider.name)
            except ProviderError as exc:
                errors.append(f"{provider.name}: {exc}")

        if errors:
            # Don't cache the miss; a later run may succeed.
            return CoverResult(book, None, None, error="; ".join(errors))
        self._remember(key, book, None, None, None)
        return CoverResult(book, None, None)

    # -- internals ----------------------------------------------------------

    def _override_for(self, book: Book) -> Path | None:
        if not self.overrides or not self.overrides.is_dir():
            return None
        names = [book.slug] + ([book.isbn] if book.isbn else [])
        for name in names:
            for suffix in IMAGE_SUFFIXES:
                path = self.overrides / f"{name}{suffix}"
                if path.exists():
                    return path
        return None

    def _remember(self, key, book, source, url, filename) -> None:
        with self._lock:
            self._index[key] = {
                "title": book.title,
                "author": book.author,
                "isbn": book.isbn,
                "source": source,
                "url": url,
                "file": filename,
                "checked": time.strftime("%Y-%m-%d"),
            }

    def _load_index(self) -> dict[str, dict]:
        try:
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save(self) -> None:
        """Write the lookup index to disk (``find_all`` does this for you)."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._index_path)


def _image_suffix(data: bytes) -> str | None:
    """Return a file suffix if ``data`` is a real, reasonably sized image."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = (img.format or "").lower()
    except Exception:
        return None
    if width < MIN_COVER_SIZE or height < MIN_COVER_SIZE:
        return None
    return {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "gif": ".gif"}.get(fmt, ".jpg")
