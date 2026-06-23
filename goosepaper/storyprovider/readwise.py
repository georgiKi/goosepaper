import datetime
import os
import re
from html import escape
from typing import List, Optional

import bs4
import requests

from .storyprovider import StoryProvider
from ..story import Story
from ..version import __version__


READWISE_READER_LIST_URL = "https://readwise.io/api/v3/list/"
READWISE_BODY_SOURCES = {"text", "html", "summary"}
READWISE_READER_LOCATIONS = {"new", "later", "shortlist", "archive", "feed"}
READWISE_READER_CATEGORIES = {
    "article",
    "email",
    "rss",
    "highlight",
    "note",
    "pdf",
    "epub",
    "tweet",
    "video",
}

_BLOCK_TAGS = {"blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "li", "p"}
_DROP_TAGS = {"form", "noscript", "script", "style", "svg"}
_MAX_READER_PAGE_SIZE = 100


class ReadwiseReaderStoryProvider(StoryProvider):
    def __init__(
        self,
        token: str = None,
        token_env: str = "READWISE_TOKEN",
        limit: int = 5,
        since_days_ago: int = None,
        location: Optional[str] = "later",
        category: Optional[str] = "article",
        tags: Optional[List[str]] = None,
        body_source: str = "text",
    ) -> None:
        if body_source not in READWISE_BODY_SOURCES:
            raise ValueError(
                "Readwise body_source must be one of "
                '"text", "html", or "summary".'
            )
        if location is not None and location not in READWISE_READER_LOCATIONS:
            raise ValueError(
                "Readwise location must be one of "
                '"new", "later", "shortlist", "archive", or "feed".'
            )
        if category is not None and category not in READWISE_READER_CATEGORIES:
            raise ValueError(
                'Readwise category must be one of "article", "email", "rss", '
                '"highlight", "note", "pdf", "epub", "tweet", or "video".'
            )

        self.token_env = token_env
        self.token = token or os.environ.get(token_env)
        if not self.token:
            raise ValueError(
                "Readwise token not found. "
                f"Set {token_env} or pass token directly."
            )

        self.limit = limit
        self.location = location
        self.category = category
        self.tags = tags or []
        self.body_source = body_source
        self._since = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=since_days_ago)
            if since_days_ago
            else None
        )

    def get_stories(self, limit: int = 5, **kwargs) -> List[Story]:
        target = min(self.limit, limit)
        if target <= 0:
            return []

        stories = []
        page_cursor = None
        while len(stories) < target:
            payload = self._fetch_page(
                limit=min(_MAX_READER_PAGE_SIZE, target - len(stories)),
                page_cursor=page_cursor,
            )
            for document in payload.get("results", []):
                story = _story_from_reader_document(
                    document,
                    body_source=self.body_source,
                )
                if story is not None:
                    stories.append(story)
                if len(stories) >= target:
                    break

            page_cursor = payload.get("nextPageCursor")
            if not page_cursor:
                break

        return stories

    def _fetch_page(self, limit: int, page_cursor: Optional[str]) -> dict:
        params = {
            "limit": limit,
            "withHtmlContent": "true"
            if self.body_source in {"text", "html"}
            else "false",
        }
        if page_cursor:
            params["pageCursor"] = page_cursor
        if self._since is not None:
            params["updatedAfter"] = self._since.isoformat()
        if self.location is not None:
            params["location"] = self.location
        if self.category is not None:
            params["category"] = self.category
        if self.tags:
            params["tag"] = self.tags

        response = requests.get(
            READWISE_READER_LIST_URL,
            params=params,
            headers={
                "Authorization": f"Token {self.token}",
                "User-Agent": f"goosepaper/{__version__}",
            },
            timeout=20,
        )
        response.raise_for_status()
        return response.json()


def _story_from_reader_document(
    document: dict,
    body_source: str = "text",
) -> Optional[Story]:
    if document.get("parent_id"):
        return None

    body_html = _document_body_html(document, body_source=body_source)
    if not body_html:
        return None

    return Story(
        headline=document.get("title") or "Untitled Readwise document",
        body_html=body_html,
        byline=_document_byline(document),
        date=_document_date(document),
        section_title="Readwise",
    )


def _document_body_html(document: dict, body_source: str = "text") -> str:
    if body_source == "summary":
        return _text_to_paragraph_html(document.get("summary") or "")

    html_content = document.get("html_content") or ""
    if body_source == "html":
        return _clean_body_html(html_content) or _text_to_paragraph_html(
            document.get("summary") or ""
        )

    blocks = _text_blocks_from_html(html_content)
    title = _normalize_text(document.get("title") or "")
    if blocks and title and _normalize_text(blocks[0]) == title:
        blocks = blocks[1:]
    if not blocks:
        blocks = _text_blocks_from_text(document.get("summary") or "")
    return "".join(f"<p>{escape(block)}</p>" for block in blocks)


def _clean_body_html(html: str) -> str:
    if not html:
        return ""
    soup = bs4.BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    body = soup.body or soup
    return str(body)


def _text_blocks_from_html(html: str) -> List[str]:
    if not html:
        return []
    soup = bs4.BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()

    blocks = []
    for tag in soup.find_all(_BLOCK_TAGS):
        if tag.find_parent(_BLOCK_TAGS):
            continue
        text = _normalize_text(tag.get_text(" ", strip=True))
        if text and (not blocks or blocks[-1] != text):
            blocks.append(text)

    if blocks:
        return blocks
    return _text_blocks_from_text(soup.get_text("\n\n", strip=True))


def _text_blocks_from_text(text: str) -> List[str]:
    if not text:
        return []
    parts = re.split(r"\n{2,}", text)
    return [
        block
        for block in (_normalize_text(part) for part in parts)
        if block
    ]


def _text_to_paragraph_html(text: str) -> str:
    return "".join(
        f"<p>{escape(block)}</p>"
        for block in _text_blocks_from_text(text)
    )


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def _document_byline(document: dict) -> Optional[str]:
    parts = [
        document.get("author"),
        document.get("site_name") or document.get("source"),
        document.get("reading_time"),
    ]
    return " - ".join(
        str(part).strip()
        for part in parts
        if str(part or "").strip()
    )


def _document_date(document: dict) -> Optional[datetime.datetime]:
    for key in ("published_date", "saved_at", "updated_at", "created_at"):
        parsed = _parse_readwise_datetime(document.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_readwise_datetime(value) -> Optional[datetime.datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed
