from __future__ import annotations

from hearth.core.opds import OPDSClient, OPDSSession, guess_series_from_title
from hearth.core.settings import Settings


SAMPLE_FEED = b"""<?xml version='1.0' encoding='utf-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <entry>
    <id>book-1</id>
    <title>Dungeon v01</title>
    <author><name>Author</name></author>
        <link rel='http://opds-spec.org/acquisition'
            type='application/epub+zip'
            href='book-1.epub'/>
  </entry>
  <entry>
    <id>nav-1</id>
    <title>Series</title>
        <link rel='subsection'
            type='application/atom+xml;profile=opds-catalog'
            href='sub.xml'/>
  </entry>
</feed>
"""


class FakeSession(OPDSSession):
    def __init__(self, settings: Settings, payloads: dict[str, bytes]):
        super().__init__(settings)
        self.payloads = payloads

    def open_bytes(self, url: str) -> bytes:
        return self.payloads[url]


def test_parse_feed_extracts_entries() -> None:
    session = FakeSession(
        Settings(),
        {"https://example.test/root.xml": SAMPLE_FEED},
    )
    client = OPDSClient(session)

    entries = client.fetch_entries("https://example.test/root.xml")
    assert len(entries) == 2
    assert entries[0].id == "book-1"


def test_crawl_acquisitions_follows_navigation() -> None:
    payloads = {
        "https://example.test/root.xml": SAMPLE_FEED,
        "https://example.test/sub.xml": b"""
            <?xml version='1.0' encoding='utf-8'?>
            <feed xmlns='http://www.w3.org/2005/Atom'>
                <entry>
                    <id>book-2</id>
                    <title>Book Two</title>
                    <link rel='http://opds-spec.org/acquisition'
                          type='application/epub+zip'
                          href='book-2.epub'/>
                </entry>
            </feed>
        """,
    }
    client = OPDSClient(FakeSession(Settings(), payloads))

    acquisitions = client.crawl_acquisitions("https://example.test/root.xml")
    assert len(acquisitions) == 2
    assert acquisitions[1][1].href == "https://example.test/book-2.epub"


def test_guess_series_from_title() -> None:
    series, volume = guess_series_from_title("Delicious in Dungeon v01")
    assert series == "Delicious in Dungeon"
    assert volume == 1


def test_auth_configuration_is_session_wide() -> None:
    settings = Settings(auth_mode="bearer", auth_bearer_token="abc")
    session = OPDSSession(settings)
    assert session.settings.auth_headers() == {"Authorization": "Bearer abc"}


def test_session_resolves_relative_url_against_opds_base() -> None:
    settings = Settings(opds_url="https://example.test/opds/root.xml")
    session = OPDSSession(settings)
    assert session.resolve_url("download") == "https://example.test/opds/download"


def test_session_rejects_relative_url_without_base() -> None:
    session = OPDSSession(Settings())
    try:
        session.resolve_url("download")
    except ValueError as exc:
        assert "relative" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for relative URL without base")


def test_session_rejects_non_http_scheme() -> None:
    session = OPDSSession(Settings(opds_url="https://example.test/opds/root.xml"))
    try:
        session.resolve_url("ftp://example.test/book.epub")
    except ValueError as exc:
        assert "scheme" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for non-http URL scheme")


def test_session_rejects_non_network_resolution() -> None:
    session = OPDSSession(Settings(opds_url="download"))
    try:
        session.resolve_url("book.epub")
    except ValueError as exc:
        assert "not an http" in str(exc).lower()
        assert "resolved" in str(exc).lower()
    else:
        raise AssertionError("Expected ValueError for non-network resolved URL")
