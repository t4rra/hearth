"""Converter page for Hearth GUI."""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFileDialog,
    QTextEdit,
    QProgressBar,
    QComboBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QTextCursor
from pathlib import Path

from ..converters.manager import ConverterManager
from ..converters.base import ConversionFormat


class ConversionWorker(QThread):
    """Worker thread for file conversion."""

    progress = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, converter, input_path, output_format):
        super().__init__()
        self.converter = converter
        self.input_path = Path(input_path)
        self.output_format = output_format

    def run(self):
        """Run conversion in background."""
        try:
            result = self.converter.convert(self.input_path, self.output_format)
            if result.success:
                self.finished.emit(True, f"Conversion successful: {result.output_path}")
            else:
                self.finished.emit(False, f"Conversion failed: {result.error}")
        except Exception as e:
            self.finished.emit(False, f"Error: {str(e)}")


class ConverterPage(QWidget):
    """File conversion page."""

    def __init__(self):
        super().__init__()
        self.converter = ConverterManager()
        self.conversion_worker = None
        self.init_ui()

    def init_ui(self):
        """Initialize UI elements."""
        layout = QVBoxLayout()
        self.setLayout(layout)

        # File selection
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("Input File:"))
        self.file_input = QLineEdit() if hasattr(self, "file_input") else None
        if self.file_input is None:
            from PyQt6.QtWidgets import QLineEdit

            self.file_input = QLineEdit()
            self.file_input.setReadOnly(True)
        file_layout.addWidget(self.file_input)

        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_file)
        file_layout.addWidget(browse_btn)
        layout.addLayout(file_layout)

        # Output format
        format_layout = QHBoxLayout()
        format_layout.addWidget(QLabel("Output Format:"))
        self.output_format = QComboBox()
        self.output_format.addItems(["MOBI", "AZW3", "EPUB"])
        format_layout.addWidget(self.output_format)
        format_layout.addStretch()
        layout.addLayout(format_layout)

        # Convert button
        convert_btn = QPushButton("Convert")
        convert_btn.clicked.connect(self.convert_file)
        layout.addWidget(convert_btn)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # Output log
        layout.addWidget(QLabel("Output:"))
        self.output_log = QTextEdit()
        self.output_log.setReadOnly(True)
        layout.addWidget(self.output_log)

    def browse_file(self):
        """Browse for file to convert."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select File to Convert",
            "",
            "All Supported (*.cbz *.cbr *.epub *.mobi *.pdf);;Comic Files (*.cbz *.cbr);;EBook Files (*.epub *.mobi *.pdf)",
        )
        if file_path:
            self.file_input.setText(file_path)

    def convert_file(self):
        """Convert selected file."""
        if not self.file_input.text():
            self.log_output("Please select a file first")
            return

        input_path = Path(self.file_input.text())
        if not self.converter.can_convert(input_path):
            self.log_output(f"Error: Cannot convert {input_path.suffix} files")
            return

        format_map = {
            "MOBI": ConversionFormat.MOBI,
            "AZW3": ConversionFormat.AZW3,
            "EPUB": ConversionFormat.EPUB,
        }
        output_format = format_map[self.output_format.currentText()]

        self.log_output(f"Starting conversion of {input_path.name}...")
        self.progress_bar.setVisible(True)

        self.conversion_worker = ConversionWorker(
            self.converter, input_path, output_format
        )
        self.conversion_worker.progress.connect(self.log_output)
        self.conversion_worker.finished.connect(self.on_conversion_finished)
        self.converter.set_progress_callback(self.log_output)
        self.conversion_worker.start()

    def on_conversion_finished(self, success: bool, message: str):
        """Handle conversion completion."""
        self.progress_bar.setVisible(False)
        self.log_output(message)

    def log_output(self, message: str):
        """Add message to output log."""
        self.output_log.moveCursor(QTextCursor.MoveOperation.End)
        self.output_log.insertPlainText(message + "\n")


# Add missing import
from PyQt6.QtWidgets import QLineEdit
