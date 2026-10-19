import io
from pathlib import Path

import pytest
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def make_image_bytes(size=(200, 300), color=(200, 30, 30), fmt="JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


@pytest.fixture
def image_bytes() -> bytes:
    return make_image_bytes()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if a test forgets to stub out HTTP."""

    def blocked(*args, **kwargs):
        raise AssertionError("tests must not touch the network")

    monkeypatch.setattr("urllib.request.urlopen", blocked)
