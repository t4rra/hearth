"""Tests for recursive OPDS collection traversal."""

import unittest
from unittest.mock import patch

import feedparser  # type: ignore[import-untyped]

from hearth.core.opds_client import OPDSClient


ROOT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>All Books</title>
    <id>urn:booklore:catalog:all</id>
    <content type="text">Browse all available books</content>
    <link rel="subsection"
          href="/api/v1/opds/catalog?page=1&amp;size=50"
          type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  </entry>
  <entry>
    <title>Libraries</title>
    <id>urn:booklore:navigation:libraries</id>
    <content type="text">Browse books by library</content>
    <link rel="subsection"
          href="/api/v1/opds/libraries"
          type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
  </entry>
</feed>
"""

LIBRARIES_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Sci-Fi Library</title>
    <id>urn:booklore:library:scifi</id>
    <link rel="subsection"
          href="/api/v1/opds/libraries/scifi"
          type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  </entry>
</feed>
"""

ACQ_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Dune</title>
    <id>book:1</id>
    <author><name>Frank Herbert</name></author>
    <link rel="http://opds-spec.org/acquisition"
          href="/api/v1/opds/download/1"
          type="application/epub+zip"/>
  </entry>
</feed>
"""

ACQ_FEED_URN_TITLE_WITH_DC = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/terms/">
  <entry>
    <title>urn:booklore:book:42</title>
    <id>urn:booklore:book:42</id>
    <dc:title>The Real Book Title</dc:title>
    <author><name>Jane Author</name></author>
    <link rel="http://opds-spec.org/acquisition"
          href="/api/v1/opds/download/42"
          type="application/epub+zip"/>
  </entry>
</feed>
"""

ACQ_FEED_WITH_SERIES = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Leviathan Wakes</title>
    <id>book:expanse-1</id>
    <author><name>James S. A. Corey</name></author>
    <series>The Expanse</series>
    <series_index>1</series_index>
    <link rel="http://opds-spec.org/acquisition"
          href="/api/v1/opds/download/expanse-1"
          type="application/epub+zip"/>
  </entry>
</feed>
"""

BOOKLORE_ROOT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>urn:booklore:root</id>
  <title>Booklore Catalog</title>
  <entry>
    <title>All Books</title>
    <id>urn:booklore:catalog:all</id>
    <link rel="subsection"
          href="/api/v1/opds/catalog?page=1&amp;size=50"
          type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
    <content type="text">Browse all available books</content>
  </entry>
  <entry>
    <title>Recently Added</title>
    <id>urn:booklore:catalog:recent</id>
    <link rel="subsection"
          href="/api/v1/opds/recent?page=1&amp;size=50"
          type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
    <content type="text">Recently added books</content>
  </entry>
  <entry>
    <title>Libraries</title>
    <id>urn:booklore:navigation:libraries</id>
    <link rel="subsection"
          href="/api/v1/opds/libraries"
          type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
    <content type="text">Browse books by library</content>
  </entry>
  <entry>
    <title>Shelves</title>
    <id>urn:booklore:navigation:shelves</id>
    <link rel="subsection"
          href="/api/v1/opds/shelves"
          type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
    <content type="text">Browse your personal shelves</content>
  </entry>
  <entry>
    <title>Magic Shelves</title>
    <id>urn:booklore:navigation:magic-shelves</id>
    <link rel="subsection"
          href="/api/v1/opds/magic-shelves"
          type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
    <content type="text">Browse your smart, dynamic shelves</content>
  </entry>
  <entry>
    <title>Authors</title>
    <id>urn:booklore:navigation:authors</id>
    <link rel="subsection"
          href="/api/v1/opds/authors"
          type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
    <content type="text">Browse books by author</content>
  </entry>
  <entry>
    <title>Series</title>
    <id>urn:booklore:navigation:series</id>
    <link rel="subsection"
          href="/api/v1/opds/series"
          type="application/atom+xml;profile=opds-catalog;kind=navigation"/>
    <content type="text">Browse books by series</content>
  </entry>
  <entry>
    <title>Surprise Me</title>
    <id>urn:booklore:catalog:surprise</id>
    <link rel="subsection"
          href="/api/v1/opds/surprise"
          type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
    <content type="text">25 random books from the catalog</content>
  </entry>
</feed>
"""


class TestOPDSTraversal(unittest.TestCase):
    """Verify recursive traversal and acquisition parsing."""

    def test_build_url_handles_root_relative_paths(self):
        client = OPDSClient("https://example.test/api/v1/opds")
        resolved = client._build_url("/api/v1/opds/catalog?page=1&size=50")
        self.assertEqual(
            resolved,
            "https://example.test/api/v1/opds/catalog?page=1&size=50",
        )

    def test_navigation_entry_classification_uses_link_type(self):
        client = OPDSClient("https://example.test/api/v1/opds")
        nav_entry = feedparser.parse(ROOT_FEED).entries[1]
        acq_entry = feedparser.parse(ROOT_FEED).entries[0]
        self.assertTrue(client._is_navigation_entry(nav_entry))
        self.assertFalse(client._is_navigation_entry(acq_entry))

    def test_traverse_collections_includes_nested_paths(self):
        client = OPDSClient("https://example.test/api/v1/opds")

        feed_map = {
            "https://example.test/api/v1/opds": feedparser.parse(ROOT_FEED),
            "/api/v1/opds/libraries": feedparser.parse(LIBRARIES_FEED),
            "/api/v1/opds/libraries/scifi": feedparser.parse(ACQ_FEED),
            "/api/v1/opds/catalog?page=1&size=50": feedparser.parse(ACQ_FEED),
        }

        def fake_get_feed(path: str = "/"):
            return feed_map.get(path)

        with patch.object(client, "get_feed", side_effect=fake_get_feed):
            collections = client.get_collections()

        paths = {c.path for c in collections if c.path}
        self.assertIn("All Books", paths)
        self.assertIn("Libraries", paths)
        self.assertIn("Libraries / Sci-Fi Library", paths)

        all_books = [c for c in collections if c.title == "All Books"]
        self.assertEqual(len(all_books), 1)
        self.assertEqual(
            all_books[0].description,
            "Browse all available books",
        )

    def test_booklore_root_entries_are_detected_as_collections(self):
        client = OPDSClient("https://example.test/api/v1/opds")
        sample = feedparser.parse(ROOT_FEED)

        with patch.object(client, "get_feed", return_value=sample):
            collections = client.get_collections()

        titles = {c.title for c in collections}
        self.assertIn("All Books", titles)
        self.assertIn("Libraries", titles)

    def test_matches_provided_booklore_root_shape(self):
        client = OPDSClient("https://example.test/api/v1/opds")
        sample = feedparser.parse(BOOKLORE_ROOT_FEED)

        with patch.object(client, "get_feed", return_value=sample):
            collections = client.get_collections()

        self.assertEqual(len(collections), 8)
        titles = [collection.title for collection in collections]
        self.assertIn("All Books", titles)
        self.assertIn("Recently Added", titles)
        self.assertIn("Surprise Me", titles)

    def test_get_books_from_feed_uses_acquisition_links(self):
        client = OPDSClient("https://example.test")
        feed = feedparser.parse(ACQ_FEED)
        books = client.get_books_from_feed(feed)

        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].title, "Dune")
        self.assertEqual(books[0].download_url, "/api/v1/opds/download/1")

    def test_get_books_from_feed_prefers_readable_title_over_urn(self):
        client = OPDSClient("https://example.test")
        feed = feedparser.parse(ACQ_FEED_URN_TITLE_WITH_DC)

        books = client.get_books_from_feed(feed)

        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].title, "The Real Book Title")
        self.assertEqual(books[0].id, "urn:booklore:book:42")

    def test_get_books_from_feed_extracts_series_metadata(self):
        client = OPDSClient("https://example.test")
        feed = feedparser.parse(ACQ_FEED_WITH_SERIES)

        books = client.get_books_from_feed(feed)

        self.assertEqual(len(books), 1)
        self.assertEqual(books[0].series_name, "The Expanse")
        self.assertEqual(books[0].series_index, "1")


if __name__ == "__main__":
    unittest.main()
