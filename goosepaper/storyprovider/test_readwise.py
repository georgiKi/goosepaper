import datetime

import pytest

from . import readwise


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _document(
    *,
    title="A saved article",
    html_content=(
        "<article><h1>A saved article</h1>"
        "<p>Hello <strong>reader</strong>.</p>"
        "<p>Second paragraph.</p></article>"
    ),
    summary="A fallback summary.",
    parent_id=None,
):
    return {
        "id": "doc-1",
        "title": title,
        "author": "Ada Lovelace",
        "site_name": "Example",
        "reading_time": "4 mins",
        "published_date": "2026-04-24",
        "saved_at": "2026-04-25T10:15:00+00:00",
        "summary": summary,
        "html_content": html_content,
        "parent_id": parent_id,
    }


def test_readwise_provider_fetches_reader_documents_as_text(monkeypatch):
    seen = {}

    def fake_get(url, *, params, headers, timeout):
        seen["url"] = url
        seen["params"] = params
        seen["headers"] = headers
        seen["timeout"] = timeout
        return _FakeResponse(
            {"results": [_document()], "nextPageCursor": None}
        )

    monkeypatch.setenv("READWISE_TOKEN", "test-token")
    monkeypatch.setattr(readwise.requests, "get", fake_get)

    provider = readwise.ReadwiseReaderStoryProvider(
        limit=4,
        location="later",
        category="article",
        tags=["morning", "longform"],
    )
    stories = provider.get_stories(limit=2)

    assert seen["url"] == "https://readwise.io/api/v3/list/"
    assert seen["params"] == {
        "limit": 2,
        "withHtmlContent": "true",
        "location": "later",
        "category": "article",
        "tag": ["morning", "longform"],
    }
    assert seen["headers"]["Authorization"] == "Token test-token"
    assert seen["headers"]["User-Agent"].startswith("goosepaper/")
    assert seen["timeout"] == 20
    assert len(stories) == 1
    assert stories[0].headline == "A saved article"
    assert stories[0].body_html == (
        "<p>Hello reader.</p><p>Second paragraph.</p>"
    )
    assert stories[0].byline == "Ada Lovelace - Example - 4 mins"
    assert stories[0].date == datetime.datetime(2026, 4, 24)
    assert stories[0].section_title == "Readwise"


def test_readwise_provider_can_use_summary_without_html_fetch(monkeypatch):
    seen = {}

    def fake_get(url, *, params, headers, timeout):
        seen["params"] = params
        return _FakeResponse(
            {
                "results": [
                    _document(
                        html_content="",
                        summary=(
                            "First summary paragraph.\n\n"
                            "Second summary paragraph."
                        ),
                    )
                ],
                "nextPageCursor": None,
            }
        )

    monkeypatch.setenv("READWISE_TOKEN", "test-token")
    monkeypatch.setattr(readwise.requests, "get", fake_get)

    provider = readwise.ReadwiseReaderStoryProvider(body_source="summary")
    stories = provider.get_stories(limit=1)

    assert seen["params"]["withHtmlContent"] == "false"
    assert stories[0].body_html == (
        "<p>First summary paragraph.</p><p>Second summary paragraph.</p>"
    )


def test_readwise_provider_skips_child_documents(monkeypatch):
    monkeypatch.setenv("READWISE_TOKEN", "test-token")
    monkeypatch.setattr(
        readwise.requests,
        "get",
        lambda *args, **kwargs: _FakeResponse(
            {
                "results": [
                    _document(title="Highlight child", parent_id="parent-doc"),
                    _document(title="Parent article"),
                ],
                "nextPageCursor": None,
            }
        ),
    )

    provider = readwise.ReadwiseReaderStoryProvider()
    stories = provider.get_stories(limit=5)

    assert len(stories) == 1
    assert stories[0].headline == "Parent article"


def test_readwise_provider_paginates_until_limit(monkeypatch):
    cursors = []

    def fake_get(url, *, params, headers, timeout):
        cursors.append(params.get("pageCursor"))
        if params.get("pageCursor") is None:
            return _FakeResponse(
                {
                    "results": [_document(title="First")],
                    "nextPageCursor": "next-page",
                }
            )
        return _FakeResponse(
            {
                "results": [_document(title="Second")],
                "nextPageCursor": None,
            }
        )

    monkeypatch.setenv("READWISE_TOKEN", "test-token")
    monkeypatch.setattr(readwise.requests, "get", fake_get)

    provider = readwise.ReadwiseReaderStoryProvider(limit=2)
    stories = provider.get_stories(limit=2)

    assert cursors == [None, "next-page"]
    assert [story.headline for story in stories] == ["First", "Second"]


def test_readwise_provider_requires_token(monkeypatch):
    monkeypatch.delenv("READWISE_TOKEN", raising=False)

    with pytest.raises(ValueError, match="Readwise token not found"):
        readwise.ReadwiseReaderStoryProvider()
