from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(name="workspace_root")
def _workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture(name="testing_files_dir")
def _testing_files_dir(workspace_root: Path) -> Path:
    directory = workspace_root / "TESTING FILES"
    if not directory.exists():
        pytest.skip("TESTING FILES directory not present")
    return directory


@pytest.fixture(name="sample_cbz_path")
def _sample_cbz_path(testing_files_dir: Path) -> Path:
    matches = sorted(testing_files_dir.glob("*.cbz"))
    if not matches:
        pytest.skip("No CBZ files in TESTING FILES")
    return matches[0]


@pytest.fixture(name="sample_epub_path")
def _sample_epub_path(testing_files_dir: Path) -> Path:
    matches = sorted(testing_files_dir.glob("*.epub"))
    if not matches:
        pytest.skip("No EPUB files in TESTING FILES")
    return matches[0]
