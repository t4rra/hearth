"""Configuration and settings management for Hearth."""

import json
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class ConversionSettings:
    """Settings for format conversion."""

    comic_format: str = "MOBI"  # Target format for comics
    ebook_format: str = "MOBI"  # Target format for ebooks
    comic_quality: str = "high"  # Quality preset for KCC
    remove_margins: bool = True  # Remove margins for Kindle display

    def to_dict(self):
        return asdict(self)


@dataclass
class HearthSettings:
    """Main configuration for Hearth."""

    opds_url: str = ""
    opds_auth_type: str = "none"  # none, basic, bearer
    opds_username: str = ""
    opds_password: str = ""
    opds_token: str = ""
    kindle_mount_path: str = ""
    mtp_auto_mount: bool = True
    mtp_auto_install_backend: bool = True
    # auto, go-mtpx, go-mtpfs, simple-mtpfs, jmtpfs
    mtp_mount_tool: str = "auto"
    sync_enabled: bool = True
    auto_convert: bool = True
    conversion_settings: ConversionSettings = field(default_factory=ConversionSettings)
    keep_originals: bool = True
    metadata_file: str = ".hearth_metadata.json"

    def to_dict(self):
        result = asdict(self)
        result["conversion_settings"] = self.conversion_settings.to_dict()
        return result


class SettingsManager:
    """Manages persistent settings for Hearth."""

    def __init__(self, config_dir: Optional[Path] = None):
        if config_dir is None:
            config_dir = Path.home() / ".config" / "hearth"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "settings.json"
        self.settings = self._load_settings()

    def _load_settings(self) -> HearthSettings:
        """Load settings from file or create defaults."""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                conversion_data = data.pop("conversion_settings", {})
                settings = HearthSettings(**data)
                conv_settings = ConversionSettings(**conversion_data)
                settings.conversion_settings = conv_settings
                return settings
            except (IOError, json.JSONDecodeError) as error:
                msg = f"Error loading settings: {error}. Using defaults."
                print(msg)
        return HearthSettings()

    def save_settings(self) -> None:
        """Save current settings to file."""
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self.settings.to_dict(), f, indent=2)

    def get_settings(self) -> HearthSettings:
        """Get current settings."""
        return self.settings

    def update_settings(self, **kwargs) -> None:
        """Update settings and save to file."""
        for key, value in kwargs.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save_settings()

    def reset_settings(self) -> None:
        """Reset settings to defaults and persist to disk."""
        self.settings = HearthSettings()
        self.save_settings()
