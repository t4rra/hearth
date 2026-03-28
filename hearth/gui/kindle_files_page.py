"""Kindle files browser page for Hearth GUI."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
)

from ..core.config import SettingsManager
from ..sync.kindle_device import KindleDevice


class KindleFilesPage(QWidget):
    """Read-only browser for files on the connected Kindle."""

    def __init__(self, kindle_device: Optional[KindleDevice] = None):
        super().__init__()
        self.settings_manager = SettingsManager()
        self._kindle_device: Optional[KindleDevice] = kindle_device
        self._initial_refresh_done = False
        self.tree: QTreeWidget
        self.path_label: QLabel
        self.status_label: QLabel
        self.init_ui()
        self.status_label.setText("Open this tab or click Refresh to load files")

    def showEvent(self, event) -> None:
        """Load file tree lazily when this tab is first shown."""
        super().showEvent(event)
        if self._initial_refresh_done:
            return
        self._initial_refresh_done = True
        self.refresh_files()

    def init_ui(self) -> None:
        """Initialize UI widgets."""
        layout = QVBoxLayout()
        self.setLayout(layout)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Mount:"))
        self.path_label = QLabel("Not connected")
        top_bar.addWidget(self.path_label)
        top_bar.addStretch()

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_files)
        top_bar.addWidget(refresh_btn)
        layout.addLayout(top_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Type", "Size", "Modified"])
        self.tree.setColumnWidth(0, 420)
        self.tree.setColumnWidth(1, 120)
        self.tree.setColumnWidth(2, 120)
        self.tree.setColumnWidth(3, 180)
        layout.addWidget(self.tree)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def _build_kindle_device(self) -> KindleDevice:
        """Create a KindleDevice using saved settings."""
        if self._kindle_device is not None:
            return self._kindle_device

        settings = self.settings_manager.get_settings()
        if settings.kindle_mount_path:
            mount_path = Path(settings.kindle_mount_path)
        else:
            mount_path = None
        self._kindle_device = KindleDevice(
            mount_path=mount_path,
            auto_mount_mtp=settings.mtp_auto_mount,
            preferred_mtp_tool=settings.mtp_mount_tool,
            auto_install_mtp_backend=settings.mtp_auto_install_backend,
        )
        return self._kindle_device

    def refresh_files(self) -> None:
        """Refresh file tree from the currently connected Kindle."""
        self.tree.clear()

        device = self._build_kindle_device()
        if not device.is_connected():
            self.path_label.setText("Not connected")
            self.status_label.setText("Kindle not connected")
            return

        entries = device.list_file_tree()
        transport = device.get_transport()
        if transport == "usb":
            mount = device.get_mount_path()
            self.path_label.setText(str(mount) if mount else "usb")
        else:
            self.path_label.setText(f"{transport} (API)")

        if not entries:
            self.status_label.setText("Connected, but no files were returned")
            return

        self.status_label.setText("Loading file tree...")

        self._populate_entries(entries)
        self.tree.collapseAll()
        self._expand_hearth_folder()
        top_count = self.tree.topLevelItemCount()
        self.status_label.setText(f"Loaded {top_count} top-level entries")

    def _expand_hearth_folder(self) -> None:
        """Expand Hearth folder and its parents when present in the tree."""
        root_count = self.tree.topLevelItemCount()
        for i in range(root_count):
            root_item = self.tree.topLevelItem(i)
            if root_item is None:
                continue
            self._expand_hearth_in_subtree(root_item)

    def _expand_hearth_in_subtree(self, item: QTreeWidgetItem) -> bool:
        """Recursively find and expand Hearth node."""
        if item.text(0).strip().lower() == "hearth":
            item.setExpanded(True)
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            return True

        for idx in range(item.childCount()):
            child = item.child(idx)
            if child is None:
                continue
            if self._expand_hearth_in_subtree(child):
                return True

        return False

    def _populate_entries(self, entries: list[dict]) -> None:
        """Populate tree from a list of entries with full paths."""
        nodes: dict[str, QTreeWidgetItem] = {}
        sorted_entries = sorted(
            entries,
            key=lambda e: str(e.get("full_path", "")),
        )
        for entry in sorted_entries:
            full_path = str(entry.get("full_path", ""))
            if not full_path or full_path == "/":
                continue

            parts = [part for part in full_path.strip("/").split("/") if part]
            parent_item: Optional[QTreeWidgetItem] = None
            current_path = ""
            for idx, part in enumerate(parts):
                current_path += f"/{part}"
                if current_path in nodes:
                    parent_item = nodes[current_path]
                    continue

                is_leaf = idx == len(parts) - 1
                if is_leaf:
                    item = self._build_entry_item(entry)
                else:
                    item = QTreeWidgetItem([part, "Folder", "-", "-"])

                nodes[current_path] = item
                if parent_item is None:
                    self.tree.addTopLevelItem(item)
                else:
                    parent_item.addChild(item)
                parent_item = item

    def _build_entry_item(self, entry: dict) -> QTreeWidgetItem:
        """Build one tree item from an entry dict."""
        name = str(entry.get("name", "")) or str(entry.get("full_path", ""))
        is_dir = bool(entry.get("is_dir", False))
        item_type = "Folder" if is_dir else "File"
        size = int(entry.get("size", 0) or 0)
        size_text = "-" if is_dir else self._human_size(size)

        mod_time = str(entry.get("mod_time", "") or "-")
        if "T" in mod_time:
            mod_time = mod_time.replace("T", " ")

        return QTreeWidgetItem([name, item_type, size_text, mod_time])

    def _populate_tree(self, root_path: Path) -> None:
        """Populate tree by recursively scanning root_path."""
        children = sorted(
            root_path.iterdir(),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        for child in children:
            item = self._build_item(child)
            self.tree.addTopLevelItem(item)
            if child.is_dir():
                self._add_children(item, child)

    def _add_children(
        self,
        parent_item: QTreeWidgetItem,
        parent_path: Path,
    ) -> None:
        """Recursively add directory children."""
        try:
            children = sorted(
                parent_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return

        for child in children:
            item = self._build_item(child)
            parent_item.addChild(item)
            if child.is_dir():
                self._add_children(item, child)

    def _build_item(self, path: Path) -> QTreeWidgetItem:
        """Build one tree item for a filesystem path."""
        item_type = "Folder" if path.is_dir() else "File"
        size_text = "-"
        modified_text = "-"

        try:
            stat = path.stat()
            modified_text = datetime.fromtimestamp(stat.st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            if path.is_file():
                size_text = self._human_size(stat.st_size)
        except OSError:
            pass

        return QTreeWidgetItem([path.name, item_type, size_text, modified_text])

    def _human_size(self, size_bytes: int) -> str:
        """Format bytes as a readable string."""
        units = ["B", "KB", "MB", "GB"]
        size = float(size_bytes)
        idx = 0
        while size >= 1024 and idx < len(units) - 1:
            size /= 1024
            idx += 1
        if idx == 0:
            return f"{int(size)} {units[idx]}"
        return f"{size:.1f} {units[idx]}"
