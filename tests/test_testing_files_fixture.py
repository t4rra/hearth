from __future__ import annotations

from pathlib import Path

from hearth.converters.detection import infer_extension


def test_testing_files_have_expected_extensions(
    testing_files_dir: Path,
) -> None:
    names = sorted(path.name for path in testing_files_dir.iterdir() if path.is_file())
    assert any(name.endswith(".cbz") for name in names)
    assert any(name.endswith(".epub") for name in names)


def test_every_fixture_file_can_be_classified(testing_files_dir: Path) -> None:
    for path in testing_files_dir.iterdir():
        if not path.is_file():
            continue
        ext = infer_extension(path)
        assert ext.startswith(".")
