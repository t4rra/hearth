from __future__ import annotations

import json
from pathlib import Path
import zipfile
import pytest

from hearth.converters.calibre import CalibreConverter
from hearth.converters.manager import ConverterManager


def _write_fake_ebook_convert(script_path: Path) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        "source = pathlib.Path(sys.argv[1])\n"
        "target = pathlib.Path(sys.argv[2])\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_bytes(b'BOOKMOBI' + source.read_bytes()[:32])\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_kcc(script_path: Path) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "source = None\n"
        "target = None\n"
        "for idx, arg in enumerate(args):\n"
        "    if arg in {'-o', '--output'} and idx + 1 < len(args):\n"
        "        target = pathlib.Path(args[idx + 1])\n"
        "for arg in args:\n"
        "    if arg and not arg.startswith('-'):\n"
        "        p = pathlib.Path(arg)\n"
        "        if p.exists():\n"
        "            source = p\n"
        "            break\n"
        "if source is None or target is None:\n"
        "    raise SystemExit(2)\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_bytes(b'BOOKMOBI' + source.read_bytes()[:64])\n"
        "print('10%')\n"
        "print('60%')\n"
        "print('100%')\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_kcc_with_arg_capture(script_path: Path, capture_path: Path) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import pathlib\n"
        "import sys\n"
        f"capture = pathlib.Path({str(capture_path)!r})\n"
        "args = sys.argv[1:]\n"
        "capture.parent.mkdir(parents=True, exist_ok=True)\n"
        "capture.write_text(json.dumps(args), encoding='utf-8')\n"
        "source = None\n"
        "target = None\n"
        "for idx, arg in enumerate(args):\n"
        "    if arg in {'-o', '--output'} and idx + 1 < len(args):\n"
        "        target = pathlib.Path(args[idx + 1])\n"
        "for arg in args:\n"
        "    if arg and not arg.startswith('-'):\n"
        "        p = pathlib.Path(arg)\n"
        "        if p.exists():\n"
        "            source = p\n"
        "            break\n"
        "if source is None or target is None:\n"
        "    raise SystemExit(2)\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_bytes(b'BOOKMOBI' + source.read_bytes()[:64])\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_kcc_retry_once_then_success(
    script_path: Path,
    counter_path: Path,
) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        f"counter = pathlib.Path({str(counter_path)!r})\n"
        "n = 0\n"
        "if counter.exists():\n"
        "    n = int(counter.read_text(encoding='utf-8') or '0')\n"
        "n += 1\n"
        "counter.parent.mkdir(parents=True, exist_ok=True)\n"
        "counter.write_text(str(n), encoding='utf-8')\n"
        "args = sys.argv[1:]\n"
        "source = None\n"
        "target = None\n"
        "for idx, arg in enumerate(args):\n"
        "    if arg in {'-o', '--output'} and idx + 1 < len(args):\n"
        "        target = pathlib.Path(args[idx + 1])\n"
        "for arg in args:\n"
        "    if arg and not arg.startswith('-'):\n"
        "        p = pathlib.Path(arg)\n"
        "        if p.exists():\n"
        "            source = p\n"
        "            break\n"
        "if source is None or target is None:\n"
        "    raise SystemExit(2)\n"
        "if n <= 2:\n"
        "    print('Worker exited unexpectedly')\n"
        "    raise SystemExit(1)\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "payload = source.read_bytes() if source.is_file() else b'dir'\n"
        "target.write_bytes(b'BOOKMOBI' + payload[:64])\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_kcc_fail_cbz_but_succeed_for_dir(
    script_path: Path,
    counter_path: Path,
) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        f"counter = pathlib.Path({str(counter_path)!r})\n"
        "n = 0\n"
        "if counter.exists():\n"
        "    n = int(counter.read_text(encoding='utf-8') or '0')\n"
        "n += 1\n"
        "counter.parent.mkdir(parents=True, exist_ok=True)\n"
        "counter.write_text(str(n), encoding='utf-8')\n"
        "args = sys.argv[1:]\n"
        "source = None\n"
        "target = None\n"
        "for idx, arg in enumerate(args):\n"
        "    if arg in {'-o', '--output'} and idx + 1 < len(args):\n"
        "        target = pathlib.Path(args[idx + 1])\n"
        "for arg in args:\n"
        "    if arg and not arg.startswith('-'):\n"
        "        p = pathlib.Path(arg)\n"
        "        if p.exists():\n"
        "            source = p\n"
        "            break\n"
        "if source is None or target is None:\n"
        "    raise SystemExit(2)\n"
        "if source.is_file() and source.suffix.lower() == '.cbz':\n"
        "    print('Failed to extract archive')\n"
        "    raise SystemExit(1)\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "payload = source.read_bytes() if source.is_file() else b'dir'\n"
        "target.write_bytes(b'BOOKMOBI' + payload[:64])\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_kcc_non_transient_failure(
    script_path: Path,
    counter_path: Path,
) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        f"counter = pathlib.Path({str(counter_path)!r})\n"
        "n = 0\n"
        "if counter.exists():\n"
        "    n = int(counter.read_text(encoding='utf-8') or '0')\n"
        "n += 1\n"
        "counter.parent.mkdir(parents=True, exist_ok=True)\n"
        "counter.write_text(str(n), encoding='utf-8')\n"
        "print('fatal: unsupported content layout')\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def _write_fake_kcc_success_code_with_failure_marker(script_path: Path) -> None:
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "source = None\n"
        "target = None\n"
        "for idx, arg in enumerate(args):\n"
        "    if arg in {'-o', '--output'} and idx + 1 < len(args):\n"
        "        target = pathlib.Path(args[idx + 1])\n"
        "for arg in args:\n"
        "    if arg and not arg.startswith('-'):\n"
        "        p = pathlib.Path(arg)\n"
        "        if p.exists():\n"
        "            source = p\n"
        "            break\n"
        "if source is None or target is None:\n"
        "    raise SystemExit(2)\n"
        "target.parent.mkdir(parents=True, exist_ok=True)\n"
        "target.write_bytes(b'BOOKMOBI' + source.read_bytes()[:64])\n"
        "print('Worker exited unexpectedly')\n"
        "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)


def test_calibre_converter_uses_command_execution(
    tmp_path: Path,
    sample_epub_path: Path,
) -> None:
    fake_converter = tmp_path / "ebook-convert"
    _write_fake_ebook_convert(fake_converter)

    converter = CalibreConverter(command=str(fake_converter))
    output = tmp_path / "out.mobi"
    converted = converter.convert(sample_epub_path, output)

    assert converted == output
    data = output.read_bytes()
    assert data.startswith(b"BOOKMOBI")


def test_manager_converts_testing_files_with_real_command_contract(
    tmp_path: Path,
    sample_cbz_path: Path,
) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    _write_fake_kcc(fake_kcc)

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    result = manager.convert_for_kindle(
        source=sample_cbz_path,
        destination_dir=tmp_path / "converted",
        stem="comic-test",
        declared_type="application/vnd.comicbook+zip",
    )

    assert result.backend == "kcc"
    assert result.output.exists()
    assert result.output.suffix == ".mobi"
    assert result.output.read_bytes().startswith(b"BOOKMOBI")


def test_manager_raises_when_no_real_converter_available(
    tmp_path: Path,
    sample_cbz_path: Path,
) -> None:
    manager = ConverterManager.from_commands(
        kcc_command="/nonexistent/kcc-c2e",
        calibre_command="/nonexistent/ebook-convert",
    )
    try:
        result = manager.convert_for_kindle(
            source=sample_cbz_path,
            destination_dir=tmp_path / "converted",
            stem="comic-test",
            declared_type="application/vnd.comicbook+zip",
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "kcc" in message.lower() or "comic" in message.lower()
    else:
        # Some environments can auto-bootstrap KCC from the repo.
        assert result.backend == "kcc"
        assert result.output.exists()


@pytest.mark.parametrize("suffix", [".cbt", ".cba", ".cb7"])
def test_manager_routes_additional_comic_extensions_to_kcc(
    tmp_path: Path,
    suffix: str,
) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    _write_fake_kcc(fake_kcc)

    source = tmp_path / f"comic{suffix}"
    source.write_bytes(b"fake-comic-content")

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    result = manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-test",
        declared_type="application/epub+zip",
    )

    assert result.backend == "kcc"
    assert result.output.exists()
    assert result.output.suffix == ".mobi"


def test_kcc_uses_comicinfo_title_author_manga_and_required_flags(
    tmp_path: Path,
) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    capture_path = tmp_path / "kcc_args.json"
    _write_fake_kcc_with_arg_capture(fake_kcc, capture_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "ComicInfo.xml",
            """
            <ComicInfo>
              <Title>Metadata Title</Title>
              <Writer>Metadata Author</Writer>
              <Manga>YesAndRightToLeft</Manga>
            </ComicInfo>
            """,
        )
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    result = manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-test",
        title="Feed Title",
        author="Feed Author",
        declared_type="application/vnd.comicbook+zip",
    )

    assert result.backend == "kcc"
    args = json.loads(capture_path.read_text(encoding="utf-8"))
    assert "-u" in args
    assert "--autolevel" in args
    assert "-f" in args
    assert "MOBI" in args
    assert "--manga-style" in args
    assert "--title" in args
    assert args[args.index("--title") + 1] == "Metadata Title"
    assert "--author" in args
    assert args[args.index("--author") + 1] == "Metadata Author"


def test_kcc_force_manga_direction_overrides_metadata(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    capture_path = tmp_path / "kcc_args.json"
    _write_fake_kcc_with_arg_capture(fake_kcc, capture_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr(
            "ComicInfo.xml",
            """
            <ComicInfo>
              <Title>Metadata Title</Title>
              <Writer>Metadata Author</Writer>
              <Manga>No</Manga>
            </ComicInfo>
            """,
        )
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(
        kcc_command=str(fake_kcc),
        kcc_manga_default=True,
        kcc_manga_force=True,
    )
    manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-test",
        declared_type="application/vnd.comicbook+zip",
    )

    args = json.loads(capture_path.read_text(encoding="utf-8"))
    assert "--manga-style" in args


def test_kcc_can_disable_autolevel(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    capture_path = tmp_path / "kcc_args.json"
    _write_fake_kcc_with_arg_capture(fake_kcc, capture_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(
        kcc_command=str(fake_kcc),
        kcc_autolevel=False,
    )
    manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-test",
        declared_type="application/vnd.comicbook+zip",
    )

    args = json.loads(capture_path.read_text(encoding="utf-8"))
    assert "--autolevel" not in args


def test_kcc_preserve_margin_flag_can_be_set(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    capture_path = tmp_path / "kcc_args.json"
    _write_fake_kcc_with_arg_capture(fake_kcc, capture_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("ComicInfo.xml", "<ComicInfo><Title>T</Title></ComicInfo>")
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(
        kcc_command=str(fake_kcc),
        kcc_preserve_margin_percent=12,
    )
    manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-test",
        declared_type="application/vnd.comicbook+zip",
    )

    args = json.loads(capture_path.read_text(encoding="utf-8"))
    assert "--preservemargin" in args
    assert args[args.index("--preservemargin") + 1] == "12"


def test_kcc_retries_once_for_transient_failure(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    counter_path = tmp_path / "invocations.txt"
    _write_fake_kcc_retry_once_then_success(fake_kcc, counter_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    result = manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-retry",
        declared_type="application/vnd.comicbook+zip",
    )

    assert result.output.exists()
    assert int(counter_path.read_text(encoding="utf-8")) == 3


def test_kcc_uses_preextract_fallback_for_cbz_extract_errors(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    counter_path = tmp_path / "invocations.txt"
    _write_fake_kcc_fail_cbz_but_succeed_for_dir(fake_kcc, counter_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    result = manager.convert_for_kindle(
        source=source,
        destination_dir=tmp_path / "converted",
        stem="comic-fallback",
        declared_type="application/vnd.comicbook+zip",
    )

    assert result.output.exists()
    assert int(counter_path.read_text(encoding="utf-8")) == 3


def test_kcc_does_not_retry_non_transient_failure(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    counter_path = tmp_path / "invocations.txt"
    _write_fake_kcc_non_transient_failure(fake_kcc, counter_path)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    with pytest.raises(RuntimeError):
        manager.convert_for_kindle(
            source=source,
            destination_dir=tmp_path / "converted",
            stem="comic-fail",
            declared_type="application/vnd.comicbook+zip",
        )

    # Only the two default argument-order attempts should run.
    assert int(counter_path.read_text(encoding="utf-8")) == 2


def test_kcc_rejects_output_when_failure_marker_present(tmp_path: Path) -> None:
    fake_kcc = tmp_path / "kcc-c2e"
    _write_fake_kcc_success_code_with_failure_marker(fake_kcc)

    source = tmp_path / "comic.cbz"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("001.jpg", b"image-data")

    manager = ConverterManager.from_commands(kcc_command=str(fake_kcc))
    with pytest.raises(RuntimeError):
        manager.convert_for_kindle(
            source=source,
            destination_dir=tmp_path / "converted",
            stem="comic-marker",
            declared_type="application/vnd.comicbook+zip",
        )

    assert not (tmp_path / "converted" / "comic-marker.mobi").exists()
