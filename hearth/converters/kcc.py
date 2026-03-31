from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shlex
import re
import shutil
import subprocess
import sys
import threading
from xml.etree import ElementTree
from typing import Callable
import os
import zipfile


_BOOTSTRAP_LOCK = threading.Lock()


@dataclass(slots=True)
class ComicMetadata:
    title: str = ""
    author: str = ""
    manga: bool | None = None


class KCCConverter:
    name = "kcc"
    REPO_URL = "https://github.com/ciromattia/kcc.git"

    PROFILE_NAME_TO_CODE = {
        "kindle 1": "K1",
        "kindle 2": "K2",
        "kindle keyboard/touch": "K34",
        "kindle 5/7/8/10": "K578",
        "kindle dx/dxg": "KDX",
        "kindle paperwhite 1/2": "KPW",
        "kindle paperwhite 5/signature edition": "KPW5",
        "kindle voyage": "KV",
        "kindle oasis 2/3": "KO",
        "kindle 11": "K11",
        "kindle scribe 1/2": "KS",
    }

    def __init__(
        self,
        command: str = "",
        device: str = "auto",
        manga_default: bool = False,
        manga_force: bool = False,
        autolevel: bool = True,
    ):
        self.command = command
        self.device = device
        self.manga_default = manga_default
        self.manga_force = manga_force
        self.autolevel = autolevel
        self.repo_dir = Path.home() / ".hearth" / "vendor" / "kcc"
        self.tools_dir = Path.home() / ".hearth" / "vendor" / "bin"

    def discover_command(self) -> str | None:
        if self.command:
            resolved = shutil.which(self.command)
            if resolved:
                return resolved
            command_path = Path(self.command)
            if command_path.exists() and command_path.is_file():
                return str(command_path)
            return None
        candidates = ["kcc-c2e", "comic2ebook"]
        for candidate in candidates:
            found = shutil.which(candidate)
            if found:
                return found
        repo_cmd = self._discover_repo_command()
        if repo_cmd:
            return " ".join(repo_cmd)
        return None

    def _discover_invocation(self) -> list[str] | None:
        if self.command:
            resolved = shutil.which(self.command)
            if resolved:
                return [resolved]
            command_path = Path(self.command)
            if command_path.exists() and command_path.is_file():
                return [str(command_path)]

        for candidate in ["kcc-c2e", "comic2ebook"]:
            found = shutil.which(candidate)
            if found:
                return [found]

        return self._discover_repo_command()

    def _discover_repo_command(self) -> list[str] | None:
        script = self.repo_dir / "kcc-c2e.py"
        if script.exists() and self._validate_repo_command(script):
            return [sys.executable, str(script)]

        if not self._bootstrap_repo_command(script):
            return None

        if self._validate_repo_command(script):
            return [sys.executable, str(script)]
        return None

    def _validate_repo_command(self, script: Path) -> bool:
        env = self._runtime_env()
        try:
            probe = subprocess.run(
                [sys.executable, str(script), "-h"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError):
            return False

        return probe.returncode == 0 and "kcc-c2e" in (
            (probe.stdout or "") + (probe.stderr or "")
        )

    def _bootstrap_repo_command(self, script: Path) -> bool:
        with _BOOTSTRAP_LOCK:
            git = shutil.which("git")
            if not git:
                return False

            self.repo_dir.parent.mkdir(parents=True, exist_ok=True)

            if not self.repo_dir.exists():
                try:
                    clone = subprocess.run(
                        [
                            git,
                            "clone",
                            "--depth",
                            "1",
                            self.REPO_URL,
                            str(self.repo_dir),
                        ],
                        capture_output=True,
                        text=True,
                        timeout=90,
                        check=False,
                    )
                except (OSError, subprocess.SubprocessError):
                    return False
                if clone.returncode != 0:
                    return False

            if not script.exists():
                return False

            # Best effort dependency bootstrap for running from source checkout.
            try:
                install = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "-q",
                        "-e",
                        str(self.repo_dir),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return False

            return install.returncode == 0

    def available(self) -> bool:
        return self._discover_invocation() is not None

    def diagnostics(self) -> dict[str, str | bool]:
        invocation = self._discover_invocation()
        env = self._runtime_env()
        seven_zip = shutil.which(
            "7zz",
            path=env.get("PATH", ""),
        ) or shutil.which("7z", path=env.get("PATH", ""))
        kindlegen = shutil.which("kindlegen", path=env.get("PATH", ""))
        return {
            "command": " ".join(invocation) if invocation else "",
            "command_available": bool(invocation),
            "archive_tool": seven_zip or "",
            "archive_tool_available": bool(seven_zip),
            "kindlegen": kindlegen or "",
            "kindlegen_available": bool(kindlegen),
        }

    def _runtime_env(self) -> dict[str, str]:
        env = os.environ.copy()
        shim_dirs: list[str] = []
        seven_zip_shim = self._ensure_7zz_shim()
        if seven_zip_shim:
            shim_dirs.append(seven_zip_shim)

        kindlegen_shim = self._ensure_kindlegen_shim()
        if kindlegen_shim:
            shim_dirs.append(kindlegen_shim)

        if shim_dirs:
            current_path = env.get("PATH", "")
            prefix = ":".join(shim_dirs)
            env["PATH"] = f"{prefix}:{current_path}" if current_path else prefix
        return env

    def _ensure_7zz_shim(self) -> str:
        if shutil.which("7zz"):
            return ""

        seven_z = shutil.which("7z")
        if not seven_z:
            return ""

        try:
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            shim_path = self.tools_dir / "7zz"
            if not shim_path.exists():
                shim_path.write_text(
                    "#!/bin/sh\n" f'exec "{seven_z}" "$@"\n',
                    encoding="utf-8",
                )
                shim_path.chmod(0o755)
            return str(self.tools_dir)
        except OSError:
            return ""

    def _find_kindlegen(self) -> str:
        existing = shutil.which("kindlegen")
        if existing:
            return existing

        candidates = [
            Path(
                "/Applications/Kindle Previewer 3.app" "/Contents/lib/fc/bin/kindlegen"
            ),
            Path("/Applications/Kindle Previewer.app" "/Contents/lib/fc/bin/kindlegen"),
            Path("/Applications/Kindle Previewer 3.app/Contents/MacOS/kindlegen"),
            Path("/Applications/Kindle Previewer.app/Contents/MacOS/kindlegen"),
            Path.home()
            / ("Applications/Kindle Previewer 3.app" "/Contents/lib/fc/bin/kindlegen"),
            Path.home()
            / ("Applications/Kindle Previewer.app" "/Contents/lib/fc/bin/kindlegen"),
        ]

        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)

        return ""

    def _ensure_kindlegen_shim(self) -> str:
        kindlegen_path = self._find_kindlegen()
        if not kindlegen_path:
            return ""

        try:
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            shim_path = self.tools_dir / "kindlegen"
            if not shim_path.exists():
                shim_path.write_text(
                    "#!/bin/sh\n" f'exec "{kindlegen_path}" "$@"\n',
                    encoding="utf-8",
                )
                shim_path.chmod(0o755)
            return str(self.tools_dir)
        except OSError:
            return ""

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

    @staticmethod
    def _normalized_tag(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].strip().lower()

    @classmethod
    def _find_text(cls, root: ElementTree.Element, names: set[str]) -> str:
        for element in root.iter():
            if cls._normalized_tag(element.tag) not in names:
                continue
            text = (element.text or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _find_texts(cls, root: ElementTree.Element, names: set[str]) -> list[str]:
        found: list[str] = []
        for element in root.iter():
            if cls._normalized_tag(element.tag) not in names:
                continue
            text = (element.text or "").strip()
            if text:
                found.append(text)
        return found

    @staticmethod
    def _parse_manga_value(value: str) -> bool | None:
        text = value.strip().lower().replace("_", "").replace("-", "")
        if not text:
            return None
        if text in {"yes", "true", "1", "righttoleft", "yesandrighttoleft"}:
            return True
        if text in {"no", "false", "0", "lefttoright", "noandlefttoright"}:
            return False
        if "righttoleft" in text:
            return True
        if "lefttoright" in text:
            return False
        return None

    def _load_comicinfo_xml(self, source: Path) -> str:
        if source.is_dir():
            candidate = source / "ComicInfo.xml"
            if candidate.exists() and candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="ignore")
            return ""

        if source.suffix.lower() != ".cbz":
            return ""

        try:
            with zipfile.ZipFile(source) as archive:
                for name in archive.namelist():
                    if Path(name).name.lower() != "comicinfo.xml":
                        continue
                    with archive.open(name) as handle:
                        return handle.read().decode("utf-8", errors="ignore")
        except (OSError, zipfile.BadZipFile):
            return ""

        return ""

    def _extract_comic_metadata(self, source: Path) -> ComicMetadata:
        raw_xml = self._load_comicinfo_xml(source)
        if not raw_xml:
            return ComicMetadata()

        try:
            root = ElementTree.fromstring(raw_xml)
        except ElementTree.ParseError:
            return ComicMetadata()

        title = self._find_text(root, {"title", "series"})
        creator_fields = {
            "writer",
            "author",
            "penciller",
            "artist",
            "inker",
            "colorist",
            "letterer",
            "coverartist",
        }
        creators = self._find_texts(root, creator_fields)
        author = ", ".join(dict.fromkeys(creators))
        manga_raw = self._find_text(root, {"manga"})
        manga = self._parse_manga_value(manga_raw) if manga_raw else None
        return ComicMetadata(title=title, author=author, manga=manga)

    def _resolve_manga_flag(self, metadata_manga: bool | None) -> bool:
        if self.manga_force:
            return self.manga_default
        if metadata_manga is not None:
            return metadata_manga
        return self.manga_default

    def _run_with_output(
        self,
        args: list[str],
        progress_callback: Callable[[float | None, str], None] | None,
    ) -> tuple[int, str]:
        env = self._runtime_env()
        command_str = shlex.join(args)
        print(f"[KCC] Executing command: {command_str}")
        if progress_callback is not None:
            progress_callback(None, f"[KCC] Executing command: {command_str}")
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
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

    @classmethod
    def normalize_profile(cls, value: str) -> str:
        raw = value.strip()
        if not raw:
            return ""

        upper = raw.upper()
        if upper in {
            "K1",
            "K2",
            "K34",
            "K578",
            "KDX",
            "KPW",
            "KPW5",
            "KV",
            "KO",
            "K11",
            "KS",
        }:
            return upper

        return cls.PROFILE_NAME_TO_CODE.get(raw.lower(), "")

    @staticmethod
    def _looks_like_manga(title: str, author: str, source: Path) -> bool:
        text = f"{title} {author} {source.stem}".lower()
        if "manga" in text:
            return True
        if re.search(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]", text):
            return True
        return False

    @staticmethod
    def _device_flags(device_profile: str) -> list[str]:
        if not device_profile:
            return []
        return ["-p", device_profile]

    def convert(
        self,
        source: Path,
        target: Path,
        device_hint: str = "",
        title: str = "",
        author: str = "",
        progress_callback: Callable[[float | None, str], None] | None = None,
    ) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        command = self._discover_invocation()
        if not command:
            raise RuntimeError("KCC command not available")

        selected_device = ""
        configured = self.device.strip().lower()
        if configured and configured != "auto":
            selected_device = self.normalize_profile(self.device)
        elif device_hint.strip() and device_hint.strip().lower() != "auto":
            selected_device = self.normalize_profile(device_hint)

        comic_metadata = self._extract_comic_metadata(source)
        use_manga_mode = self._resolve_manga_flag(comic_metadata.manga)

        resolved_title = (comic_metadata.title or title or source.stem).strip()
        resolved_author = (comic_metadata.author or author).strip()

        manga_flags = ["--manga-style"] if use_manga_mode else []
        profile_flags = self._device_flags(selected_device)
        title_flags = ["--title", resolved_title] if resolved_title else []
        author_flags = ["--author", resolved_author] if resolved_author else []
        format_flags = ["-f", "MOBI"]
        upscale_flags = ["-u"]
        autolevel_flags = ["--autolevel"] if self.autolevel else []
        common_flags = [
            *profile_flags,
            *manga_flags,
            *upscale_flags,
            *autolevel_flags,
            *format_flags,
            *title_flags,
            *author_flags,
        ]

        # Keep flags close to KCC GUI-equivalent behavior.
        attempts: list[list[str]] = [
            [
                *command,
                *common_flags,
                str(source),
                "-o",
                str(target),
            ],
            [
                *command,
                *common_flags,
                "-o",
                str(target),
                str(source),
            ],
        ]

        last_error = ""
        for args in attempts:
            code, output = self._run_with_output(args, progress_callback)
            if code == 0 and target.exists() and target.stat().st_size > 0:
                return target
            last_error = output.strip()

        raise RuntimeError(last_error or "KCC conversion failed")
