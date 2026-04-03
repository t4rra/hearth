from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
import shlex
from typing import Callable

from .detection import COMIC_EXTENSIONS


class CalibreConverter:
    name = "calibre"

    def __init__(self, command: str = ""):
        self.command = command
        self.extra_args = ""
        try:
            self.extra_args_list: list[str] = []
        except Exception:
            self.extra_args_list = []

    def set_extra_args(self, extra: str) -> None:
        self.extra_args = extra or ""
        try:
            self.extra_args_list = shlex.split(self.extra_args)
        except Exception:
            self.extra_args_list = []

    def discover_command(self) -> str | None:
        if self.command:
            resolved = shutil.which(self.command)
            if resolved:
                return resolved
            command_path = Path(self.command)
            if command_path.exists() and command_path.is_file():
                return str(command_path)
            return None

        direct = shutil.which("ebook-convert")
        if direct:
            return direct

        mac_path = "/Applications/calibre.app/Contents/MacOS/ebook-convert"
        return mac_path if Path(mac_path).exists() else None

    def available(self) -> bool:
        return self.discover_command() is not None

    @staticmethod
    def _looks_like_manga(title: str, author: str, source: Path) -> bool:
        text = f"{title} {author} {source.stem}".lower()
        if "manga" in text:
            return True

        # Heuristic: Japanese script in title/author strongly suggests manga.
        if re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", text):
            return True

        # Common manga volume naming pattern plus non-ASCII marker.
        if re.search(r"\b(v|vol\.?|volume)\s*\d+\b", text):
            if any(ord(char) > 127 for char in (title + author)):
                return True
        return False

    @staticmethod
    def _extract_percent(line: str) -> float | None:
        match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", line)
        if not match:
            return None
        value = float(match.group(1))
        if value < 0:
            return 0.0
        if value > 100:
            return 100.0
        return value

    def _run_with_output(
        self,
        args: list[str],
        progress_callback: Callable[[float | None, str], None] | None,
    ) -> tuple[int, str]:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        output_lines: list[str] = []
        assert process.stdout is not None
        for line in process.stdout:
            text = line.rstrip()
            if not text:
                continue
            output_lines.append(text)
            if progress_callback is not None:
                progress_callback(self._extract_percent(text), text)
        code = process.wait()
        return code, "\n".join(output_lines)

    def convert(
        self,
        source: Path,
        target: Path,
        title: str = "",
        author: str = "",
        progress_callback: Callable[[float | None, str], None] | None = None,
    ) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        command = self.discover_command()
        if not command:
            raise RuntimeError("Calibre ebook-convert is not available")

        args = [command, str(source), str(target)]

        # Preserve metadata/cover and avoid image quality loss.
        args.extend(
            [
                "--prefer-metadata-cover",
                "--insert-metadata",
                "--mobi-keep-original-images",
            ]
        )

        if title.strip():
            args.extend(["--title", title.strip()])
        if author.strip():
            args.extend(["--authors", author.strip()])

        suffix = source.suffix.lower()
        if suffix in COMIC_EXTENSIONS:
            # Avoid destructive comic processing steps.
            args.extend(
                [
                    "--no-process",
                    "--dont-normalize",
                    "--dont-sharpen",
                    "--dont-compress",
                    "--dont-grayscale",
                ]
            )
            if self._looks_like_manga(title, author, source):
                args.append("--right2left")

        # Append any user-specified extra args for ebook-convert here.
        if hasattr(self, "extra_args_list") and self.extra_args_list:
            args.extend(self.extra_args_list)

        code, output = self._run_with_output(
            args,
            progress_callback,
        )
        if code != 0:
            details = output.strip()
            if not details:
                details = "ebook-convert failed"
            raise RuntimeError(details)

        if not target.exists() or target.stat().st_size == 0:
            raise RuntimeError("ebook-convert did not produce output")
        return target
