from __future__ import annotations

# pylint: disable=import-error

from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any, Callable, Literal, cast

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from hearth.converters.manager import ConverterManager
from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import KindleDevice
from hearth.sync.manager import SyncItem, SyncManager
from hearth.sync.metadata import load_metadata

from .workers import WorkerPool


@dataclass(slots=True)
class LibraryRow:
    id: str
    title: str
    author: str
    download_url: str
    declared_type: str


@dataclass(slots=True)
class PendingTask:
    future: Future[Any]
    on_success: Callable[[object], None]
    action_name: str


@dataclass(slots=True)
class DeviceSnapshot:
    transport: str
    root: Path


class HearthMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hearth")
        self.resize(1200, 800)

        self.worker_pool = WorkerPool(max_workers=3)
        self.pending_tasks: list[PendingTask] = []
        self.connected_device: DeviceSnapshot | None = None
        self.is_busy = False

        self.settings_path_input = QLineEdit(".hearth/settings.json")
        self.workspace_input = QLineEdit(".hearth")
        self.download_dir_input = QLineEdit(str(Path.home() / "Downloads"))

        self.feed_input = QLineEdit("")
        self.auth_mode_combo = QComboBox()
        self.auth_mode_combo.addItems(["none", "basic", "bearer"])
        self.auth_username_input = QLineEdit("")
        self.auth_password_input = QLineEdit("")
        self.auth_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.auth_bearer_input = QLineEdit("")
        self.auth_bearer_input.setEchoMode(QLineEdit.EchoMode.Password)

        self.transport_combo = QComboBox()
        self.transport_combo.addItems(["auto", "usb", "mtp"])
        self.kindle_root_input = QLineEdit("")
        self.desired_output_combo = QComboBox()
        self.desired_output_combo.addItems(["auto", "epub", "mobi"])
        self.kcc_command_input = QLineEdit("")
        self.calibre_command_input = QLineEdit("")

        self.load_settings_button = QPushButton("Load")
        self.save_settings_button = QPushButton("Save")
        self.probe_kindle_button = QPushButton("Probe Kindle")

        self.load_library_button = QPushButton("Load Library")
        self.sync_selected_button = QPushButton("Sync Selected")
        self.select_all_library_button = QPushButton("Select All")
        self.clear_library_selection_button = QPushButton("Clear")
        self.force_checkbox = QCheckBox("Force re-sync")

        self.refresh_files_button = QPushButton("Refresh Files")
        self.download_selected_file_button = QPushButton("Download Selected")
        self.delete_selected_file_button = QPushButton("Delete Selected")

        self.status_label = QLabel("Idle")
        self.kindle_status_label = QLabel("Kindle: probing...")

        self.library_table = QTableWidget(0, 5)
        self.library_table.setHorizontalHeaderLabels(
            ["Sync", "Title", "Author", "Type", "Status"]
        )

        self.kindle_files_table = QTableWidget(0, 2)
        self.kindle_files_table.setHorizontalHeaderLabels(["Filename", "Size"])

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        self.tabs = QTabWidget()
        self.library_tab = QWidget()
        self.kindle_files_tab = QWidget()
        self.settings_tab = QWidget()

        self._configure_layout()
        self._connect_events()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(100)
        self.poll_timer.timeout.connect(self._poll_pending_tasks)
        self.poll_timer.start()

        self._load_settings_from_file()
        self._probe_kindle()

    def _configure_layout(self) -> None:
        central = QWidget()
        root = QVBoxLayout()

        header = QHBoxLayout()
        header.addWidget(QLabel("Settings file"))
        header.addWidget(self.settings_path_input)
        header.addWidget(self.load_settings_button)
        header.addWidget(self.save_settings_button)
        header.addSpacing(12)
        header.addWidget(self.probe_kindle_button)
        header.addSpacing(12)
        header.addWidget(self.kindle_status_label)
        header.addStretch(1)
        header.addWidget(self.status_label)

        self._configure_library_tab()
        self._configure_kindle_files_tab()
        self._configure_settings_tab()

        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.kindle_files_tab, "Kindle Files")
        self.tabs.addTab(self.settings_tab, "Settings")

        root.addLayout(header)
        root.addWidget(self.tabs, stretch=5)
        root.addWidget(QLabel("Log"))
        root.addWidget(self.log_output, stretch=2)

        central.setLayout(root)
        self.setCentralWidget(central)

    def _configure_library_tab(self) -> None:
        layout = QVBoxLayout()

        controls = QHBoxLayout()
        controls.addWidget(self.load_library_button)
        controls.addWidget(self.sync_selected_button)
        controls.addWidget(self.select_all_library_button)
        controls.addWidget(self.clear_library_selection_button)
        controls.addWidget(self.force_checkbox)
        controls.addStretch(1)

        library_header = self.library_table.horizontalHeader()
        if library_header is not None:
            library_header.setStretchLastSection(True)
        library_vertical = self.library_table.verticalHeader()
        if library_vertical is not None:
            library_vertical.setVisible(False)

        layout.addLayout(controls)
        layout.addWidget(self.library_table)

        self.library_tab.setLayout(layout)

    def _configure_kindle_files_tab(self) -> None:
        layout = QVBoxLayout()

        controls = QGridLayout()
        controls.addWidget(QLabel("Download folder"), 0, 0)
        controls.addWidget(self.download_dir_input, 0, 1, 1, 3)
        controls.addWidget(self.refresh_files_button, 1, 0)
        controls.addWidget(self.download_selected_file_button, 1, 1)
        controls.addWidget(self.delete_selected_file_button, 1, 2)

        files_header = self.kindle_files_table.horizontalHeader()
        if files_header is not None:
            files_header.setStretchLastSection(True)
        files_vertical = self.kindle_files_table.verticalHeader()
        if files_vertical is not None:
            files_vertical.setVisible(False)

        self.kindle_files_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.kindle_files_table.setSelectionMode(
            QTableWidget.SelectionMode.ExtendedSelection
        )

        layout.addLayout(controls)
        layout.addWidget(self.kindle_files_table)

        self.kindle_files_tab.setLayout(layout)

    def _configure_settings_tab(self) -> None:
        layout = QGridLayout()

        layout.addWidget(QLabel("Workspace"), 0, 0)
        layout.addWidget(self.workspace_input, 0, 1, 1, 3)

        layout.addWidget(QLabel("OPDS feed URL"), 1, 0)
        layout.addWidget(self.feed_input, 1, 1, 1, 3)

        layout.addWidget(QLabel("Auth mode"), 2, 0)
        layout.addWidget(self.auth_mode_combo, 2, 1)
        layout.addWidget(QLabel("Username"), 2, 2)
        layout.addWidget(self.auth_username_input, 2, 3)

        layout.addWidget(QLabel("Password"), 3, 0)
        layout.addWidget(self.auth_password_input, 3, 1)
        layout.addWidget(QLabel("Bearer token"), 3, 2)
        layout.addWidget(self.auth_bearer_input, 3, 3)

        layout.addWidget(QLabel("Kindle transport"), 4, 0)
        layout.addWidget(self.transport_combo, 4, 1)
        layout.addWidget(QLabel("Kindle mount"), 4, 2)
        layout.addWidget(self.kindle_root_input, 4, 3)

        layout.addWidget(QLabel("Preferred output"), 5, 0)
        layout.addWidget(self.desired_output_combo, 5, 1)
        layout.addWidget(QLabel("KCC command"), 5, 2)
        layout.addWidget(self.kcc_command_input, 5, 3)

        layout.addWidget(QLabel("Calibre command"), 6, 0)
        layout.addWidget(self.calibre_command_input, 6, 1, 1, 3)

        settings_widget = QWidget()
        settings_widget.setLayout(layout)
        wrapper = QVBoxLayout()
        wrapper.addWidget(settings_widget)
        wrapper.addStretch(1)

        final_widget = QWidget()
        final_widget.setLayout(wrapper)
        tab_layout = QVBoxLayout()
        tab_layout.addWidget(final_widget)
        self.settings_tab.setLayout(tab_layout)

    def _connect_events(self) -> None:
        self.load_settings_button.clicked.connect(self._load_settings_from_file)
        self.save_settings_button.clicked.connect(self._save_settings_to_file)
        self.probe_kindle_button.clicked.connect(self._probe_kindle)

        self.auth_mode_combo.currentTextChanged.connect(self._update_auth_visibility)

        self.load_library_button.clicked.connect(self._load_library)
        self.sync_selected_button.clicked.connect(self._sync_selected)
        self.select_all_library_button.clicked.connect(self._select_all_library_rows)
        self.clear_library_selection_button.clicked.connect(
            self._clear_library_selection
        )

        self.refresh_files_button.clicked.connect(self._refresh_kindle_files)
        self.download_selected_file_button.clicked.connect(
            self._download_selected_kindle_files
        )
        self.delete_selected_file_button.clicked.connect(
            self._delete_selected_kindle_files
        )

    def _load_settings_from_file(self) -> None:
        settings_path = Path(self.settings_path_input.text().strip())
        settings = Settings.load(settings_path)

        self.feed_input.setText(settings.opds_url)
        self.auth_mode_combo.setCurrentText(settings.auth_mode)
        self.auth_username_input.setText(settings.auth_username)
        self.auth_password_input.setText(settings.auth_password)
        self.auth_bearer_input.setText(settings.auth_bearer_token)
        self.transport_combo.setCurrentText(settings.kindle_transport)
        self.kindle_root_input.setText(settings.kindle_mount)
        self.desired_output_combo.setCurrentText(settings.desired_output)
        self.kcc_command_input.setText(settings.kcc_command)
        self.calibre_command_input.setText(settings.calibre_command)

        self._update_auth_visibility()
        self._log(f"Loaded settings from {settings_path}")

    def _save_settings_to_file(self) -> None:
        settings_path = Path(self.settings_path_input.text().strip())
        settings = self._current_settings()
        settings.save(settings_path)
        self._log(f"Saved settings to {settings_path}")

    def _current_settings(self) -> Settings:
        auth_mode = cast(
            Literal["none", "basic", "bearer"],
            self.auth_mode_combo.currentText(),
        )
        transport = cast(
            Literal["auto", "usb", "mtp"],
            self.transport_combo.currentText(),
        )
        desired_output = cast(
            Literal["auto", "epub", "mobi"],
            self.desired_output_combo.currentText(),
        )

        return Settings(
            opds_url=self.feed_input.text().strip(),
            auth_mode=auth_mode,
            auth_username=self.auth_username_input.text().strip(),
            auth_password=self.auth_password_input.text(),
            auth_bearer_token=self.auth_bearer_input.text().strip(),
            kindle_transport=transport,
            kindle_mount=self.kindle_root_input.text().strip(),
            desired_output=desired_output,
            kcc_command=self.kcc_command_input.text().strip(),
            calibre_command=self.calibre_command_input.text().strip(),
        )

    def _update_auth_visibility(self) -> None:
        mode = self.auth_mode_combo.currentText()
        is_basic = mode == "basic"
        is_bearer = mode == "bearer"

        self.auth_username_input.setEnabled(is_basic)
        self.auth_password_input.setEnabled(is_basic)
        self.auth_bearer_input.setEnabled(is_bearer)

    def _probe_kindle(self) -> None:
        if self.is_busy:
            return
        settings = self._current_settings()
        self._set_busy("Probing Kindle...")
        future = self.worker_pool.submit(self._probe_kindle_worker, settings)
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_probe_kindle_result,
                action_name="probe kindle",
            )
        )

    def _probe_kindle_worker(
        self,
        settings: Settings,
    ) -> DeviceSnapshot | None:
        device = KindleDevice.detect(
            preferred=settings.kindle_transport,
            root_hint=settings.kindle_mount,
        )
        if device is None:
            return None
        return DeviceSnapshot(transport=device.transport, root=device.root)

    def _on_probe_kindle_result(self, result: object) -> None:
        if result is None:
            self.connected_device = None
            self.kindle_status_label.setText("Kindle: not connected")
            self._log("Kindle not detected")
            return

        if not isinstance(result, DeviceSnapshot):
            raise TypeError("Unexpected probe result type")

        self.connected_device = result
        self.kindle_status_label.setText(f"Kindle: {result.transport} at {result.root}")
        self._log(f"Detected Kindle at {result.root}")
        self._refresh_kindle_files()

    def _load_library(self) -> None:
        if self.is_busy:
            return

        feed_url = self.feed_input.text().strip()
        if not feed_url:
            QMessageBox.warning(
                self,
                "Missing feed URL",
                "Set an OPDS URL in Settings first.",
            )
            return

        settings = self._current_settings()
        self._set_busy("Loading library...")
        future = self.worker_pool.submit(
            self._fetch_library_rows,
            settings,
            feed_url,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._populate_library_table,
                action_name="load library",
            )
        )

    def _fetch_library_rows(
        self,
        settings: Settings,
        feed_url: str,
    ) -> list[LibraryRow]:
        session = OPDSSession(settings)
        client = OPDSClient(session)

        rows: list[LibraryRow] = []
        for entry, link in client.crawl_acquisitions(feed_url):
            rows.append(
                LibraryRow(
                    id=entry.id,
                    title=entry.title,
                    author=entry.author,
                    download_url=link.href,
                    declared_type=link.type,
                )
            )
        return rows

    def _populate_library_table(self, result: object) -> None:
        if not isinstance(result, list):
            raise TypeError("Unexpected result type for library loading")

        records = {}
        device_files: set[str] = set()
        if self.connected_device is not None:
            device = KindleDevice(
                transport=self.connected_device.transport,
                root=self.connected_device.root,
            )
            metadata_path = device.documents_dir / ".hearth_metadata.json"
            records = load_metadata(metadata_path)
            device_files = {p.name for p in device.list_files()}

        rows: list[LibraryRow] = result
        self.library_table.setRowCount(len(rows))
        for idx, row in enumerate(rows):
            check_item = QTableWidgetItem("")
            check_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check_item.setCheckState(Qt.CheckState.Unchecked)
            check_item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "id": row.id,
                    "title": row.title,
                    "download_url": row.download_url,
                    "declared_type": row.declared_type,
                },
            )

            status_text = "Not Synced"
            if row.id in records:
                record = records[row.id]
                if record.device_filename in device_files and record.on_device:
                    status_text = "On Device"
                elif record.desired:
                    status_text = "Wanted"

            self.library_table.setItem(idx, 0, check_item)
            self.library_table.setItem(idx, 1, QTableWidgetItem(row.title))
            self.library_table.setItem(
                idx,
                2,
                QTableWidgetItem(row.author or ""),
            )
            self.library_table.setItem(
                idx,
                3,
                QTableWidgetItem(row.declared_type or ""),
            )
            self.library_table.setItem(idx, 4, QTableWidgetItem(status_text))

        self._log(f"Loaded {len(rows)} library items")

    def _select_all_library_rows(self) -> None:
        for row in range(self.library_table.rowCount()):
            item = self.library_table.item(row, 0)
            if item is None:
                continue
            item.setCheckState(Qt.CheckState.Checked)

    def _clear_library_selection(self) -> None:
        for row in range(self.library_table.rowCount()):
            item = self.library_table.item(row, 0)
            if item is None:
                continue
            item.setCheckState(Qt.CheckState.Unchecked)

    def _selected_sync_items(self) -> list[SyncItem]:
        selected: list[SyncItem] = []
        for row in range(self.library_table.rowCount()):
            check_item = self.library_table.item(row, 0)
            if check_item is None:
                continue
            if check_item.checkState() != Qt.CheckState.Checked:
                continue
            payload = check_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(payload, dict):
                continue
            selected.append(
                SyncItem(
                    id=payload["id"],
                    title=payload["title"],
                    download_url=payload["download_url"],
                    declared_type=payload["declared_type"],
                )
            )
        return selected

    def _sync_selected(self) -> None:
        if self.is_busy:
            return

        if self.connected_device is None:
            QMessageBox.warning(
                self,
                "No Kindle",
                "Connect or configure a Kindle mount first.",
            )
            return

        items = self._selected_sync_items()
        if not items:
            QMessageBox.information(
                self,
                "No selection",
                "Select one or more books in Library.",
            )
            return

        settings = self._current_settings()
        workspace = Path(self.workspace_input.text().strip() or ".hearth")
        root = self.connected_device.root
        force_resync = self.force_checkbox.isChecked()

        self._set_busy(f"Syncing {len(items)} items...")
        future = self.worker_pool.submit(
            self._run_sync_worker,
            settings,
            workspace,
            str(root),
            items,
            force_resync,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_sync_finished,
                action_name="sync",
            )
        )

    def _run_sync_worker(
        self,
        settings: Settings,
        workspace: Path,
        kindle_root: str,
        items: list[SyncItem],
        force_resync: bool,
    ) -> tuple[int, int]:
        session = OPDSSession(settings)
        converters = ConverterManager.from_commands(
            settings.kcc_command,
            settings.calibre_command,
        )
        device = KindleDevice.probe(
            preferred=settings.kindle_transport,
            root_hint=kindle_root,
        )
        manager = SyncManager(
            session=session,
            converters=converters,
            device=device,
            workspace=workspace,
        )
        outcome = manager.sync(items=items, force_resync=force_resync)
        return (outcome.synced, outcome.skipped)

    def _on_sync_finished(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 2:
            raise TypeError("Unexpected sync result")

        synced, skipped = result
        self._log(f"Sync complete: synced={synced}, skipped={skipped}")
        self._refresh_kindle_files()

    def _refresh_kindle_files(self) -> None:
        if self.is_busy:
            return

        if self.connected_device is None:
            self.kindle_files_table.setRowCount(0)
            return

        self._set_busy("Loading Kindle files...")
        future = self.worker_pool.submit(
            self._list_kindle_files_worker,
            self.connected_device.transport,
            str(self.connected_device.root),
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._populate_kindle_files,
                action_name="refresh files",
            )
        )

    def _list_kindle_files_worker(
        self,
        transport: str,
        root: str,
    ) -> list[tuple[str, int]]:
        device = KindleDevice(transport=transport, root=Path(root))
        rows: list[tuple[str, int]] = []
        for path in device.list_files():
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append((path.name, size))
        rows.sort(key=lambda item: item[0].lower())
        return rows

    def _populate_kindle_files(self, result: object) -> None:
        if not isinstance(result, list):
            raise TypeError("Unexpected kindle files result")

        rows = cast(list[tuple[str, int]], result)
        self.kindle_files_table.setRowCount(len(rows))
        for idx, (name, size) in enumerate(rows):
            self.kindle_files_table.setItem(idx, 0, QTableWidgetItem(name))
            self.kindle_files_table.setItem(
                idx,
                1,
                QTableWidgetItem(str(size)),
            )

        self._log(f"Loaded {len(rows)} files from Kindle")

    def _selected_kindle_file_names(self) -> list[str]:
        selection_model = self.kindle_files_table.selectionModel()
        if selection_model is None:
            return []

        selected_rows = selection_model.selectedRows()
        names: list[str] = []
        for index in selected_rows:
            item = self.kindle_files_table.item(index.row(), 0)
            if item is None:
                continue
            names.append(item.text())
        return names

    def _download_selected_kindle_files(self) -> None:
        if self.is_busy:
            return
        if self.connected_device is None:
            QMessageBox.warning(self, "No Kindle", "No connected Kindle.")
            return

        names = self._selected_kindle_file_names()
        if not names:
            QMessageBox.information(
                self,
                "No file selected",
                "Select one or more files in Kindle Files.",
            )
            return

        target_dir = Path(self.download_dir_input.text().strip())
        self._set_busy(f"Downloading {len(names)} files...")
        future = self.worker_pool.submit(
            self._download_files_worker,
            self.connected_device.transport,
            str(self.connected_device.root),
            names,
            str(target_dir),
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_files_downloaded,
                action_name="download files",
            )
        )

    def _download_files_worker(
        self,
        transport: str,
        root: str,
        names: list[str],
        target_dir: str,
    ) -> int:
        device = KindleDevice(transport=transport, root=Path(root))
        destination = Path(target_dir)
        destination.mkdir(parents=True, exist_ok=True)

        copied = 0
        for name in names:
            source = device.documents_dir / name
            if not source.exists():
                continue
            shutil.copy2(source, destination / name)
            copied += 1
        return copied

    def _on_files_downloaded(self, result: object) -> None:
        if not isinstance(result, int):
            raise TypeError("Unexpected download result")
        copied = result
        self._log(f"Downloaded {copied} file(s)")

    def _delete_selected_kindle_files(self) -> None:
        if self.is_busy:
            return
        if self.connected_device is None:
            QMessageBox.warning(self, "No Kindle", "No connected Kindle.")
            return

        names = self._selected_kindle_file_names()
        if not names:
            QMessageBox.information(
                self,
                "No file selected",
                "Select one or more files in Kindle Files.",
            )
            return

        answer = QMessageBox.question(
            self,
            "Delete files",
            f"Delete {len(names)} selected file(s) from Kindle?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self._set_busy(f"Deleting {len(names)} files...")
        future = self.worker_pool.submit(
            self._delete_files_worker,
            self.connected_device.transport,
            str(self.connected_device.root),
            names,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_files_deleted,
                action_name="delete files",
            )
        )

    def _delete_files_worker(
        self,
        transport: str,
        root: str,
        names: list[str],
    ) -> int:
        device = KindleDevice(transport=transport, root=Path(root))
        deleted = 0
        for name in names:
            if device.delete_file(name):
                deleted += 1
        return deleted

    def _on_files_deleted(self, result: object) -> None:
        if not isinstance(result, int):
            raise TypeError("Unexpected delete result")
        deleted = result
        self._log(f"Deleted {deleted} file(s)")
        self._refresh_kindle_files()

    def _poll_pending_tasks(self) -> None:
        remaining: list[PendingTask] = []
        for task in self.pending_tasks:
            if not task.future.done():
                remaining.append(task)
                continue

            try:
                output = task.future.result()
                task.on_success(output)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._log(f"{task.action_name} failed: {exc}")
                QMessageBox.critical(
                    self,
                    "Operation failed",
                    f"{task.action_name} failed:\n{exc}",
                )

        self.pending_tasks = remaining
        if self.is_busy and not self.pending_tasks:
            self._set_idle()

    def _set_busy(self, text: str) -> None:
        self.is_busy = True
        self.status_label.setText(text)
        self.load_library_button.setEnabled(False)
        self.sync_selected_button.setEnabled(False)
        self.refresh_files_button.setEnabled(False)
        self.download_selected_file_button.setEnabled(False)
        self.delete_selected_file_button.setEnabled(False)
        self.probe_kindle_button.setEnabled(False)

    def _set_idle(self) -> None:
        self.is_busy = False
        self.status_label.setText("Idle")
        self.load_library_button.setEnabled(True)
        self.sync_selected_button.setEnabled(True)
        self.refresh_files_button.setEnabled(True)
        self.download_selected_file_button.setEnabled(True)
        self.delete_selected_file_button.setEnabled(True)
        self.probe_kindle_button.setEnabled(True)

    def _log(self, message: str) -> None:
        self.log_output.append(message)

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI lifecycle
        self.worker_pool.shutdown()
        event.accept()


def main() -> int:
    app = QApplication([])
    window = HearthMainWindow()
    window.show()
    return app.exec()
