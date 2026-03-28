"""OPDS Feed client for Hearth."""

import requests
import feedparser
from typing import List, Optional, Set
from dataclasses import dataclass, field
from urllib.parse import urljoin


@dataclass
class Book:
    """Represents a book from OPDS feed."""

    title: str
    author: str
    id: str
    download_url: Optional[str] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None
    format: Optional[str] = None
    series_name: Optional[str] = None
    series_index: Optional[str] = None


@dataclass
class Collection:
    """Represents an OPDS collection/category."""

    title: str
    id: str
    feed_url: str
    description: Optional[str] = None
    books: List[Book] = field(default_factory=list)
    path: Optional[str] = None


class OPDSClient:
    """Client for OPDS (Open Publication Distribution System) feeds."""

    def __init__(
        self,
        server_url: str,
        timeout: int = 10,
        auth_type: str = "none",
        username: str = "",
        password: str = "",
        token: str = "",
    ):
        self.server_url = server_url.rstrip("/")
        self.root_feed = self.server_url
        self.timeout = timeout
        self.session = requests.Session()
        self.auth_type = auth_type
        self.username = username
        self.password = password
        self.token = token
        self._configure_auth()

    def _configure_auth(self) -> None:
        """Configure session authentication for OPDS requests."""
        self.session.auth = None
        if "Authorization" in self.session.headers:
            del self.session.headers["Authorization"]

        if self.auth_type == "basic" and self.username:
            self.session.auth = (self.username, self.password)
        elif self.auth_type == "bearer" and self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _build_url(self, path_or_url: str) -> str:
        """Build absolute URL from OPDS-relative or absolute URLs."""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        # Keep leading slash semantics: /api/... should resolve at host root.
        return urljoin(f"{self.server_url}/", path_or_url)

    def get_feed(self, path: str = "/") -> Optional[feedparser.FeedParserDict]:
        """Fetch and parse an OPDS feed."""
        try:
            url = self._build_url(path)
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            return feedparser.parse(response.content)
        except requests.RequestException as error:
            print(f"Error fetching OPDS feed from {url}: {error}")
            return None

    def download_content(
        self,
        path_or_url: str,
        timeout: int = 30,
    ) -> Optional[bytes]:
        """Download protected or public content using OPDS session auth."""
        url = self._build_url(path_or_url)
        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            print(f"Error downloading OPDS content from {url}: {error}")
            return None

    def get_main_feed(self) -> Optional[feedparser.FeedParserDict]:
        """Get the main OPDS feed."""
        return self.get_feed(self.root_feed)

    def _entry_description(self, entry) -> str:
        """Extract description text from OPDS entry."""
        summary = entry.get("summary")
        if summary:
            return str(summary)

        content = entry.get("content")
        if isinstance(content, list) and content:
            value = content[0].get("value")
            if value:
                return str(value)

        if isinstance(content, str):
            return content

        return ""

    def _looks_like_identifier(self, value: str) -> bool:
        """Return True when value appears to be an internal identifier."""
        normalized = (value or "").strip().lower()
        if not normalized:
            return True

        return normalized.startswith("urn:")

    def _entry_title(self, entry) -> str:
        """Extract a human-readable title from OPDS entry metadata."""
        title_candidates = [
            entry.get("title"),
            entry.get("dc_title"),
            entry.get("dcterms_title"),
        ]

        title_detail = entry.get("title_detail")
        if isinstance(title_detail, dict):
            title_candidates.append(title_detail.get("value"))

        for link in entry.get("links", []):
            title_candidates.append(link.get("title"))

        for candidate in title_candidates:
            if not candidate:
                continue

            text = str(candidate).strip()
            if not text:
                continue
            if self._looks_like_identifier(text):
                continue
            return text

        fallback = str(entry.get("id", "") or "").strip()
        return fallback or "Unknown"

    def _entry_series_name(self, entry) -> Optional[str]:
        """Extract optional series name from common OPDS/calibre fields."""
        candidates = [
            entry.get("series"),
            entry.get("calibre_series"),
            entry.get("belongs_to_collection"),
        ]

        for link in entry.get("links", []):
            rel = (link.get("rel") or "").lower()
            if "collection" in rel:
                candidates.append(link.get("title"))

        for candidate in candidates:
            if not candidate:
                continue

            if isinstance(candidate, dict):
                candidate = candidate.get("title") or candidate.get("name")

            text = str(candidate).strip()
            if not text:
                continue
            if self._looks_like_identifier(text):
                continue
            return text

        return None

    def _entry_series_index(self, entry) -> Optional[str]:
        """Extract optional series index from common OPDS/calibre fields."""
        candidates = [
            entry.get("series_index"),
            entry.get("calibre_series_index"),
            entry.get("group_position"),
        ]

        for link in entry.get("links", []):
            rel = (link.get("rel") or "").lower()
            if "collection" in rel:
                candidates.append(link.get("number"))

        for candidate in candidates:
            if candidate is None:
                continue

            text = str(candidate).strip()
            if not text:
                continue
            return text

        return None

    def get_books_from_feed(
        self,
        feed: feedparser.FeedParserDict,
    ) -> List[Book]:
        """Extract books from an OPDS feed."""
        books: List[Book] = []

        if not hasattr(feed, "entries"):
            return books

        for entry in feed.entries:
            acquisition_links = self._get_acquisition_links(entry)
            if not acquisition_links:
                continue

            book = Book(
                title=self._entry_title(entry),
                author=entry.get("author", "Unknown"),
                id=entry.get("id", ""),
                description=self._entry_description(entry),
                series_name=self._entry_series_name(entry),
                series_index=self._entry_series_index(entry),
            )

            # Extract download link
            first_acq = acquisition_links[0]
            book.download_url = first_acq.get("href")
            book.format = first_acq.get("type", "").split("/")[-1]

            for link in entry.get("links", []):
                if link.get("rel") == "http://opds-spec.org/image":
                    book.cover_url = link.get("href")

            books.append(book)

        return books

    def _get_acquisition_links(self, entry) -> List[dict]:
        """Return acquisition links from an OPDS entry."""
        matches = []
        for link in entry.get("links", []):
            rel = (link.get("rel") or "").lower()
            if "acquisition" in rel:
                matches.append(link)
        return matches

    def _get_subsection_link(self, entry) -> Optional[str]:
        """Return subsection/facet link from an OPDS entry if present."""
        facet_rel = "http://opds-spec.org/catalog/1.2/rel/facet"
        for link in entry.get("links", []):
            rel = link.get("rel", "")
            if rel in ["subsection", facet_rel]:
                return link.get("href")
        return None

    def _is_navigation_entry(self, entry) -> bool:
        """Return True when entry points to another catalog feed."""
        subsection = self._get_subsection_link(entry)
        if not subsection:
            return False

        has_declared_type = False
        for link in entry.get("links", []):
            link_type = (link.get("type") or "").lower()
            if not link_type:
                continue
            has_declared_type = True
            if "kind=navigation" in link_type:
                return True
            if "kind=acquisition" in link_type:
                return False

        # If no kind is declared, assume navigation so traversal still works.
        return not has_declared_type

    def _books_from_feed_paginated(
        self,
        feed: feedparser.FeedParserDict,
        visited_feeds: Optional[Set[str]] = None,
    ) -> List[Book]:
        """Extract books from feed and any paginated next links."""
        if visited_feeds is None:
            visited_feeds = set()

        books = self.get_books_from_feed(feed)
        next_url = None
        for link in getattr(feed, "links", []):
            if link.get("rel") == "next":
                next_url = link.get("href")
                break

        if not next_url:
            return books

        absolute_next = self._build_url(next_url)
        if absolute_next in visited_feeds:
            return books

        visited_feeds.add(absolute_next)
        next_feed = self.get_feed(next_url)
        if not next_feed:
            return books

        books.extend(self._books_from_feed_paginated(next_feed, visited_feeds))
        return books

    def _traverse_collections(
        self,
        feed_path: str,
        path_prefix: str = "",
        visited: Optional[Set[str]] = None,
        visited_feed_ids: Optional[Set[str]] = None,
        depth: int = 0,
        max_depth: int = 8,
    ) -> List[Collection]:
        """Recursively traverse OPDS subsection links and collect feeds."""
        if visited is None:
            visited = set()
        if visited_feed_ids is None:
            visited_feed_ids = set()

        absolute_url = self._build_url(feed_path)
        if absolute_url in visited or depth > max_depth:
            return []
        visited.add(absolute_url)

        feed = self.get_feed(feed_path)
        if not feed or not hasattr(feed, "entries"):
            return []

        feed_meta = getattr(feed, "feed", None)
        feed_id = getattr(feed_meta, "id", "") if feed_meta else ""
        if feed_id:
            if feed_id in visited_feed_ids:
                return []
            visited_feed_ids.add(feed_id)

        collections: List[Collection] = []
        for entry in feed.entries:
            subsection = self._get_subsection_link(entry)
            if not subsection:
                continue

            title = entry.get("title", "Unknown")
            path = f"{path_prefix} / {title}" if path_prefix else title
            collection = Collection(
                title=title,
                id=entry.get("id", subsection),
                feed_url=subsection,
                description=(entry.get("summary") or entry.get("content") or ""),
                path=path,
            )
            collections.append(collection)

            if self._is_navigation_entry(entry):
                collections.extend(
                    self._traverse_collections(
                        subsection,
                        path,
                        visited,
                        visited_feed_ids,
                        depth + 1,
                        max_depth,
                    )
                )

        return collections

    def get_collections(self) -> List[Collection]:
        """Traverse OPDS navigation tree and return discovered collections."""
        return self._traverse_collections(self.root_feed)

    def load_collection(self, collection: Collection) -> bool:
        """Load books for a specific collection, including paginated feeds."""
        feed = self.get_feed(collection.feed_url)
        if feed:
            collection.books = self._books_from_feed_paginated(feed)
            return True
        return False

    def search(self, query: str) -> List[Book]:
        """Search for books in the OPDS server."""
        feed = self.get_feed(f"/search?q={query}")
        if feed:
            return self.get_books_from_feed(feed)
        return []

    def get_all_books(self) -> List[Book]:
        """Get books by traversing discovered collections."""
        all_books: List[Book] = []
        seen_ids: Set[str] = set()

        for collection in self.get_collections():
            if self.load_collection(collection):
                for book in collection.books:
                    if book.id and book.id in seen_ids:
                        continue
                    if book.id:
                        seen_ids.add(book.id)
                    all_books.append(book)

        return all_books
