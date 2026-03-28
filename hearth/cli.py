from __future__ import annotations

import argparse
from pathlib import Path

from hearth.converters.manager import ConverterManager
from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import KindleDevice
from hearth.sync.manager import SyncItem, SyncManager


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hearth",
        description="Sync OPDS books to Kindle",
    )
    parser.add_argument("--settings", default=".hearth/settings.json")
    parser.add_argument("--workspace", default=".hearth")
    parser.add_argument("--feed-url", default="")
    parser.add_argument("--kindle-root", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _discover_items(client: OPDSClient, feed_url: str) -> list[SyncItem]:
    items: list[SyncItem] = []
    for entry, link in client.crawl_acquisitions(feed_url):
        items.append(
            SyncItem(
                id=entry.id,
                title=entry.title,
                download_url=link.href,
                declared_type=link.type,
            )
        )
    return items


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    settings_path = Path(args.settings)
    settings = Settings.load(settings_path)

    feed_url = args.feed_url or settings.opds_url
    if not feed_url:
        parser.error(
            "feed URL is required via --feed-url or settings.opds_url"
        )

    session = OPDSSession(settings)
    client = OPDSClient(session)
    converters = ConverterManager.from_commands(
        settings.kcc_command,
        settings.calibre_command,
    )
    device = KindleDevice.probe(
        preferred=settings.kindle_transport,
        root_hint=args.kindle_root or settings.kindle_mount,
    )
    manager = SyncManager(
        session=session,
        converters=converters,
        device=device,
        workspace=Path(args.workspace),
    )

    items = _discover_items(client, feed_url)
    if args.dry_run:
        print(f"discovered={len(items)}")
        return 0

    outcome = manager.sync(items, force_resync=args.force)
    print(f"synced={outcome.synced} skipped={outcome.skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
