"""Settings page for Hearth GUI."""

from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QFileDialog,
    QCheckBox,
    QComboBox,
    QGroupBox,
    QMessageBox,
)

from ..core.config import SettingsManager
from ..sync.kindle_device import KindleDevice


class SettingsPage(QWidget):
    """Settings configuration page."""

    def __init__(self, kindle_device: Optional[KindleDevice] = None):
        super().__init__()
        self.settings_manager = SettingsManager()
        self._kindle_device = kindle_device
        self.init_ui()
        self.load_settings()

    def _get_kindle_device(self) -> KindleDevice:
        """Return a KindleDevice configured from current Settings values."""
        mount_value = self.kindle_path.text().strip()
        mount_path = Path(mount_value) if mount_value else None

        if self._kindle_device is None:
            self._kindle_device = KindleDevice(
                mount_path=mount_path,
                auto_mount_mtp=self.mtp_auto_mount.isChecked(),
                preferred_mtp_tool=self.mtp_tool.currentText(),
                auto_install_mtp_backend=self.mtp_auto_install.isChecked(),
            )
            return self._kindle_device

        # Keep the shared device in sync with current settings before actions.
        self._kindle_device.mount_path = mount_path
        self._kindle_device.auto_mount_mtp = self.mtp_auto_mount.isChecked()
        self._kindle_device.preferred_mtp_tool = self.mtp_tool.currentText()
        self._kindle_device.auto_install_mtp_backend = self.mtp_auto_install.isChecked()
        return self._kindle_device

    def init_ui(self):
        """Initialize UI elements."""
        layout = QVBoxLayout()
        self.setLayout(layout)

        # OPDS Server Settings
        opds_group = QGroupBox("OPDS Server")
        opds_layout = QVBoxLayout()

        opds_url_layout = QHBoxLayout()
        opds_url_layout.addWidget(QLabel("Server URL:"))
        self.opds_url = QLineEdit()
        opds_url_layout.addWidget(self.opds_url)
        opds_layout.addLayout(opds_url_layout)

        auth_layout = QHBoxLayout()
        auth_layout.addWidget(QLabel("Auth Type:"))
        self.opds_auth_type = QComboBox()
        self.opds_auth_type.addItems(["none", "basic", "bearer"])
        self.opds_auth_type.currentTextChanged.connect(self._update_auth_visibility)
        auth_layout.addWidget(self.opds_auth_type)
        auth_layout.addStretch()
        opds_layout.addLayout(auth_layout)

        user_layout = QHBoxLayout()
        user_layout.addWidget(QLabel("Username:"))
        self.opds_username = QLineEdit()
        user_layout.addWidget(self.opds_username)
        opds_layout.addLayout(user_layout)

        password_layout = QHBoxLayout()
        password_layout.addWidget(QLabel("Password:"))
        self.opds_password = QLineEdit()
        self.opds_password.setEchoMode(QLineEdit.EchoMode.Password)
        password_layout.addWidget(self.opds_password)
        opds_layout.addLayout(password_layout)

        token_layout = QHBoxLayout()
        token_layout.addWidget(QLabel("Bearer Token:"))
        self.opds_token = QLineEdit()
        self.opds_token.setEchoMode(QLineEdit.EchoMode.Password)
        token_layout.addWidget(self.opds_token)
        opds_layout.addLayout(token_layout)

        self._opds_user_layout = user_layout
        self._opds_password_layout = password_layout
        self._opds_token_layout = token_layout

        opds_group.setLayout(opds_layout)
        layout.addWidget(opds_group)

        # Kindle Settings
        kindle_group = QGroupBox("Kindle Device")
        kindle_layout = QVBoxLayout()

        # Mount path
        mount_layout = QHBoxLayout()
        mount_layout.addWidget(QLabel("Mount Path:"))
        self.kindle_path = QLineEdit()
        self.kindle_path.setReadOnly(True)
        mount_layout.addWidget(self.kindle_path)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_kindle_path)
        mount_layout.addWidget(browse_btn)
        detect_btn = QPushButton("Auto-Detect")
        detect_btn.clicked.connect(self.detect_kindle_path)
        mount_layout.addWidget(detect_btn)
        kindle_layout.addLayout(mount_layout)

        mtp_layout = QHBoxLayout()
        self.mtp_auto_mount = QCheckBox("Auto-mount MTP Kindle")
        mtp_layout.addWidget(self.mtp_auto_mount)
        self.mtp_auto_install = QCheckBox("Auto-install MTP backend")
        mtp_layout.addWidget(self.mtp_auto_install)
        mtp_layout.addWidget(QLabel("MTP Tool:"))
        self.mtp_tool = QComboBox()
        self.mtp_tool.addItems(
            ["auto", "go-mtpx", "go-mtpfs", "simple-mtpfs", "jmtpfs"]
        )
        mtp_layout.addWidget(self.mtp_tool)
        mtp_layout.addStretch()
        kindle_layout.addLayout(mtp_layout)

        kindle_group.setLayout(kindle_layout)
        layout.addWidget(kindle_group)

        # Conversion Settings
        conversion_group = QGroupBox("Conversion Settings")
        conversion_layout = QVBoxLayout()

        # Auto-convert
        self.auto_convert = QCheckBox("Auto-convert to MOBI format")
        conversion_layout.addWidget(self.auto_convert)

        # Keep originals
        self.keep_originals = QCheckBox("Keep original files after conversion")
        conversion_layout.addWidget(self.keep_originals)

        # Comic quality
        quality_layout = QHBoxLayout()
        quality_layout.addWidget(QLabel("Comic Quality:"))
        self.comic_quality = QComboBox()
        self.comic_quality.addItems(["High", "Medium", "Low"])
        quality_layout.addWidget(self.comic_quality)
        conversion_layout.addLayout(quality_layout)

        device_layout = QHBoxLayout()
        device_layout.addWidget(QLabel("Comic Device Profile:"))
        self.comic_device_profile = QComboBox()
        self.comic_device_profile.addItem("Kindle Standard (KS)", "KS")
        self.comic_device_profile.addItem("Kindle Paperwhite (KPW)", "KPW")
        self.comic_device_profile.addItem("Kindle Voyage (KV)", "KV")
        self.comic_device_profile.addItem("Kindle Oasis (KOA)", "KOA")
        self.comic_device_profile.addItem("Kindle Scribe (KS)", "KS")
        device_layout.addWidget(self.comic_device_profile)
        conversion_layout.addLayout(device_layout)

        direction_layout = QHBoxLayout()
        direction_layout.addWidget(QLabel("Reading Direction:"))
        self.manga_mode = QComboBox()
        self.manga_mode.addItem("Auto", "auto")
        self.manga_mode.addItem("Left-to-Right", "ltr")
        self.manga_mode.addItem("Right-to-Left", "rtl")
        direction_layout.addWidget(self.manga_mode)
        conversion_layout.addLayout(direction_layout)

        self.manga_auto_detect = QCheckBox("Auto-detect manga from metadata")
        conversion_layout.addWidget(self.manga_auto_detect)

        conversion_group.setLayout(conversion_layout)
        layout.addWidget(conversion_group)

        # Buttons
        button_layout = QHBoxLayout()
        reset_btn = QPushButton("Reset Config")
        reset_btn.clicked.connect(self.reset_config)
        button_layout.addWidget(reset_btn)
        save_btn = QPushButton("Save Settings")
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)
        button_layout.addStretch()
        layout.addLayout(button_layout)

        layout.addStretch()

    def load_settings(self):
        """Load settings from file."""
        settings = self.settings_manager.get_settings()
        self.opds_url.setText(settings.opds_url)
        self.opds_auth_type.setCurrentText(settings.opds_auth_type)
        self.opds_username.setText(settings.opds_username)
        self.opds_password.setText(settings.opds_password)
        self.opds_token.setText(settings.opds_token)
        self.kindle_path.setText(settings.kindle_mount_path)
        self.mtp_auto_mount.setChecked(settings.mtp_auto_mount)
        self.mtp_auto_install.setChecked(settings.mtp_auto_install_backend)
        self.mtp_tool.setCurrentText(settings.mtp_mount_tool)
        self.auto_convert.setChecked(settings.auto_convert)
        self.keep_originals.setChecked(settings.keep_originals)
        self._update_auth_visibility(self.opds_auth_type.currentText())

        quality_map = {"high": 0, "medium": 1, "low": 2}
        conversion_settings = settings.conversion_settings
        comic_quality = getattr(conversion_settings, "comic_quality", "high")
        self.comic_quality.setCurrentIndex(quality_map.get(comic_quality, 0))

        profile = getattr(conversion_settings, "comic_device_profile", "KS")
        profile_index = self.comic_device_profile.findData(profile)
        if profile_index >= 0:
            self.comic_device_profile.setCurrentIndex(profile_index)

        mode = getattr(conversion_settings, "manga_mode", "auto")
        mode_index = self.manga_mode.findData(mode)
        if mode_index >= 0:
            self.manga_mode.setCurrentIndex(mode_index)

        auto_detect = getattr(conversion_settings, "manga_auto_detect", True)
        self.manga_auto_detect.setChecked(bool(auto_detect))

    def save_settings(self):
        """Save settings to file."""
        quality_map = {0: "high", 1: "medium", 2: "low"}

        self.settings_manager.update_settings(
            opds_url=self.opds_url.text(),
            opds_auth_type=self.opds_auth_type.currentText(),
            opds_username=self.opds_username.text(),
            opds_password=self.opds_password.text(),
            opds_token=self.opds_token.text(),
            kindle_mount_path=self.kindle_path.text(),
            mtp_auto_mount=self.mtp_auto_mount.isChecked(),
            mtp_auto_install_backend=self.mtp_auto_install.isChecked(),
            mtp_mount_tool=self.mtp_tool.currentText(),
            auto_convert=self.auto_convert.isChecked(),
            keep_originals=self.keep_originals.isChecked(),
        )

        settings = self.settings_manager.get_settings()
        settings.conversion_settings.comic_quality = quality_map[
            self.comic_quality.currentIndex()
        ]
        profile = self.comic_device_profile.currentData()
        settings.conversion_settings.comic_device_profile = str(profile or "KS")
        mode = self.manga_mode.currentData()
        settings.conversion_settings.manga_mode = str(mode or "auto")
        settings.conversion_settings.manga_auto_detect = (
            self.manga_auto_detect.isChecked()
        )
        self.settings_manager.save_settings()

    def reset_config(self):
        """Reset Hearth settings to defaults after confirmation."""
        answer = QMessageBox.question(
            self,
            "Reset Configuration",
            "Reset all Hearth settings to defaults? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.settings_manager.reset_settings()
        self.load_settings()
        QMessageBox.information(
            self,
            "Configuration Reset",
            "Hearth settings were reset to defaults.",
        )

    def browse_kindle_path(self):
        """Browse for Kindle mount path."""
        path = QFileDialog.getExistingDirectory(
            self,
            "Select Kindle Mount Path",
        )
        if path:
            self.kindle_path.setText(path)

    def detect_kindle_path(self):
        """Auto-detect Kindle over USB/MTP and fill mount path when mounted."""
        device = self._get_kindle_device()
        if device.is_connected() and device.get_transport() == "mtp-libmtp":
            self.kindle_path.setText("")
            QMessageBox.information(
                self,
                "Kindle Detected",
                "Detected Kindle over MTP. No mount path is required.",
            )
            return

        path = device.get_mount_path()
        if path:
            self.kindle_path.setText(str(path))
            return

        selected = QFileDialog.getExistingDirectory(
            self,
            "Select Mounted Kindle",
        )
        if selected:
            self.kindle_path.setText(selected)

    def _update_auth_visibility(self, auth_type: str):
        """Show only fields relevant to selected OPDS auth mode."""
        show_basic = auth_type == "basic"
        show_bearer = auth_type == "bearer"

        for i in range(self._opds_user_layout.count()):
            widget = self._opds_user_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(show_basic)

        for i in range(self._opds_password_layout.count()):
            widget = self._opds_password_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(show_basic)

        for i in range(self._opds_token_layout.count()):
            widget = self._opds_token_layout.itemAt(i).widget()
            if widget:
                widget.setVisible(show_bearer)
