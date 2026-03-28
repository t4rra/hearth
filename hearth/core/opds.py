from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from pathlib import Path
import re
import urllib.parse
import urllib.request
from xml.etree import ElementTree as ET

from .settings import Settings

NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass(slots=True)
class OPDSLink:
    href: str
    rel: str
    type: str
    title: str = ""

    def is_navigation(self) -> bool:
        return "navigation" in self.rel or "atom+xml" in self.type

    def is_acquisition(self) -> bool:
        return "acquisition" in self.rel or self.type.startswith("application/epub")


@dataclass(slots=True)
class OPDSEntry:
    id: str
    title: str
    author: str
    links: list[OPDSLink]

    def acquisition_links(self) -> list[OPDSLink]:
        return [link for link in self.links if link.is_acquisition()]


class OPDSSession:
    """Single session carrying auth across browse/download calls."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _request(self, url: str) -> urllib.request.Request:
        headers = self.settings.auth_headers().copy()
        return urllib.request.Request(url=url, headers=headers)

    def open_bytes(self, url: str) -> bytes:
        req = self._request(url)
        creds = self.settings.basic_auth_credentials()
        if creds:
            manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
            manager.add_password(None, url, creds[0], creds[1])
            opener = urllib.request.build_opener(
                urllib.request.HTTPBasicAuthHandler(manager)
            )
            with opener.open(req, timeout=30) as resp:
                return resp.read()
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def download_to(self, url: str, target: Path) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.open_bytes(url))
        return target


class OPDSClient:
    def __init__(self, session: OPDSSession):
        self.session = session

    def parse_feed(self, xml_payload: bytes) -> list[OPDSEntry]:
        root = ET.fromstring(xml_payload.lstrip())
        entries: list[OPDSEntry] = []
        for entry in root.findall("atom:entry", NS):
            title = (
                entry.findtext("atom:title", default="", namespaces=NS) or ""
            ).strip()
            entry_id = (
                entry.findtext("atom:id", default=title, namespaces=NS) or title
            ).strip()
            author = (
                entry.findtext(
                    "atom:author/atom:name",
                    default="",
                    namespaces=NS,
                )
                or ""
            ).strip()
            links: list[OPDSLink] = []
            for link in entry.findall("atom:link", NS):
                links.append(
                    OPDSLink(
                        href=link.attrib.get("href", ""),
                        rel=link.attrib.get("rel", ""),
                        type=link.attrib.get("type", ""),
                        title=link.attrib.get("title", ""),
                    )
                )
            entries.append(
                OPDSEntry(
                    id=entry_id,
                    title=title,
                    author=author,
                    links=links,
                )
            )
        return entries

    def fetch_entries(self, feed_url: str) -> list[OPDSEntry]:
        payload = self.session.open_bytes(feed_url)
        return self.parse_feed(payload)

    def crawl_acquisitions(
        self,
        root_url: str,
        limit: int = 500,
    ) -> list[tuple[OPDSEntry, OPDSLink]]:
        visited: set[str] = set()
        queue = [root_url]
        acquisitions: list[tuple[OPDSEntry, OPDSLink]] = []

        while queue and len(visited) < limit:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)

            for entry in self.fetch_entries(current):
                for link in entry.links:
                    if link.is_acquisition() and link.href:
                        acquisitions.append((entry, self._resolve(current, link)))
                    elif link.is_navigation() and link.href:
                        candidate = self._resolve_url(current, link.href)
                        if candidate not in visited:
                            queue.append(candidate)
        return acquisitions

    def _resolve(self, base_url: str, link: OPDSLink) -> OPDSLink:
        return OPDSLink(
            href=self._resolve_url(base_url, link.href),
            rel=link.rel,
            type=link.type,
            title=link.title,
        )

    @staticmethod
    def _resolve_url(base_url: str, href: str) -> str:
        return urllib.parse.urljoin(base_url, href)


def guess_series_from_title(title: str) -> tuple[str, int | None]:
    match = re.search(
        r"^(?P<series>.*?)(?:\s+v(?:ol)?\.?\s*(?P<volume>\d+))$",
        title,
        flags=re.IGNORECASE,
    )
    if not match:
        return (unescape(title).strip(), None)
    series = unescape(match.group("series")).strip(" -")
    volume = int(match.group("volume"))
    return (series, volume)
