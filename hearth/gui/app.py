from __future__ import annotations

# pylint: disable=import-error

from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, cast

from PyQt6.QtCore import QTimer, Qt  # type: ignore[import-not-found]
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
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)  # type: ignore[import-not-found]

from hearth.converters.manager import ConverterManager
from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import KindleDevice
from hearth.sync.manager import SyncItem, SyncManager

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


class HearthMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hearth")
        self.resize(1100, 760)

        self.worker_pool = WorkerPool(max_workers=2)
        self.pending_tasks: list[PendingTask] = []

        self.settings_path_input = QLineEdit(".hearth/settings.json")
        self.workspace_input = QLineEdit(".hearth")
        self.feed_input = QLineEdit("")
        self.kindle_root_input = QLineEdit("")

        self.transport_combo = QComboBox()
        self.transport_combo.addItems(["auto", "usb", "mtp"])

        self.force_checkbox = QCheckBox("Force re-sync")

        self.load_settings_button = QPushButton("Load Settings")
        self.save_settings_button = QPushButton("Save Settings")
        self.load_library_button = QPushButton("Load Library")
        self.sync_selected_button = QPushButton("Sync Selected")

        self.status_label = QLabel("Idle")
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        self.library_table = QTableWidget(0, 4)
        self.library_table.setHorizontalHeaderLabels(
            ["Sync", "Title", "Author", "Type"]
        )
        horizontal_header = self.library_table.horizontalHeader()
        if horizontal_header is not None:
            horizontal_header.setStretchLastSection(True)
        vertical_header = self.library_table.verticalHeader()
        if vertical_header is not None:
            vertical_header.setVisible(False)

        self._configure_layout()
        self._connect_events()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(100)
        self.poll_timer.timeout.connect(self._poll_pending_tasks)
        self.poll_timer.start()

        self._load_settings_from_file()

    def _configure_layout(self) -> None:
        central = QWidget()
        outer = QVBoxLayout()

        form_layout = QGridLayout()
        form_layout.addWidget(QLabel("Settings file"), 0, 0)
        form_layout.addWidget(self.settings_path_input, 0, 1)
        form_layout.addWidget(self.load_settings_button, 0, 2)
        form_layout.addWidget(self.save_settings_button, 0, 3)

        form_layout.addWidget(QLabel("OPDS feed URL"), 1, 0)
        form_layout.addWidget(self.feed_input, 1, 1, 1, 3)

        form_layout.addWidget(QLabel("Workspace"), 2, 0)
        form_layout.addWidget(self.workspace_input, 2, 1)
        form_layout.addWidget(QLabel("Kindle root"), 2, 2)
        form_layout.addWidget(self.kindle_root_input, 2, 3)

        form_layout.addWidget(QLabel("Transport"), 3, 0)
        form_layout.addWidget(self.transport_combo, 3, 1)
        form_layout.addWidget(self.force_checkbox, 3, 2, 1, 2)

        buttons = QHBoxLayout()
        buttons.addWidget(self.load_library_button)
        buttons.addWidget(self.sync_selected_button)
        buttons.addStretch(1)
        buttons.addWidget(self.status_label)

        outer.addLayout(form_layout)
        outer.addLayout(buttons)
        outer.addWidget(self.library_table, stretch=3)
        outer.addWidget(QLabel("Log"))
        outer.addWidget(self.log_output, stretch=2)

        central.setLayout(outer)
        self.setCentralWidget(central)

    def _connect_events(self) -> None:
        self.load_settings_button.clicked.connect(self._load_settings_from_file)
        self.save_settings_button.clicked.connect(self._save_settings_to_file)
        self.load_library_button.clicked.connect(self._load_library)
        self.sync_selected_button.clicked.connect(self._sync_selected)

    def _load_settings_from_file(self) -> None:
        settings_path = Path(self.settings_path_input.text().strip())
        settings = Settings.load(settings_path)
        self.feed_input.setText(settings.opds_url)
        self.kindle_root_input.setText(settings.kindle_mount)
        self.workspace_input.setText(".hearth")
        self.transport_combo.setCurrentText(settings.kindle_transport)
        self._log(f"Loaded settings from {settings_path}")

    def _save_settings_to_file(self) -> None:
        settings_path = Path(self.settings_path_input.text().strip())
        settings = self._current_settings()
        settings.save(settings_path)
        self._log(f"Saved settings to {settings_path}")

    def _current_settings(self) -> Settings:
        transport = cast(
            Literal["auto", "usb", "mtp"],
            self.transport_combo.currentText(),
        )
        return Settings(
            opds_url=self.feed_input.text().strip(),
            kindle_transport=transport,
            kindle_mount=self.kindle_root_input.text().strip(),
        )

    def _load_library(self) -> None:
        feed_url = self.feed_input.text().strip()
        if not feed_url:
            QMessageBox.warning(
                self,
                "Missing feed URL",
                "Set an OPDS URL first.",
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

    def _sync_selected(self) -> None:
        selected = self._selected_sync_items()
        if not selected:
            QMessageBox.information(
                self,
                "Nothing selected",
                "Select one or more rows to sync.",
            )
            return

        settings = self._current_settings()
        workspace = Path(self.workspace_input.text().strip() or ".hearth")
        kindle_root = self.kindle_root_input.text().strip()
        force_resync = self.force_checkbox.isChecked()

        self._set_busy(f"Syncing {len(selected)} items...")
        future = self.worker_pool.submit(
            self._run_sync,
            settings,
            workspace,
            kindle_root,
            selected,
            force_resync,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_sync_finished,
                action_name="sync",
            )
        )

    def _selected_sync_items(self) -> list[SyncItem]:
        items: list[SyncItem] = []
        for row in range(self.library_table.rowCount()):
            check_item = self.library_table.item(row, 0)
            if check_item is None:
                continue
            if check_item.checkState() != Qt.CheckState.Checked:
                continue

            row_data = check_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(row_data, dict):
                continue

            items.append(
                SyncItem(
                    id=row_data["id"],
                    title=row_data["title"],
                    download_url=row_data["download_url"],
                    declared_type=row_data["declared_type"],
                )
            )
        return items

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

    def _run_sync(
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
            root_hint=kindle_root or settings.kindle_mount,
        )
        manager = SyncManager(
            session=session,
            converters=converters,
            device=device,
            workspace=workspace,
        )
        outcome = manager.sync(items=items, force_resync=force_resync)
        return (outcome.synced, outcome.skipped)

    def _populate_library_table(self, result: object) -> None:
        if not isinstance(result, list):
            raise TypeError("Unexpected result type for library loading")

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
            self.library_table.setItem(idx, 0, check_item)
            self.library_table.setItem(
                idx,
                1,
                QTableWidgetItem(row.title),
            )
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

        self._set_idle()
        self._log(f"Loaded {len(rows)} acquisition items")

    def _on_sync_finished(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 2:
            raise TypeError("Unexpected result type for sync")

        synced, skipped = result
        self._set_idle()
        self._log(f"Sync complete: synced={synced}, skipped={skipped}")

    def _poll_pending_tasks(self) -> None:
        remaining: list[PendingTask] = []
        for task in self.pending_tasks:
            if not task.future.done():
                remaining.append(task)
                continue

            try:
                result = task.future.result()
                task.on_success(result)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._set_idle()
                self._log(f"{task.action_name} failed: {exc}")
                QMessageBox.critical(
                    self,
                    "Operation failed",
                    f"{task.action_name} failed:\n{exc}",
                )

        self.pending_tasks = remaining

    def _set_busy(self, text: str) -> None:
        self.status_label.setText(text)
        self.load_library_button.setEnabled(False)
        self.sync_selected_button.setEnabled(False)

    def _set_idle(self) -> None:
        self.status_label.setText("Idle")
        self.load_library_button.setEnabled(True)
        self.sync_selected_button.setEnabled(True)

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
