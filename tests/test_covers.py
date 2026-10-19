import json

import pytest

from digital_bookshelf import Book, CoverFinder
from digital_bookshelf import covers as covers_mod
from digital_bookshelf.covers import (
    AppleBooks,
    OpenLibrary,
    ProviderError,
    match_score,
    short_title,
    title_similarity,
)

from .conftest import make_image_bytes

WAGER = Book("The Wager", ["David Grann"])


class FakeWeb:
    """Stands in for http_get/get_json, recording every URL requested."""

    def __init__(self, json_responses=None, images=None):
        self.json_responses = json_responses or {}
        self.images = images or {}
        self.requests: list[str] = []

    def get_json(self, url):
        self.requests.append(url)
        for prefix, payload in self.json_responses.items():
            if url.startswith(prefix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        return {}

    def http_get(self, url, **kwargs):
        self.requests.append(url)
        return self.images.get(url)


@pytest.fixture
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(covers_mod, "get_json", fake.get_json)
    monkeypatch.setattr(covers_mod, "http_get", fake.http_get)
    monkeypatch.setattr(AppleBooks, "min_interval", 0)
    return fake


# -- matching ------------------------------------------------------------------


def test_short_title():
    assert short_title("The Girl With the Dragon Tattoo (Millennium, #1)") == (
        "The Girl With the Dragon Tattoo"
    )
    assert short_title("The Wager: A Tale of Shipwreck") == "The Wager"
    assert short_title("Dune") == "Dune"


def test_title_similarity():
    assert title_similarity("The Wager", "Wager") == 1.0
    assert title_similarity("The Wager: A Tale of Shipwreck", "The Wager") == 1.0
    assert title_similarity("Gödel, Escher, Bach", "Godel Escher Bach") == 1.0
    assert title_similarity("The Wager", "Killers of the Flower Moon") < 0.5


def test_match_score_requires_author():
    assert match_score(WAGER, "The Wager", ["David Grann"]) == 1.0
    assert match_score(WAGER, "The Wager", ["Someone Else"]) == 0.0
    assert match_score(WAGER, "Summary of The Wager", ["Irb Media"]) == 0.0


# -- providers -----------------------------------------------------------------


def test_openlibrary_isbn_and_search(web):
    web.json_responses[OpenLibrary.SEARCH] = {
        "docs": [
            {"title": "Summary of David Grann's the Wager", "author_name": ["Irb Media"]},
            {"title": "Los Náufragos", "author_name": ["David Grann"], "cover_i": 1},
            {"title": "Wager", "author_name": ["David Grann"], "cover_i": 42},
        ]
    }
    provider = OpenLibrary()
    book = Book("The Wager", ["David Grann"], isbn="9780385534260")
    assert list(provider.by_isbn(book)) == [
        "https://covers.openlibrary.org/b/isbn/9780385534260-L.jpg?default=false"
    ]
    assert list(provider.search(book)) == [
        "https://covers.openlibrary.org/b/id/42-L.jpg?default=false"
    ]


def test_apple_search_upsizes_artwork(web):
    web.json_responses[AppleBooks.SEARCH] = {
        "results": [
            {
                "trackName": "The Wager",
                "artistName": "David Grann",
                "artworkUrl100": "https://example.com/a/9780385534277.jpg/100x100bb.jpg",
            },
            {"trackName": "The White Darkness", "artistName": "David Grann",
             "artworkUrl100": "https://example.com/b/100x100bb.jpg"},
        ]
    }  # fmt: skip
    assert list(AppleBooks().search(WAGER)) == [
        "https://example.com/a/9780385534277.jpg/1200x1200bb.jpg"
    ]


def test_google_is_skipped_without_api_key(web, monkeypatch):
    monkeypatch.delenv("GOOGLE_BOOKS_API_KEY", raising=False)
    provider = covers_mod.GoogleBooks()
    assert list(provider.search(WAGER)) == []
    assert web.requests == []


# -- CoverFinder ---------------------------------------------------------------


def test_finder_downloads_and_caches(web, tmp_path):
    isbn_url = OpenLibrary.COVER.format(kind="isbn", value="9780000000002")
    web.images[isbn_url] = make_image_bytes()
    book = Book("The Glass Orchard", ["Iris Calloway"], isbn="9780000000002")

    finder = CoverFinder(cache_dir=tmp_path)
    [result] = finder.find_all([book])
    assert result.source == "openlibrary"
    assert result.path.exists() and result.path.suffix == ".jpg"
    assert web.requests == [isbn_url]  # ISBN hit: no searches needed

    index = json.loads((tmp_path / "index.json").read_text())
    assert next(iter(index.values()))["url"] == isbn_url

    # A second finder reads from the cache without touching the network.
    web.requests.clear()
    [again] = CoverFinder(cache_dir=tmp_path).find_all([book])
    assert again.path == result.path
    assert web.requests == []


def test_finder_isbn_pass_runs_before_searches(web, tmp_path):
    web.images[OpenLibrary.COVER.format(kind="isbn", value="9780000000002")] = None
    book = Book("Whatever", ["Some One"], isbn="9780000000002")
    CoverFinder(cache_dir=tmp_path, sources=["apple", "openlibrary"]).find(book)
    assert web.requests[0].startswith("https://covers.openlibrary.org/b/isbn/")
    assert web.requests[1].startswith(AppleBooks.SEARCH)


def test_finder_skips_tiny_or_broken_images(web, tmp_path):
    web.json_responses[AppleBooks.SEARCH] = {
        "results": [
            {"trackName": "The Wager", "artistName": "David Grann",
             "artworkUrl100": "https://img/1/100x100bb.jpg"},
            {"trackName": "The Wager", "artistName": "David Grann",
             "artworkUrl100": "https://img/2/100x100bb.jpg"},
        ]
    }  # fmt: skip
    web.images["https://img/1/1200x1200bb.jpg"] = make_image_bytes(size=(1, 1))
    web.images["https://img/2/1200x1200bb.jpg"] = make_image_bytes(fmt="PNG")
    result = CoverFinder(cache_dir=tmp_path, sources=["apple"]).find(WAGER)
    assert result.source == "apple"
    assert result.path.suffix == ".png"


def test_finder_caches_misses(web, tmp_path):
    finder = CoverFinder(cache_dir=tmp_path, sources=["openlibrary"])
    result = finder.find_all([WAGER])[0]
    assert result.path is None and result.error is None
    web.requests.clear()
    assert CoverFinder(cache_dir=tmp_path, sources=["openlibrary"]).find(WAGER).path is None
    assert web.requests == []
    # --refresh searches again
    CoverFinder(cache_dir=tmp_path, sources=["openlibrary"], refresh=True).find(WAGER)
    assert web.requests


def test_finder_does_not_cache_errors(web, tmp_path):
    web.json_responses[OpenLibrary.SEARCH] = ProviderError("HTTP 503 from openlibrary.org")
    finder = CoverFinder(cache_dir=tmp_path, sources=["openlibrary"])
    result = finder.find_all([WAGER])[0]
    assert result.path is None
    assert "503" in result.error
    assert json.loads((tmp_path / "index.json").read_text()) == {}


def test_overrides_win(web, tmp_path):
    overrides = tmp_path / "mine"
    overrides.mkdir()
    (overrides / f"{WAGER.slug}.png").write_bytes(make_image_bytes(fmt="PNG"))
    result = CoverFinder(cache_dir=tmp_path / "cache", overrides=overrides).find(WAGER)
    assert result.source == "override"
    assert result.path == overrides / "the-wager-david-grann.png"
    assert web.requests == []


def test_override_by_isbn(web, tmp_path):
    book = Book("X", ["Y"], isbn="9780000000002")
    (tmp_path / "9780000000002.jpg").write_bytes(make_image_bytes())
    assert CoverFinder(cache_dir=tmp_path / "c", overrides=tmp_path).find(book).source == "override"


def test_unknown_source_rejected(tmp_path):
    with pytest.raises(ValueError, match="nope"):
        CoverFinder(cache_dir=tmp_path, sources=["nope"])


def test_http_get_handles_404(monkeypatch):
    import urllib.error

    def not_found(*args, **kwargs):
        raise urllib.error.HTTPError("https://x", 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", not_found)
    assert covers_mod.http_get("https://example.com/missing.jpg") is None


def test_http_get_raises_provider_error(monkeypatch):
    import urllib.error

    def forbidden(*args, **kwargs):
        raise urllib.error.HTTPError("https://x", 403, "Forbidden", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    with pytest.raises(ProviderError, match="403"):
        covers_mod.http_get("https://example.com/a.jpg")


def test_author_typos_still_match():
    book = Book("A History of Pi", ["Pert Beckman"])
    assert match_score(book, "A History of Pi", ["Petr Beckmann"]) == 1.0
    assert match_score(book, "A History of Pi", ["Someone Else"]) == 0.0
