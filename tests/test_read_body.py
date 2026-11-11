import asyncio
from collections.abc import AsyncIterator

from app.api.webhooks import _read_body

LIMIT = 10


class FakeRequest:
    """The two things _read_body uses: .headers and an async stream()."""

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}
        self.chunks = chunks
        self.chunks_read = 0

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.chunks_read += 1
            yield chunk


def read(request: FakeRequest) -> bytes | None:
    return asyncio.run(_read_body(request, LIMIT))  # type: ignore[arg-type]


def test_declared_length_over_the_limit_is_refused_without_reading() -> None:
    request = FakeRequest([b"x"], {"content-length": str(LIMIT + 1)})
    assert read(request) is None
    assert request.chunks_read == 0


def test_declared_length_at_the_limit_is_read() -> None:
    body = b"x" * LIMIT
    assert read(FakeRequest([body], {"content-length": str(LIMIT)})) == body


def test_non_ascii_digit_length_does_not_raise_and_small_body_is_returned() -> None:
    assert read(FakeRequest([b"hello"], {"content-length": "\u00b2"})) == b"hello"


def test_non_ascii_digit_length_still_hits_the_streaming_cap() -> None:
    request = FakeRequest([b"x" * (LIMIT + 1)], {"content-length": "\u00b2"})
    assert read(request) is None


def test_chunked_body_without_length_is_capped_while_streaming() -> None:
    request = FakeRequest([b"x" * 4] * 5)
    assert read(request) is None
    assert request.chunks_read == 3  # stopped as soon as the total passed the limit


def test_chunked_body_within_the_limit_is_joined() -> None:
    assert read(FakeRequest([b"abcde", b"fghij"])) == b"abcdefghij"
