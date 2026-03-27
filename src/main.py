"""
Ollama Manager Pro — v4.0.0
===========================
Entry point. Run with:  python main.py
"""
import sys

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QPalette

from logger import log  # initialise logging before anything else
from theme import STYLESHEET
from window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Ollama Manager Pro")
    app.setApplicationVersion("4.0.0")
    app.setOrganizationName("KeystoneAI")

    try:
        app.setStyle("Fusion")
    except Exception:
        pass

    # Dark palette base — prevents white flash before stylesheet loads
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window,          QColor("#0e1117"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#e2e8f0"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#111827"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#141c2b"))
    palette.setColor(QPalette.ColorRole.Text,            QColor("#e2e8f0"))
    palette.setColor(QPalette.ColorRole.Button,          QColor("#1e2533"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#94a3b8"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#1e3a5f"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#e2e8f0"))
    palette.setColor(QPalette.ColorRole.ToolTipBase,     QColor("#1e2533"))
    palette.setColor(QPalette.ColorRole.ToolTipText,     QColor("#e2e8f0"))
    app.setPalette(palette)

    app.setStyleSheet(STYLESHEET)

    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
