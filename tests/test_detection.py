from __future__ import annotations

from pathlib import Path

from hearth.converters.detection import infer_extension


def test_infer_extension_for_real_cbz(sample_cbz_path: Path) -> None:
    assert infer_extension(sample_cbz_path).lower() == ".cbz"


def test_infer_extension_for_real_epub(sample_epub_path: Path) -> None:
    assert infer_extension(sample_epub_path).lower() == ".epub"


def test_declared_type_wins_when_present(tmp_path: Path) -> None:
    payload = tmp_path / "book.bin"
    payload.write_bytes(b"nonsense")
    assert (
        infer_extension(payload, declared_type="application/epub+zip")
        == ".epub"
    )
