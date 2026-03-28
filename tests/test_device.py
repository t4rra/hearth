from __future__ import annotations

from pathlib import Path

from hearth.sync.device import KindleDevice


def test_put_and_delete_file(tmp_path: Path) -> None:
    device_root = tmp_path / "kindle"
    device = KindleDevice(transport="usb", root=device_root)
    source = tmp_path / "in.epub"
    source.write_text("payload", encoding="utf-8")

    remote = device.put_file(source, "Book.epub")
    assert remote.exists()
    assert remote.read_text(encoding="utf-8") == "payload"

    assert device.delete_file("Book.epub") is True
    assert not remote.exists()


def test_hearth_dir_candidates_include_documents(tmp_path: Path) -> None:
    device = KindleDevice(transport="usb", root=tmp_path / "kindle")
    candidates = device.hearth_dir_candidates()
    assert any(path.name == "documents" for path in candidates)
