from __future__ import annotations

# pylint: disable=import-error

from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, cast
import urllib.parse

from PyQt6.QtCore import QTimer, Qt  # type: ignore[import-not-found]
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)  # type: ignore[import-not-found]

from hearth.converters.manager import ConverterManager
from hearth.core.opds import OPDSClient, OPDSSession
from hearth.core.settings import Settings
from hearth.sync.device import DeviceFile, KindleDevice
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
class CollectionRow:
    feed_url: str
    title: str


@dataclass(slots=True)
class FeedLoadResult:
    feed_url: str
    is_root: bool
    children: list[CollectionRow]
    books: list[LibraryRow]


@dataclass(slots=True)
class PendingTask:
    future: Future[Any]
    on_success: Callable[[object], None]
    action_name: str
    show_errors: bool = True


@dataclass(slots=True)
class DeviceSnapshot:
    transport: str
    root: Path


@dataclass(slots=True)
class KindleFilesLoadResult:
    rows: list[DeviceFile]
    diagnostics: list[str]


class HearthMainWindow(QMainWindow):
    PLACEHOLDER_TEXT = "(expand to load)"

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Hearth")
        self.resize(1240, 820)

        self.settings_path = Path.home() / ".hearth" / "settings.json"
        self.worker_pool = WorkerPool(max_workers=3)
        self.pending_tasks: list[PendingTask] = []

        self.connected_device: DeviceSnapshot | None = None
        self.is_busy = False
        self.startup_catalog_attempted = False

        self.books_by_feed: dict[str, list[LibraryRow]] = {}
        self.loaded_feeds: set[str] = set()
        self.loading_feeds: set[str] = set()
        self.tree_item_by_feed: dict[str, QTreeWidgetItem] = {}

        self.workspace_input = QLineEdit(str(Path.home() / ".hearth"))
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
        self.desired_output_combo.addItems(["auto", "mobi"])
        self.kcc_command_input = QLineEdit("")
        self.calibre_command_input = QLineEdit("")

        self.probe_kindle_button = QPushButton("Probe Kindle")

        self.load_catalog_button = QPushButton("Reload Root")
        self.sync_selected_button = QPushButton("Sync Selected")
        self.select_all_library_button = QPushButton("Select All")
        self.clear_library_selection_button = QPushButton("Clear")
        self.force_checkbox = QCheckBox("Force re-sync")

        self.refresh_files_button = QPushButton("Refresh Files")
        self.download_selected_file_button = QPushButton("Download Selected")
        self.delete_selected_file_button = QPushButton("Delete Selected")

        self.status_label = QLabel("Idle")
        self.kindle_status_label = QLabel("Kindle: probing...")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(False)

        self.collections_tree = QTreeWidget()
        self.collections_tree.setColumnCount(1)
        self.collections_tree.setHeaderLabels(["Collections"])

        self.library_table = QTableWidget(0, 5)
        self.library_table.setHorizontalHeaderLabels(
            ["Sync", "Title", "Author", "Type", "Status"]
        )

        self.kindle_files_tree = QTreeWidget()
        self.kindle_files_tree.setColumnCount(3)
        self.kindle_files_tree.setHeaderLabels(["Name", "Type", "Size"])

        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)

        self.reset_general_button = QPushButton("Reset General")
        self.reset_opds_button = QPushButton("Reset OPDS")
        self.reset_kindle_button = QPushButton("Reset Kindle")
        self.reset_conversion_button = QPushButton("Reset Conversion")
        self.reset_all_button = QPushButton("Reset All")

        self.tabs = QTabWidget()
        self.library_tab = QWidget()
        self.kindle_files_tab = QWidget()
        self.settings_tab = QWidget()

        self._configure_layout()
        self._connect_events()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(120)
        self.poll_timer.timeout.connect(self._poll_pending_tasks)
        self.poll_timer.start()

        self._load_settings_from_file()
        self._probe_kindle()

    def _configure_layout(self) -> None:
        central = QWidget()
        root = QVBoxLayout()

        header = QHBoxLayout()
        header.addWidget(self.probe_kindle_button)
        header.addSpacing(12)
        header.addWidget(self.kindle_status_label)
        header.addStretch(1)
        header.addWidget(self.progress_bar)
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
        controls.addWidget(self.load_catalog_button)
        controls.addWidget(self.sync_selected_button)
        controls.addWidget(self.select_all_library_button)
        controls.addWidget(self.clear_library_selection_button)
        controls.addWidget(self.force_checkbox)
        controls.addStretch(1)

        tree_header = self.collections_tree.header()
        if tree_header is not None:
            tree_header.setStretchLastSection(True)

        library_header = self.library_table.horizontalHeader()
        if library_header is not None:
            library_header.setStretchLastSection(True)
        library_vertical = self.library_table.verticalHeader()
        if library_vertical is not None:
            library_vertical.setVisible(False)

        layout.addLayout(controls)

        split = QHBoxLayout()
        split.addWidget(self.collections_tree, stretch=2)
        split.addWidget(self.library_table, stretch=4)
        layout.addLayout(split)

        self.library_tab.setLayout(layout)

    def _configure_kindle_files_tab(self) -> None:
        layout = QVBoxLayout()

        controls = QGridLayout()
        controls.addWidget(QLabel("Download folder"), 0, 0)
        controls.addWidget(self.download_dir_input, 0, 1, 1, 3)
        controls.addWidget(self.refresh_files_button, 1, 0)
        controls.addWidget(self.download_selected_file_button, 1, 1)
        controls.addWidget(self.delete_selected_file_button, 1, 2)

        files_header = self.kindle_files_tree.header()
        if files_header is not None:
            files_header.setStretchLastSection(True)
        self.kindle_files_tree.setColumnWidth(0, 560)
        self.kindle_files_tree.setColumnWidth(1, 90)
        self.kindle_files_tree.setColumnWidth(2, 140)
        self.kindle_files_tree.setSelectionMode(
            QTreeWidget.SelectionMode.ExtendedSelection
        )

        layout.addLayout(controls)
        layout.addWidget(self.kindle_files_tree)

        self.kindle_files_tab.setLayout(layout)

    def _configure_settings_tab(self) -> None:
        tab_layout = QVBoxLayout()

        general_group = QGroupBox("General")
        general_layout = QGridLayout()
        general_layout.addWidget(QLabel("Workspace"), 0, 0)
        general_layout.addWidget(self.workspace_input, 0, 1, 1, 3)
        general_layout.addWidget(QLabel("Download folder"), 1, 0)
        general_layout.addWidget(self.download_dir_input, 1, 1, 1, 2)
        general_layout.addWidget(self.reset_general_button, 1, 3)
        general_group.setLayout(general_layout)

        opds_group = QGroupBox("OPDS")
        opds_layout = QGridLayout()
        opds_layout.addWidget(QLabel("Feed URL"), 0, 0)
        opds_layout.addWidget(self.feed_input, 0, 1, 1, 3)
        opds_layout.addWidget(QLabel("Auth mode"), 1, 0)
        opds_layout.addWidget(self.auth_mode_combo, 1, 1)
        opds_layout.addWidget(QLabel("Username"), 1, 2)
        opds_layout.addWidget(self.auth_username_input, 1, 3)
        opds_layout.addWidget(QLabel("Password"), 2, 0)
        opds_layout.addWidget(self.auth_password_input, 2, 1)
        opds_layout.addWidget(QLabel("Bearer token"), 2, 2)
        opds_layout.addWidget(self.auth_bearer_input, 2, 3)
        opds_layout.addWidget(self.reset_opds_button, 3, 3)
        opds_group.setLayout(opds_layout)

        kindle_group = QGroupBox("Kindle")
        kindle_layout = QGridLayout()
        kindle_layout.addWidget(QLabel("Transport"), 0, 0)
        kindle_layout.addWidget(self.transport_combo, 0, 1)
        kindle_layout.addWidget(QLabel("Mount"), 0, 2)
        kindle_layout.addWidget(self.kindle_root_input, 0, 3)
        kindle_layout.addWidget(self.reset_kindle_button, 1, 3)
        kindle_group.setLayout(kindle_layout)

        conversion_group = QGroupBox("Conversion")
        conversion_layout = QGridLayout()
        conversion_layout.addWidget(QLabel("Preferred output"), 0, 0)
        conversion_layout.addWidget(self.desired_output_combo, 0, 1)
        conversion_layout.addWidget(self.reset_conversion_button, 0, 3)
        conversion_group.setLayout(conversion_layout)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(self.reset_all_button)

        tab_layout.addWidget(general_group)
        tab_layout.addWidget(opds_group)
        tab_layout.addWidget(kindle_group)
        tab_layout.addWidget(conversion_group)
        tab_layout.addLayout(footer)
        tab_layout.addStretch(1)

        self.settings_tab.setLayout(tab_layout)

    def _connect_events(self) -> None:
        self.probe_kindle_button.clicked.connect(self._probe_kindle)
        self.auth_mode_combo.currentTextChanged.connect(self._update_auth_visibility)

        self.load_catalog_button.clicked.connect(self._load_root_collections)
        self.sync_selected_button.clicked.connect(self._sync_selected)
        self.select_all_library_button.clicked.connect(self._select_all_library_rows)
        self.clear_library_selection_button.clicked.connect(
            self._clear_library_selection
        )
        self.collections_tree.currentItemChanged.connect(self._on_collection_changed)
        self.collections_tree.itemExpanded.connect(self._on_collection_expanded)

        self.refresh_files_button.clicked.connect(self._refresh_kindle_files)
        self.download_selected_file_button.clicked.connect(
            self._download_selected_kindle_files
        )
        self.delete_selected_file_button.clicked.connect(
            self._delete_selected_kindle_files
        )

        self.reset_general_button.clicked.connect(self._reset_general)
        self.reset_opds_button.clicked.connect(self._reset_opds)
        self.reset_kindle_button.clicked.connect(self._reset_kindle)
        self.reset_conversion_button.clicked.connect(self._reset_conversion)
        self.reset_all_button.clicked.connect(self._reset_all)

        self._connect_autosave()

    def _connect_autosave(self) -> None:
        for combo in [
            self.auth_mode_combo,
            self.transport_combo,
            self.desired_output_combo,
        ]:
            combo.currentTextChanged.connect(self._save_settings_to_file)

        for edit in [
            self.workspace_input,
            self.download_dir_input,
            self.feed_input,
            self.auth_username_input,
            self.auth_password_input,
            self.auth_bearer_input,
            self.kindle_root_input,
        ]:
            edit.editingFinished.connect(self._save_settings_to_file)

    def _load_settings_from_file(self) -> None:
        settings = Settings.load(self.settings_path)

        self.feed_input.setText(settings.opds_url)
        self.auth_mode_combo.setCurrentText(settings.auth_mode)
        self.auth_username_input.setText(settings.auth_username)
        self.auth_password_input.setText(settings.auth_password)
        self.auth_bearer_input.setText(settings.auth_bearer_token)
        self.transport_combo.setCurrentText(settings.kindle_transport)
        self.kindle_root_input.setText(settings.kindle_mount)

        output = settings.desired_output
        if output not in {"auto", "mobi"}:
            output = "auto"
        self.desired_output_combo.setCurrentText(output)

        self.kcc_command_input.setText(settings.kcc_command)
        self.calibre_command_input.setText(settings.calibre_command)

        self._update_auth_visibility()
        self._log(f"Loaded settings from {self.settings_path}")

    def _save_settings_to_file(self) -> None:
        settings = self._current_settings()
        settings.save(self.settings_path)

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

    def _reset_general(self) -> None:
        self.workspace_input.setText(str(Path.home() / ".hearth"))
        self.download_dir_input.setText(str(Path.home() / "Downloads"))
        self._save_settings_to_file()

    def _reset_opds(self) -> None:
        self.feed_input.setText("")
        self.auth_mode_combo.setCurrentText("none")
        self.auth_username_input.setText("")
        self.auth_password_input.setText("")
        self.auth_bearer_input.setText("")
        self._update_auth_visibility()
        self._save_settings_to_file()

    def _reset_kindle(self) -> None:
        self.transport_combo.setCurrentText("auto")
        self.kindle_root_input.setText("")
        self._save_settings_to_file()

    def _reset_conversion(self) -> None:
        self.desired_output_combo.setCurrentText("auto")
        self.kcc_command_input.setText("")
        self.calibre_command_input.setText("")
        self._save_settings_to_file()

    def _reset_all(self) -> None:
        self._reset_general()
        self._reset_opds()
        self._reset_kindle()
        self._reset_conversion()
        self._log("Reset all settings groups")

    def _update_auth_visibility(self) -> None:
        mode = self.auth_mode_combo.currentText()
        is_basic = mode == "basic"
        is_bearer = mode == "bearer"

        self.auth_username_input.setEnabled(is_basic)
        self.auth_password_input.setEnabled(is_basic)
        self.auth_bearer_input.setEnabled(is_bearer)
        self._save_settings_to_file()

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
            if self.transport_combo.currentText() == "mtp":
                self.kindle_status_label.setText("Kindle: MTP backend unavailable")
                self._log("MTP selected, but go-mtpx bridge is unavailable")
            else:
                self.kindle_status_label.setText("Kindle: not connected")
            self.kindle_files_tree.clear()
            self._log("Kindle not detected")
        elif isinstance(result, DeviceSnapshot):
            self.connected_device = result
            self.kindle_status_label.setText(
                f"Kindle: {result.transport} at {result.root}"
            )
            self._log(f"Detected Kindle at {result.root}")
            self._log_kindle_probe_details(result)
            self._refresh_kindle_files(force=True)
        else:
            raise TypeError("Unexpected probe result")

        self._startup_load_root_collections()

    def _startup_load_root_collections(self) -> None:
        if self.startup_catalog_attempted:
            return

        feed_url = self.feed_input.text().strip()
        if not feed_url:
            self.startup_catalog_attempted = True
            return

        self.startup_catalog_attempted = True
        self._load_root_collections(silent=True)

    def _load_root_collections(self, silent: bool = False) -> None:
        feed_url = self.feed_input.text().strip()
        if not feed_url:
            QMessageBox.warning(
                self,
                "Missing OPDS URL",
                "Set an OPDS URL in Settings first.",
            )
            return

        self.books_by_feed = {}
        self.loaded_feeds = set()
        self.loading_feeds = set()
        self.tree_item_by_feed = {}
        self.collections_tree.clear()
        self.library_table.setRowCount(0)

        self._request_feed_load(
            feed_url=feed_url,
            is_root=True,
            show_errors=not silent,
        )

    def _request_feed_load(
        self,
        feed_url: str,
        is_root: bool,
        show_errors: bool,
    ) -> None:
        if feed_url in self.loading_feeds:
            return

        if feed_url in self.loaded_feeds and not is_root:
            current = self.collections_tree.currentItem()
            if current is not None:
                current_feed = current.data(0, Qt.ItemDataRole.UserRole)
                if current_feed == feed_url:
                    self._populate_library_table(self.books_by_feed.get(feed_url, []))
            return

        settings = self._current_settings()
        if not self.is_busy:
            status = "Loading root collections..." if is_root else "Loading shelf..."
            self._set_busy(status)

        self.loading_feeds.add(feed_url)
        future = self.worker_pool.submit(
            self._fetch_feed_worker,
            settings,
            feed_url,
            is_root,
        )
        self.pending_tasks.append(
            PendingTask(
                future=future,
                on_success=self._on_feed_loaded,
                action_name=f"load feed {feed_url}",
                show_errors=show_errors,
            )
        )

    def _fetch_feed_worker(
        self,
        settings: Settings,
        feed_url: str,
        is_root: bool,
    ) -> FeedLoadResult:
        session = OPDSSession(settings)
        client = OPDSClient(session)

        entries = client.fetch_entries(feed_url)
        children: list[CollectionRow] = []
        books: list[LibraryRow] = []
        seen_children: set[str] = set()

        for entry in entries:
            for link in entry.links:
                if link.is_acquisition() and link.href:
                    books.append(
                        LibraryRow(
                            id=entry.id,
                            title=entry.title,
                            author=entry.author,
                            download_url=urllib.parse.urljoin(
                                feed_url,
                                link.href,
                            ),
                            declared_type=link.type,
                        )
                    )
                    continue

                if not link.is_navigation() or not link.href:
                    continue

                child_feed_url = urllib.parse.urljoin(feed_url, link.href)
                if child_feed_url in seen_children:
                    continue
                seen_children.add(child_feed_url)
                children.append(
                    CollectionRow(
                        feed_url=child_feed_url,
                        title=link.title or entry.title or child_feed_url,
                    )
                )

        return FeedLoadResult(
            feed_url=feed_url,
            is_root=is_root,
            children=children,
            books=books,
        )

    def _on_feed_loaded(self, result: object) -> None:
        if not isinstance(result, FeedLoadResult):
            raise TypeError("Unexpected feed load result")

        self.loading_feeds.discard(result.feed_url)
        self.loaded_feeds.add(result.feed_url)
        self.books_by_feed[result.feed_url] = result.books

        if result.is_root:
            self._populate_root_collections(result.children)
            self._log(f"Loaded {len(result.children)} top-level collections")
            return

        parent_item = self.tree_item_by_feed.get(result.feed_url)
        if parent_item is not None:
            self._replace_placeholder_with_children(
                parent_item,
                result.children,
            )

        current = self.collections_tree.currentItem()
        if current is not None:
            current_feed = current.data(0, Qt.ItemDataRole.UserRole)
            if current_feed == result.feed_url:
                self._populate_library_table(result.books)

        self._log(f"Loaded {len(result.children)} sub-collections")

    def _populate_root_collections(self, rows: list[CollectionRow]) -> None:
        self.collections_tree.clear()
        self.tree_item_by_feed = {}

        for row in rows:
            item = QTreeWidgetItem([row.title])
            item.setData(0, Qt.ItemDataRole.UserRole, row.feed_url)
            self._attach_placeholder(item)
            self.collections_tree.addTopLevelItem(item)
            self.tree_item_by_feed[row.feed_url] = item

        self.collections_tree.collapseAll()
        if self.collections_tree.topLevelItemCount() > 0:
            self.collections_tree.setCurrentItem(self.collections_tree.topLevelItem(0))

    def _attach_placeholder(self, item: QTreeWidgetItem) -> None:
        if item.childCount() > 0:
            return
        child = QTreeWidgetItem([self.PLACEHOLDER_TEXT])
        child.setData(0, Qt.ItemDataRole.UserRole, None)
        item.addChild(child)

    def _replace_placeholder_with_children(
        self,
        parent_item: QTreeWidgetItem,
        rows: list[CollectionRow],
    ) -> None:
        parent_item.takeChildren()
        for row in rows:
            child_item = QTreeWidgetItem([row.title])
            child_item.setData(0, Qt.ItemDataRole.UserRole, row.feed_url)
            self._attach_placeholder(child_item)
            parent_item.addChild(child_item)
            self.tree_item_by_feed[row.feed_url] = child_item

    def _on_collection_expanded(self, item: QTreeWidgetItem) -> None:
        feed_url = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            return
        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
        )

    def _on_collection_changed(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        _ = previous
        if current is None:
            self.library_table.setRowCount(0)
            return

        feed_url = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(feed_url, str):
            self.library_table.setRowCount(0)
            return

        if feed_url in self.loaded_feeds:
            self._populate_library_table(self.books_by_feed.get(feed_url, []))
            return

        self._request_feed_load(
            feed_url=feed_url,
            is_root=False,
            show_errors=True,
        )

    def _populate_library_table(self, rows: list[LibraryRow]) -> None:
        records = {}
        device_files: set[str] = set()
        if self.connected_device is not None:
            device = KindleDevice(
                transport=self.connected_device.transport,
                root=self.connected_device.root,
            )
            if device.transport == "mtp":
                metadata_path = (
                    Path(self.workspace_input.text().strip() or ".hearth")
                    / ".hearth_metadata.mtp.json"
                )
            else:
                metadata_path = device.documents_dir / ".hearth_metadata.json"
            records = load_metadata(metadata_path)
            device_files = set()
            for entry in device.list_files():
                if entry.is_dir:
                    continue
                device_files.add(entry.name)
                device_files.add(entry.path)
                normalized = entry.path.strip("/")
                if normalized:
                    device_files.add(normalized)
                    if normalized.startswith("documents/"):
                        relative = normalized.removeprefix("documents/")
                        device_files.add(relative)

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

            status = "Not Synced"
            if row.id in records:
                record = records[row.id]
                if record.device_filename in device_files and record.on_device:
                    status = "On Device"
                elif record.desired:
                    status = "Wanted"

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
            self.library_table.setItem(idx, 4, QTableWidgetItem(status))

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
                "Connect a Kindle first.",
            )
            return

        items = self._selected_sync_items()
        if not items:
            QMessageBox.information(
                self,
                "No Selection",
                "Select one or more titles in Library.",
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
        self._refresh_kindle_files(force=True)

    def _refresh_kindle_files(self, force: bool = False) -> None:
        if self.is_busy and not force:
            return

        if self.connected_device is None:
            self.kindle_files_tree.clear()
            return

        if not self.is_busy:
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

    def _log_kindle_probe_details(self, snapshot: DeviceSnapshot) -> None:
        device = KindleDevice(
            transport=snapshot.transport,
            root=snapshot.root,
        )
        root = device.root
        docs = device.documents_dir

        self._log(f"[kindle-diag] root exists={root.exists()} dir={root.is_dir()}")
        self._log(
            f"[kindle-diag] documents path={docs} exists={docs.exists()} "
            f"dir={docs.is_dir()}"
        )

        for candidate in device.hearth_dir_candidates():
            self._log(
                f"[kindle-diag] candidate {candidate} " f"exists={candidate.exists()}"
            )

        if snapshot.transport == "mtp":
            mtp_diag = KindleDevice.mtp_backend().diagnostics()
            self._log(f"[kindle-diag] mtp available={mtp_diag['available']}")
            self._log(
                "[kindle-diag] mtp commands: "
                f"detect={mtp_diag['detect_cmd']} "
                f"files={mtp_diag['files_cmd']} "
                f"folders={mtp_diag['folders_cmd']} "
                f"get={mtp_diag['get_cmd']} "
                f"send={mtp_diag['send_cmd']} "
                f"delete={mtp_diag['delete_cmd']}"
            )
            self._log(f"[kindle-diag] mtp install hint={mtp_diag['install_hint']}")

        try:
            entries = sorted(path.name for path in root.iterdir())[:12]
        except OSError as exc:
            self._log(f"[kindle-diag] root list failed: {exc}")
        else:
            self._log(
                f"[kindle-diag] root entries({len(entries)} shown): "
                + ", ".join(entries)
            )

    def _list_kindle_files_worker(
        self,
        transport: str,
        root: str,
    ) -> KindleFilesLoadResult:
        device = KindleDevice(transport=transport, root=Path(root))
        diagnostics: list[str] = []
        diagnostics.append(
            f"transport={transport} root={device.root} "
            f"exists={device.root.exists()}"
        )
        diagnostics.append(
            f"documents={device.documents_dir} "
            f"exists={device.documents_dir.exists()}"
        )

        if transport == "mtp":
            mtp_diag = KindleDevice.mtp_backend().diagnostics()
            diagnostics.append(f"mtp available={mtp_diag['available']}")
            diagnostics.append(
                "mtp commands "
                f"detect={mtp_diag['detect_cmd']} "
                f"files={mtp_diag['files_cmd']} "
                f"folders={mtp_diag['folders_cmd']} "
                f"get={mtp_diag['get_cmd']} "
                f"send={mtp_diag['send_cmd']} "
                f"delete={mtp_diag['delete_cmd']}"
            )

        for candidate in device.hearth_dir_candidates():
            diagnostics.append(f"candidate={candidate} exists={candidate.exists()}")

        rows: list[DeviceFile] = []
        try:
            listed = device.list_files()
        except (OSError, RuntimeError) as exc:
            diagnostics.append(f"list_files failed: {exc}")
            return KindleFilesLoadResult(rows=rows, diagnostics=diagnostics)

        diagnostics.append(f"list_files count={len(listed)}")
        rows = sorted(listed, key=lambda item: item.path.lower())
        return KindleFilesLoadResult(rows=rows, diagnostics=diagnostics)

    def _populate_kindle_files(self, result: object) -> None:
        if not isinstance(result, KindleFilesLoadResult):
            raise TypeError("Unexpected kindle files result")

        for line in result.diagnostics:
            self._log(f"[kindle-diag] {line}")

        self._populate_kindle_files_tree(result.rows)

        file_count = len([row for row in result.rows if not row.is_dir])
        dir_count = len([row for row in result.rows if row.is_dir])
        self._log(f"Loaded {file_count} files and {dir_count} folders from Kindle")

    def _populate_kindle_files_tree(self, rows: list[DeviceFile]) -> None:
        self.kindle_files_tree.clear()
        node_by_path: dict[str, QTreeWidgetItem] = {}

        for entry in rows:
            parts = [segment for segment in entry.path.split("/") if segment]
            if not parts:
                continue

            parent_item: QTreeWidgetItem | None = None
            current_path = ""
            for idx, part in enumerate(parts):
                current_path = f"{current_path}/{part}" if current_path else part
                node = node_by_path.get(current_path)
                is_leaf = idx == len(parts) - 1
                if node is None:
                    node = QTreeWidgetItem([part, "Folder", ""])
                    node.setData(0, Qt.ItemDataRole.UserRole, current_path)
                    node.setData(1, Qt.ItemDataRole.UserRole, False)
                    if parent_item is None:
                        self.kindle_files_tree.addTopLevelItem(node)
                    else:
                        parent_item.addChild(node)
                    node_by_path[current_path] = node

                if is_leaf:
                    is_file = not entry.is_dir
                    node.setText(0, entry.name)
                    node.setText(1, "File" if is_file else "Folder")
                    node.setText(
                        2,
                        self._format_size(entry.size) if is_file else "",
                    )
                    node.setData(0, Qt.ItemDataRole.UserRole, entry.path)
                    node.setData(1, Qt.ItemDataRole.UserRole, is_file)

                parent_item = node

        self.kindle_files_tree.collapseAll()

    def _format_size(self, size_bytes: int) -> str:
        if size_bytes < 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size_bytes)
        unit_idx = 0
        while value >= 1024.0 and unit_idx < len(units) - 1:
            value /= 1024.0
            unit_idx += 1
        if unit_idx == 0:
            return f"{int(value)} {units[unit_idx]}"
        return f"{value:.1f} {units[unit_idx]}"

    def _selected_kindle_file_names(self) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for item in self.kindle_files_tree.selectedItems():
            is_file = item.data(1, Qt.ItemDataRole.UserRole)
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if is_file is not True or not isinstance(path, str):
                continue
            if path in seen:
                continue
            seen.add(path)
            names.append(path)
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
            filename = self._download_filename(name)
            destination_path = self._dedupe_download_path(destination / filename)
            try:
                device.download_file(name, destination_path)
            except (OSError, RuntimeError):
                continue
            else:
                copied += 1
        return copied

    def _download_filename(self, source_path: str) -> str:
        normalized = source_path.strip().rstrip("/")
        candidate = Path(normalized).name
        if not candidate:
            return "download.bin"
        return candidate

    def _dedupe_download_path(self, destination: Path) -> Path:
        if not destination.exists():
            return destination

        stem = destination.stem
        suffix = destination.suffix
        parent = destination.parent
        index = 1
        while True:
            candidate = parent / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
            index += 1

    def _on_files_downloaded(self, result: object) -> None:
        if not isinstance(result, int):
            raise TypeError("Unexpected download result")
        self._log(f"Downloaded {result} file(s)")

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
        self._log(f"Deleted {result} file(s)")
        self._refresh_kindle_files(force=True)

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
                feed = task.action_name.removeprefix("load feed ")
                self.loading_feeds.discard(feed)
                self._log(f"{task.action_name} failed: {exc}")
                if task.show_errors:
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
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.load_catalog_button.setEnabled(False)
        self.sync_selected_button.setEnabled(False)
        self.refresh_files_button.setEnabled(False)
        self.download_selected_file_button.setEnabled(False)
        self.delete_selected_file_button.setEnabled(False)
        self.probe_kindle_button.setEnabled(False)

    def _set_idle(self) -> None:
        self.is_busy = False
        self.status_label.setText("Idle")
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 1)
        self.load_catalog_button.setEnabled(True)
        self.sync_selected_button.setEnabled(True)
        self.refresh_files_button.setEnabled(True)
        self.download_selected_file_button.setEnabled(True)
        self.delete_selected_file_button.setEnabled(True)
        self.probe_kindle_button.setEnabled(True)

    def _log(self, message: str) -> None:
        self.log_output.append(message)

    def closeEvent(self, event) -> None:  # pragma: no cover
        self.worker_pool.shutdown()
        event.accept()


def main() -> int:
    app = QApplication([])
    window = HearthMainWindow()
    window.show()
    return app.exec()
