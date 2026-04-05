from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any


class MTPBackendError(RuntimeError):
    pass


@dataclass(slots=True)
class MTPRemoteFile:
    file_id: int
    name: str
    path: str
    size: int
    is_dir: bool = False


class LibmtpCLIBackend:
    """Persistent go-mtpx bridge backend for non-mounted MTP access."""

    def __init__(self) -> None:
        self.go_cmd = self._resolve_go_command()

        default_hearth_home = str(Path.home() / ".hearth")
        hearth_home = Path(os.environ.get("HEARTH_HOME", default_hearth_home))
        self._bridge_dir = self._resolve_bridge_dir(hearth_home)
        self._bridge_build_dir = hearth_home / "mtpx_bridge" / ".build"
        self._bridge_bin = self._bridge_build_dir / "hearth-mtpx-bridge"

        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._request_id = 0

        self._last_probe_ok = False
        self._last_probe_ts = 0.0
        self._sticky_seconds = 90.0
        self._last_detect_info = ""

        self._list_cache: list[MTPRemoteFile] = []
        self._list_cache_ts = 0.0
        self._list_cache_ttl = 6.0

    def diagnostics(self) -> dict[str, str | bool]:
        return {
            "detect_cmd": self.go_cmd or "",
            "files_cmd": str(self._bridge_bin),
            "folders_cmd": str(self._bridge_bin),
            "get_cmd": str(self._bridge_bin),
            "send_cmd": str(self._bridge_bin),
            "delete_cmd": str(self._bridge_bin),
            "available": self.available(),
            "install_hint": (
                "Install Go + libusb and ensure mtpx bridge sources "
                "exist under ~/.hearth/vendor/mtpx_bridge"
            ),
        }

    def available(self) -> bool:
        return bool(self.go_cmd and self._bridge_dir.exists())

    @staticmethod
    def _resolve_go_command() -> str | None:
        go_cmd = shutil.which("go")
        if go_cmd:
            return go_cmd

        candidates = [
            Path("/opt/homebrew/bin/go"),
            Path("/usr/local/bin/go"),
        ]
        for candidate in candidates:
            if candidate.exists() and candidate.is_file():
                return str(candidate)
        return None

    @staticmethod
    def _has_bridge_sources(path: Path) -> bool:
        required = [path / "go.mod", path / "main.go"]
        return all(item.exists() and item.is_file() for item in required)

    @classmethod
    def _resolve_bridge_dir(cls, hearth_home: Path) -> Path:
        env_override = os.environ.get("HEARTH_MTPX_BRIDGE_DIR", "").strip()
        candidates: list[Path] = []
        if env_override:
            candidates.append(Path(env_override))

        candidates.extend(
            [
                hearth_home / "vendor" / "mtpx_bridge",
                Path(__file__).parent / "mtpx_bridge",
            ]
        )

        for candidate in candidates:
            if cls._has_bridge_sources(candidate):
                return candidate

        if env_override:
            return Path(env_override)
        return hearth_home / "vendor" / "mtpx_bridge"

    def detect_device(self) -> bool:
        now = time.time()
        if self._last_probe_ok and (now - self._last_probe_ts) < self._sticky_seconds:
            return True

        if not self.available():
            return False

        try:
            result = self._rpc("detect", {})
        except MTPBackendError:
            if not self._last_probe_ok:
                self._last_probe_ts = now
            return False

        ok = bool(result.get("detected"))
        self._last_detect_info = str(result.get("device_info", ""))
        self._last_probe_ok = ok
        self._last_probe_ts = now
        return ok

    def detected_device_info(self) -> str:
        return self._last_detect_info

    def list_files(self) -> list[MTPRemoteFile]:
        now = time.time()
        if (now - self._list_cache_ts) < self._list_cache_ttl:
            return list(self._list_cache)

        result = self._rpc("list", {"base_path": "/"})
        raw_files = result.get("files")
        if not isinstance(raw_files, list):
            raise MTPBackendError("Invalid list response from go-mtpx bridge")

        rows: list[MTPRemoteFile] = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                continue
            raw_path = str(raw.get("path", "")).strip()
            if not raw_path:
                continue
            path = str(Path("/" + raw_path.lstrip("/")))
            if path in {"", "/"}:
                continue
            rows.append(
                MTPRemoteFile(
                    file_id=int(raw.get("id", 0)),
                    name=Path(path).name,
                    path=path,
                    size=int(raw.get("size", 0)),
                    is_dir=bool(raw.get("is_dir", False)),
                )
            )

        self._list_cache = rows
        self._list_cache_ts = now
        return list(rows)

    def download_file_by_name(self, name: str, target: Path) -> Path:
        remote = self._find_file(name)
        if remote is None:
            raise MTPBackendError(f"Remote MTP file not found: {name}")
        if remote.is_dir:
            raise MTPBackendError(f"Remote path is a folder: {name}")

        target.parent.mkdir(parents=True, exist_ok=True)
        self._rpc(
            "download",
            {
                "path": remote.path,
                "destination": str(target),
                "base_path": "/documents",
            },
        )
        return target

    def upload_file(self, local_path: Path, remote_name: str) -> None:
        payload_path = local_path
        temp_dir: tempfile.TemporaryDirectory[str] | None = None

        if local_path.name != remote_name:
            temp_dir = tempfile.TemporaryDirectory(prefix="hearth-mtp-upload-")
            payload_path = Path(temp_dir.name) / remote_name
            payload_path.parent.mkdir(parents=True, exist_ok=True)
            payload_path.write_bytes(local_path.read_bytes())

        try:
            self._rpc(
                "upload",
                {
                    "source": str(payload_path),
                    "path": remote_name,
                    "base_path": "/documents",
                },
            )
            self._invalidate_list_cache()
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    def delete_file_by_name(self, name: str) -> bool:
        remote = self._find_file(name, allow_missing=True)
        if remote is None:
            return False

        if remote.is_dir:
            prefix = remote.path.rstrip("/") + "/"
            children = [
                entry for entry in self.list_files() if entry.path.startswith(prefix)
            ]
            children.sort(key=lambda entry: entry.path.count("/"), reverse=True)
            for child in children:
                self._rpc(
                    "delete",
                    {
                        "path": child.path,
                        "base_path": "/",
                    },
                )

        self._rpc(
            "delete",
            {
                "path": remote.path,
                "base_path": "/",
            },
        )
        self._invalidate_list_cache()
        return True

    def close(self) -> None:
        with self._lock:
            if self._process is None:
                return

            try:
                self._write_request({"id": 0, "method": "close", "params": {}})
            except Exception:  # pylint: disable=broad-exception-caught
                pass

            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()

            self._process = None

    def _invalidate_list_cache(self) -> None:
        self._list_cache = []
        self._list_cache_ts = 0.0

    def _find_file(
        self,
        name: str,
        allow_missing: bool = False,
    ) -> MTPRemoteFile | None:
        last_error: Exception | None = None
        for _ in range(3):
            try:
                files = self.list_files()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_error = exc
                time.sleep(0.2)
                continue

            for item in files:
                query = name.strip("/")
                item_path = item.path.strip("/")
                if item.name == query or item_path == query:
                    return item
                if item_path.endswith("/" + query):
                    return item
            break

        if allow_missing:
            return None

        if last_error is not None:
            raise MTPBackendError(str(last_error)) from last_error
        raise MTPBackendError(f"Remote MTP file not found: {name}")

    def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._ensure_process()

            assert self._process is not None
            self._request_id += 1
            payload = {
                "id": self._request_id,
                "method": method,
                "params": params,
            }

            try:
                self._write_request(payload)
                response = self._read_response()
            except MTPBackendError:
                self._restart_process()
                if self._process is None:
                    raise
                self._write_request(payload)
                response = self._read_response()

            if not response.get("ok", False):
                message = str(response.get("error") or "MTP bridge error")
                raise MTPBackendError(message)

            result = response.get("result", {})
            if not isinstance(result, dict):
                return {"value": result}
            return result

    def _write_request(self, payload: dict[str, Any]) -> None:
        if self._process is None or self._process.stdin is None:
            raise MTPBackendError("MTP bridge process is not available")

        line = json.dumps(payload, separators=(",", ":")) + "\n"
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except OSError as exc:
            raise MTPBackendError(str(exc)) from exc

    def _read_response(self) -> dict[str, Any]:
        if self._process is None or self._process.stdout is None:
            raise MTPBackendError("MTP bridge process is not available")

        line = self._process.stdout.readline()
        if not line:
            stderr = ""
            if self._process.stderr is not None:
                stderr = self._process.stderr.read().strip()
            raise MTPBackendError(stderr or "MTP bridge terminated unexpectedly")

        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MTPBackendError(f"Invalid MTP bridge response: {line}") from exc

        if not isinstance(decoded, dict):
            raise MTPBackendError("Invalid MTP bridge response payload")
        return decoded

    def _ensure_process(self) -> None:
        if not self.available():
            raise MTPBackendError(
                "go-mtpx bridge is unavailable; install Go and libusb"
            )

        if self._process is not None and self._process.poll() is None:
            return

        self._build_bridge_if_needed()

        if not self._bridge_bin.exists():
            raise MTPBackendError(
                f"go-mtpx bridge binary not found: {self._bridge_bin}"
            )

        try:
            self._process = subprocess.Popen(
                [str(self._bridge_bin)],
                cwd=str(self._bridge_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise MTPBackendError(str(exc)) from exc

    def _restart_process(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._process = None

    def _build_bridge_if_needed(self) -> None:
        source_files = [
            self._bridge_dir / "go.mod",
            self._bridge_dir / "main.go",
        ]

        if not all(path.exists() for path in source_files):
            raise MTPBackendError("go-mtpx bridge source files are missing")

        needs_build = not self._bridge_bin.exists()
        if not needs_build:
            bin_mtime = self._bridge_bin.stat().st_mtime
            for source in source_files:
                if source.stat().st_mtime > bin_mtime:
                    needs_build = True
                    break

        if not needs_build:
            return

        self._bridge_bin.parent.mkdir(parents=True, exist_ok=True)

        self._run_go(["mod", "tidy"])
        self._run_go(
            [
                "build",
                "-o",
                str(self._bridge_bin),
                ".",
            ]
        )

    def _run_go(self, args: list[str]) -> None:
        if not self.go_cmd:
            raise MTPBackendError("Go compiler not found")

        command = [self.go_cmd, *args]
        try:
            result = subprocess.run(
                command,
                cwd=str(self._bridge_dir),
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MTPBackendError(str(exc)) from exc

        output = (result.stdout or "") + "\n" + (result.stderr or "")
        if result.returncode != 0:
            raise MTPBackendError(output.strip() or "go command failed")
