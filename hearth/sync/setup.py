from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
from pathlib import Path
import tempfile

from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings

from .device import KindleDevice


_DEFAULT_SETTINGS = Settings()
_DEFAULT_VALUES = asdict(_DEFAULT_SETTINGS)
_SETTINGS_FIELDS = {item.name for item in fields(Settings)}


@dataclass(slots=True)
class DeviceSettingsImport:
    settings: Settings
    device: KindleDevice
    remote_path: str


def detect_kcc_profile_for_device(device: KindleDevice) -> str:
    if device.transport != "mtp":
        return ""

    info = KindleDevice.mtp_backend().detected_device_info().lower()
    if not info:
        return ""

    if "scribe" in info:
        return "KS"
    if "oasis" in info:
        return "KO"
    if "paperwhite 5" in info or "signature" in info:
        return "KPW5"
    if "voyage" in info:
        return "KV"
    if "paperwhite" in info:
        return "KPW"
    if "kindle 11" in info:
        return "K11"
    if "kindle" in info:
        return "KPW"
    return ""


def test_opds_connection(settings: Settings) -> tuple[bool, str]:
    feed_url = settings.opds_url.strip()
    if not feed_url:
        return (False, "OPDS URL is required")

    try:
        session = OPDSSession(settings)
        client = OPDSClient(session)
        client.fetch_entries(feed_url)
    except Exception as exc:  # pragma: no cover - exercised via mocks in tests
        return (False, str(exc))

    return (True, "Connection successful")


def import_settings_from_device(
    preferred_transport: str,
    root_hint: str,
    remote_candidates: list[str] | None = None,
) -> DeviceSettingsImport | None:
    device = KindleDevice.detect(
        preferred=preferred_transport,
        root_hint=root_hint,
    )
    if device is None:
        return None

    candidates = remote_candidates or [
        "Hearth/settings.json",
        "Hearth/.hearth_settings.json",
        "Hearth/hearth-settings.json",
    ]

    with tempfile.TemporaryDirectory(prefix="hearth-device-settings-") as temp_dir:
        for remote_path in candidates:
            temp_path = Path(temp_dir) / "settings.json"
            try:
                device.download_file(remote_path, temp_path)
                payload = json.loads(temp_path.read_text(encoding="utf-8"))
                settings = settings_from_payload(payload)
            except (OSError, RuntimeError, json.JSONDecodeError, TypeError, ValueError):
                continue
            return DeviceSettingsImport(
                settings=settings,
                device=device,
                remote_path=remote_path,
            )

    return None


def settings_from_payload(payload: object) -> Settings:
    if not isinstance(payload, dict):
        raise TypeError("Settings payload must be a JSON object")
    filtered = {key: value for key, value in payload.items() if key in _SETTINGS_FIELDS}
    return Settings(**filtered)


def merge_settings_with_conflict_choice(
    local_settings: Settings,
    device_settings: Settings,
    prefer_device_on_conflict: bool,
) -> tuple[Settings, list[str]]:
    local_payload = asdict(local_settings)
    device_payload = asdict(device_settings)
    merged = dict(local_payload)
    conflicts: list[str] = []

    for key in _SETTINGS_FIELDS:
        local_value = local_payload[key]
        device_value = device_payload[key]
        default_value = _DEFAULT_VALUES[key]

        if local_value == device_value:
            merged[key] = local_value
            continue

        local_is_default = local_value == default_value
        device_is_default = device_value == default_value

        if local_is_default and not device_is_default:
            merged[key] = device_value
            continue
        if device_is_default and not local_is_default:
            merged[key] = local_value
            continue

        conflicts.append(key)
        merged[key] = device_value if prefer_device_on_conflict else local_value

    return (Settings(**merged), sorted(conflicts))
