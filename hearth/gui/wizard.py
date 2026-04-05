from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PyQt6.QtWidgets import (  # type: ignore[import-not-found]
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from hearth.core.settings import Settings
from hearth.sync.setup import (
    import_settings_from_device,
    merge_settings_with_conflict_choice,
    test_opds_connection,
)


class SetupWizard(QWizard):
    def __init__(self, base_settings: Settings, settings_path: Path, parent=None):
        super().__init__(parent)
        self._base_settings = base_settings
        self._working_settings = base_settings
        self._settings_path = settings_path
        self._import_attempted = False

        self.setWindowTitle("Welcome to Hearth")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)

        self._setup_device_page()
        self._setup_opds_page()
        self._setup_finish_page()
        self._populate_fields(base_settings)

    def result_settings(self) -> Settings:
        return self._settings_from_fields()

    def validateCurrentPage(self) -> bool:
        current_page = self.currentId()
        if current_page == 0 and self.import_checkbox.isChecked():
            self._attempt_device_import_once()
        if current_page == 1:
            settings = self._settings_from_fields()
            if not settings.opds_url.strip():
                QMessageBox.warning(
                    self,
                    "OPDS Required",
                    "Please enter an OPDS feed URL before finishing setup.",
                )
                return False
        if current_page == 2:
            self._refresh_summary()
        return True

    def _setup_device_page(self) -> None:
        page = QWizardPage()
        page.setTitle("Kindle Setup")
        page.setSubTitle(
            "Choose your Kindle model so Hearth can choose transport and KCC profile."
        )

        layout = QVBoxLayout()
        form = QFormLayout()

        self.kindle_model_combo = QComboBox()
        for label, transport, kcc in [
            ("Auto detect", "auto", "auto"),
            ("Kindle Paperwhite 1/2", "usb", "KPW"),
            ("Kindle Paperwhite 5/Signature Edition", "mtp", "KPW5"),
            ("Kindle Oasis 2/3", "mtp", "KO"),
            ("Kindle Voyage", "usb", "KV"),
            ("Kindle 11", "mtp", "K11"),
            ("Kindle Scribe 1/2", "mtp", "KS"),
        ]:
            self.kindle_model_combo.addItem(label, {"transport": transport, "kcc": kcc})

        self.transport_combo = QComboBox()
        self.transport_combo.addItems(["auto", "usb", "mtp"])

        self.mount_input = QLineEdit("")
        self.mount_input.setPlaceholderText("Optional: /Volumes/Kindle")

        self.import_checkbox = QCheckBox(
            "Try importing settings from documents/Hearth/settings.json"
        )
        self.import_checkbox.setChecked(True)

        form.addRow("Kindle model", self.kindle_model_combo)
        form.addRow("Transport", self.transport_combo)
        form.addRow("Mount hint", self.mount_input)

        layout.addLayout(form)
        layout.addWidget(self.import_checkbox)
        layout.addStretch(1)
        page.setLayout(layout)

        self.kindle_model_combo.currentIndexChanged.connect(
            self._on_kindle_model_changed
        )
        self.addPage(page)

    def _setup_opds_page(self) -> None:
        page = QWizardPage()
        page.setTitle("OPDS Settings")
        page.setSubTitle("Enter your OPDS endpoint and authentication details.")

        layout = QVBoxLayout()
        form = QFormLayout()

        self.feed_input = QLineEdit("")
        self.auth_mode_combo = QComboBox()
        self.auth_mode_combo.addItems(["none", "basic", "bearer"])
        self.auth_username_input = QLineEdit("")
        self.auth_password_input = QLineEdit("")
        self.auth_bearer_input = QLineEdit("")

        form.addRow("Feed URL", self.feed_input)
        form.addRow("Auth mode", self.auth_mode_combo)
        form.addRow("Username", self.auth_username_input)
        form.addRow("Password", self.auth_password_input)
        form.addRow("Bearer token", self.auth_bearer_input)
        layout.addLayout(form)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.test_opds_button = QPushButton("Test OPDS Connection")
        actions.addWidget(self.test_opds_button)
        layout.addLayout(actions)
        page.setLayout(layout)

        self.auth_mode_combo.currentTextChanged.connect(self._update_auth_visibility)
        self.test_opds_button.clicked.connect(self._test_opds_settings)
        self.addPage(page)

    def _setup_finish_page(self) -> None:
        page = QWizardPage()
        page.setTitle("Confirm")
        page.setSubTitle("Review setup values before saving.")

        layout = QVBoxLayout()
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        layout.addStretch(1)
        page.setLayout(layout)
        self.addPage(page)

    def _on_kindle_model_changed(self) -> None:
        payload = self.kindle_model_combo.currentData()
        if not isinstance(payload, dict):
            return
        transport = payload.get("transport", "auto")
        if isinstance(transport, str) and transport in {"auto", "usb", "mtp"}:
            self.transport_combo.setCurrentText(transport)

    def _settings_from_fields(self) -> Settings:
        payload = asdict(self._base_settings)
        payload.update(
            {
                "opds_url": self.feed_input.text().strip(),
                "auth_mode": self.auth_mode_combo.currentText().strip(),
                "auth_username": self.auth_username_input.text().strip(),
                "auth_password": self.auth_password_input.text(),
                "auth_bearer_token": self.auth_bearer_input.text().strip(),
                "kindle_transport": self.transport_combo.currentText().strip(),
                "kindle_mount": self.mount_input.text().strip(),
            }
        )
        model_payload = self.kindle_model_combo.currentData()
        if isinstance(model_payload, dict):
            kcc = model_payload.get("kcc")
            if isinstance(kcc, str) and kcc.strip():
                payload["kcc_device"] = kcc

        self._working_settings = Settings(**payload)
        return self._working_settings

    def _populate_fields(self, settings: Settings) -> None:
        self.feed_input.setText(settings.opds_url)
        self.auth_mode_combo.setCurrentText(settings.auth_mode)
        self.auth_username_input.setText(settings.auth_username)
        self.auth_password_input.setText(settings.auth_password)
        self.auth_bearer_input.setText(settings.auth_bearer_token)
        self.transport_combo.setCurrentText(settings.kindle_transport)
        self.mount_input.setText(settings.kindle_mount)

        for idx in range(self.kindle_model_combo.count()):
            payload = self.kindle_model_combo.itemData(idx)
            if not isinstance(payload, dict):
                continue
            kcc = payload.get("kcc")
            if isinstance(kcc, str) and kcc.upper() == settings.kcc_device.upper():
                self.kindle_model_combo.setCurrentIndex(idx)
                break

        self._update_auth_visibility()
        self._refresh_summary()

    def _attempt_device_import_once(self) -> None:
        if self._import_attempted:
            return
        self._import_attempted = True

        imported = import_settings_from_device(
            preferred_transport=self.transport_combo.currentText(),
            root_hint=self.mount_input.text().strip(),
        )
        if imported is None:
            return

        local_settings = self._settings_from_fields()
        merged, conflicts = merge_settings_with_conflict_choice(
            local_settings=local_settings,
            device_settings=imported.settings,
            prefer_device_on_conflict=True,
        )

        if conflicts:
            message = QMessageBox(self)
            message.setIcon(QMessageBox.Icon.Question)
            message.setWindowTitle("Conflicting Settings Found")
            message.setText(
                "Settings on the connected Kindle conflict with local setup values."
            )
            message.setInformativeText(
                "Choose which settings to keep for conflicting keys.\n\n"
                f"Conflicts: {', '.join(conflicts)}"
            )
            use_device_button = message.addButton(
                "Use settings on device",
                QMessageBox.ButtonRole.AcceptRole,
            )
            use_local_button = message.addButton(
                "Use current settings on this computer",
                QMessageBox.ButtonRole.RejectRole,
            )
            message.exec()

            prefer_device = message.clickedButton() == use_device_button
            if message.clickedButton() not in {use_device_button, use_local_button}:
                return

            merged, _ = merge_settings_with_conflict_choice(
                local_settings=local_settings,
                device_settings=imported.settings,
                prefer_device_on_conflict=prefer_device,
            )

        self._base_settings = merged
        self._working_settings = merged
        self._populate_fields(merged)
        QMessageBox.information(
            self,
            "Settings Imported",
            (
                "Imported settings from Kindle path "
                f"{imported.remote_path} and applied them to this setup."
            ),
        )

    def _test_opds_settings(self) -> None:
        settings = self._settings_from_fields()
        ok, message = test_opds_connection(settings)
        if ok:
            QMessageBox.information(self, "OPDS Test", message)
            return
        QMessageBox.warning(self, "OPDS Test Failed", message)

    def _update_auth_visibility(self) -> None:
        mode = self.auth_mode_combo.currentText()
        self.auth_username_input.setEnabled(mode == "basic")
        self.auth_password_input.setEnabled(mode == "basic")
        self.auth_bearer_input.setEnabled(mode == "bearer")

    def _refresh_summary(self) -> None:
        settings = self._settings_from_fields()
        self.summary_label.setText(
            "\n".join(
                [
                    f"Settings file: {self._settings_path}",
                    f"Kindle transport: {settings.kindle_transport}",
                    f"KCC device profile: {settings.kcc_device}",
                    f"OPDS URL: {settings.opds_url or '(not set)'}",
                    f"Auth mode: {settings.auth_mode}",
                ]
            )
        )
