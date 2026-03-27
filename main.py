"""Main entry point for Hearth application."""

import sys
from PyQt6.QtWidgets import QApplication

from hearth.gui.main_window import HearthMainWindow


def main():
    """Run the Hearth application."""
    app = QApplication(sys.argv)
    window = HearthMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
