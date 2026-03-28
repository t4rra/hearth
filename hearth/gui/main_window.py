"""Main GUI window for Hearth."""

from PyQt6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QTabWidget

from .settings_page import SettingsPage
from .converter_page import ConverterPage
from .sync_page import SyncPage
from .kindle_files_page import KindleFilesPage


class HearthMainWindow(QMainWindow):
    """Main window for Hearth application."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Hearth - Kindle Library Sync")
        self.setGeometry(100, 100, 900, 600)

        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Create layout
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # Create tab widget
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        # Add pages
        self.sync_page = SyncPage()
        self.settings_page = SettingsPage(self.sync_page.sync_manager.kindle)
        self.converter_page = ConverterPage()
        self.kindle_files_page = KindleFilesPage(self.sync_page.sync_manager.kindle)

        self.tabs.addTab(self.sync_page, "Library")
        self.tabs.addTab(self.settings_page, "Settings")
        self.tabs.addTab(self.converter_page, "Converter")
        self.tabs.addTab(self.kindle_files_page, "Kindle Files")

        # Status bar
        self.statusBar().showMessage("Ready")

    def closeEvent(self, event):
        """Handle window close."""
        event.accept()
