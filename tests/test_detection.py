from __future__ import annotations

from pathlib import Path
import pytest

from hearth.converters.detection import infer_extension


def test_infer_extension_for_real_cbz(sample_cbz_path: Path) -> None:
    assert infer_extension(sample_cbz_path).lower() == ".cbz"


def test_infer_extension_for_real_epub(sample_epub_path: Path) -> None:
    assert infer_extension(sample_epub_path).lower() == ".epub"


def test_declared_type_wins_when_present(tmp_path: Path) -> None:
    payload = tmp_path / "book.bin"
    payload.write_bytes(b"nonsense")
    assert infer_extension(payload, declared_type="application/epub+zip") == ".epub"


def test_real_extension_wins_over_declared_type(tmp_path: Path) -> None:
    payload = tmp_path / "book.cbz"
    payload.write_bytes(b"not-a-real-cbz")
    assert infer_extension(payload, declared_type="application/epub+zip") == ".cbz"


@pytest.mark.parametrize("suffix", [".cbr", ".cbz", ".cbt", ".cba", ".cb7"])
def test_comic_extensions_are_detected_by_suffix(
    tmp_path: Path,
    suffix: str,
) -> None:
    payload = tmp_path / f"book{suffix}"
    payload.write_bytes(b"not-a-real-archive")

    assert infer_extension(payload) == suffix


@pytest.mark.parametrize("suffix", [".cbr", ".cbz", ".cbt", ".cba", ".cb7"])
def test_comic_suffix_wins_over_declared_epub_type(
    tmp_path: Path,
    suffix: str,
) -> None:
    payload = tmp_path / f"book{suffix}"
    payload.write_bytes(b"not-a-real-archive")

    assert infer_extension(payload, declared_type="application/epub+zip") == suffix
