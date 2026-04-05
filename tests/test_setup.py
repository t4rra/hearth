from __future__ import annotations

from pathlib import Path

from hearth.core.settings import Settings
from hearth.sync.setup import (
    import_settings_from_device,
    merge_settings_with_conflict_choice,
)


def test_import_settings_from_usb_device_documents_hearth(tmp_path: Path) -> None:
    device_root = tmp_path / "kindle"
    settings_file = device_root / "documents" / "Hearth" / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(
        """
        {
          "opds_url": "https://example.test/opds",
          "auth_mode": "basic",
          "auth_username": "alice",
          "auth_password": "secret",
          "kcc_device": "KPW5"
        }
        """,
        encoding="utf-8",
    )

    loaded = import_settings_from_device(
        preferred_transport="usb",
        root_hint=str(device_root),
    )

    assert loaded is not None
    assert loaded.remote_path == "Hearth/settings.json"
    assert loaded.settings.opds_url == "https://example.test/opds"
    assert loaded.settings.auth_mode == "basic"
    assert loaded.settings.auth_username == "alice"
    assert loaded.settings.kcc_device == "KPW5"


def test_merge_settings_prefers_device_on_conflict() -> None:
    local = Settings(opds_url="https://local.test/opds", auth_mode="none")
    device = Settings(opds_url="https://device.test/opds", auth_mode="basic")

    merged, conflicts = merge_settings_with_conflict_choice(
        local_settings=local,
        device_settings=device,
        prefer_device_on_conflict=True,
    )

    assert conflicts == ["opds_url"]
    assert merged.opds_url == "https://device.test/opds"
    assert merged.auth_mode == "basic"


def test_merge_settings_prefers_local_on_conflict() -> None:
    local = Settings(opds_url="https://local.test/opds", auth_mode="none")
    device = Settings(opds_url="https://device.test/opds", auth_mode="basic")

    merged, conflicts = merge_settings_with_conflict_choice(
        local_settings=local,
        device_settings=device,
        prefer_device_on_conflict=False,
    )

    assert conflicts == ["opds_url"]
    assert merged.opds_url == "https://local.test/opds"
    assert merged.auth_mode == "basic"
