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


def test_usb_list_files_includes_tree_entries(tmp_path: Path) -> None:
    device = KindleDevice(transport="usb", root=tmp_path / "kindle")
    nested_dir = device.documents_dir / "Comics" / "Series"
    nested_dir.mkdir(parents=True, exist_ok=True)
    nested_file = nested_dir / "Volume 01.cbz"
    nested_file.write_text("cbz-bytes", encoding="utf-8")

    entries = device.list_files()
    paths = {entry.path: entry for entry in entries}

    assert "Comics" in paths
    assert paths["Comics"].is_dir is True
    assert "Comics/Series" in paths
    assert paths["Comics/Series"].is_dir is True
    assert "Comics/Series/Volume 01.cbz" in paths
    assert paths["Comics/Series/Volume 01.cbz"].is_dir is False


def test_delete_folder_removes_nested_tree(tmp_path: Path) -> None:
    device = KindleDevice(transport="usb", root=tmp_path / "kindle")
    nested_dir = device.documents_dir / "Hearth" / "Series"
    nested_dir.mkdir(parents=True, exist_ok=True)
    (nested_dir / "Volume 01.mobi").write_text("payload", encoding="utf-8")

    assert device.delete_file("Hearth") is True
    assert not (device.documents_dir / "Hearth").exists()


def test_delete_file_removes_matching_sdr_folder(tmp_path: Path) -> None:
    device = KindleDevice(transport="usb", root=tmp_path / "kindle")
    book = device.documents_dir / "Hearth" / "Book One.epub"
    sdr = device.documents_dir / "Hearth" / "Book One.sdr"
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_text("payload", encoding="utf-8")
    sdr.mkdir(parents=True, exist_ok=True)

    assert device.delete_file("Hearth/Book One.epub") is True
    assert not book.exists()
    assert not sdr.exists()


def test_delete_file_removes_sdr_folder_with_suffix(tmp_path: Path) -> None:
    device = KindleDevice(transport="usb", root=tmp_path / "kindle")
    book = device.documents_dir / "Hearth" / "Book One.epub"
    sdr = device.documents_dir / "Hearth" / "Book One - ASIN123.sdr"
    other = device.documents_dir / "Hearth" / "Book One Hundred.sdr"
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_text("payload", encoding="utf-8")
    sdr.mkdir(parents=True, exist_ok=True)
    other.mkdir(parents=True, exist_ok=True)

    assert device.delete_file("Hearth/Book One.epub") is True
    assert not book.exists()
    assert not sdr.exists()
    assert other.exists()
