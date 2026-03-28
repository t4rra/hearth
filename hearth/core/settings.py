from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Literal

AuthMode = Literal["none", "basic", "bearer"]


@dataclass(slots=True)
class Settings:
    """User-configurable settings persisted to disk."""

    opds_url: str = ""
    auth_mode: AuthMode = "none"
    auth_username: str = ""
    auth_password: str = ""
    auth_bearer_token: str = ""
    kindle_transport: Literal["auto", "usb", "mtp"] = "auto"
    kindle_mount: str = ""
    desired_output: Literal["auto", "epub", "mobi"] = "auto"
    kcc_command: str = ""
    calibre_command: str = ""

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(**payload)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def auth_headers(self) -> dict[str, str]:
        if self.auth_mode == "bearer" and self.auth_bearer_token:
            return {"Authorization": f"Bearer {self.auth_bearer_token}"}
        return {}

    def basic_auth_credentials(self) -> tuple[str, str] | None:
        if self.auth_mode == "basic" and self.auth_username:
            return (self.auth_username, self.auth_password)
        return None


def sanitize_filename(name: str, max_len: int = 120) -> str:
    """Keep file names readable and safe across filesystems."""

    normalized = "".join("_" if c in '<>:"/\\|?*' else c for c in name).strip()
    if not normalized:
        normalized = "untitled"
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max_len - 1].rstrip() + "_"


def merge_overrides(base: Settings, overrides: dict[str, Any]) -> Settings:
    payload = asdict(base)
    payload.update({k: v for k, v in overrides.items() if v is not None})
    return Settings(**payload)
