import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
import collections
import platform
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests
from PyQt6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QObject, QThread,
    pyqtSignal, QTimer, QSize,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QPalette, QPixmap, QPainter,
    QBrush, QPen, QLinearGradient, QSyntaxHighlighter, QTextCharFormat,
)
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QSplitter, QSpacerItem,
    QStackedWidget, QStatusBar, QTabWidget, QTableView, QTextEdit,
    QToolBar, QVBoxLayout, QWidget, QAbstractItemView,
)

# ─────────────────────────────────────────────
#  THEME
# ─────────────────────────────────────────────

STYLESHEET = """
/* ── Global ────────────────────────────────── */
QMainWindow, QDialog {
    background-color: #0e1117;
    color: #e2e8f0;
}

QWidget {
    background-color: #0e1117;
    color: #e2e8f0;
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

/* ── Toolbar ────────────────────────────────── */
QToolBar {
    background-color: #0e1117;
    border-bottom: 1px solid #1e2533;
    padding: 4px 8px;
    spacing: 6px;
}

QToolBar QLabel {
    color: #64748b;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
}

QToolBar QLineEdit {
    background-color: #161c27;
    border: 1px solid #2a3347;
    border-radius: 6px;
    color: #e2e8f0;
    padding: 5px 10px;
    font-size: 13px;
    min-width: 260px;
    selection-background-color: #3b82f6;
}

QToolBar QLineEdit:focus {
    border-color: #3b82f6;
    background-color: #1a2235;
}

/* ── Toolbar Actions ────────────────────────── */
QToolButton {
    background-color: transparent;
    border: 1px solid #2a3347;
    border-radius: 6px;
    color: #94a3b8;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 12px;
    min-width: 80px;
}

QToolButton:hover {
    background-color: #1e2a3d;
    border-color: #3b5580;
    color: #e2e8f0;
}

QToolButton:pressed {
    background-color: #172035;
    border-color: #2563eb;
}

/* ── Tab Widget ─────────────────────────────── */
QTabWidget::pane {
    border: none;
    background-color: #0e1117;
}

QTabWidget::tab-bar {
    left: 0px;
}

QTabBar::tab {
    background-color: transparent;
    color: #64748b;
    font-size: 13px;
    font-weight: 600;
    padding: 10px 22px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
}

QTabBar::tab:selected {
    color: #60a5fa;
    border-bottom: 2px solid #3b82f6;
    background-color: transparent;
}

QTabBar::tab:hover:!selected {
    color: #94a3b8;
    border-bottom: 2px solid #2a3347;
}

/* ── Table ──────────────────────────────────── */
QTableView {
    background-color: #111827;
    border: 1px solid #1e2533;
    border-radius: 8px;
    gridline-color: #1a2235;
    color: #e2e8f0;
    font-size: 13px;
    selection-background-color: #1e3a5f;
    selection-color: #e2e8f0;
    alternate-background-color: #141c2b;
    outline: none;
}

QTableView::item {
    padding: 8px 12px;
    border: none;
}

QTableView::item:selected {
    background-color: #1e3a5f;
    color: #e2e8f0;
}

QTableView::item:hover:!selected {
    background-color: #172035;
}

QHeaderView::section {
    background-color: #0e1117;
    color: #64748b;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    padding: 8px 12px;
    border: none;
    border-bottom: 1px solid #1e2533;
}

QHeaderView::section:horizontal {
    border-right: 1px solid #1e2533;
}

QHeaderView {
    background-color: #0e1117;
}

/* ── QPushButton ────────────────────────────── */
QPushButton {
    background-color: #1e2533;
    border: 1px solid #2a3347;
    border-radius: 6px;
    color: #94a3b8;
    font-size: 13px;
    font-weight: 600;
    padding: 7px 16px;
    min-height: 32px;
}

QPushButton:hover {
    background-color: #1e2a3d;
    border-color: #3b5580;
    color: #cbd5e1;
}

QPushButton:pressed {
    background-color: #172035;
    border-color: #2563eb;
}

QPushButton:disabled {
    background-color: #131923;
    border-color: #1e2533;
    color: #374151;
}

/* Primary action button */
QPushButton#btn_primary {
    background-color: #2563eb;
    border-color: #1d4ed8;
    color: #ffffff;
}

QPushButton#btn_primary:hover {
    background-color: #1d4ed8;
    border-color: #1e40af;
}

QPushButton#btn_primary:pressed {
    background-color: #1e40af;
}

QPushButton#btn_primary:disabled {
    background-color: #1e3a5f;
    border-color: #1e3a5f;
    color: #4b6a9e;
}

/* Danger button */
QPushButton#btn_danger {
    background-color: transparent;
    border: 1px solid #7f1d1d;
    color: #f87171;
}

QPushButton#btn_danger:hover {
    background-color: #3b0f0f;
    border-color: #ef4444;
    color: #fca5a5;
}

QPushButton#btn_danger:disabled {
    border-color: #2a1a1a;
    color: #4b2020;
}

/* Cancel button */
QPushButton#btn_cancel {
    background-color: transparent;
    border: 1px solid #374151;
    color: #6b7280;
}

QPushButton#btn_cancel:hover {
    border-color: #4b5563;
    color: #9ca3af;
}

QPushButton#btn_cancel:disabled {
    border-color: #1f2937;
    color: #374151;
}

/* ── QLineEdit ──────────────────────────────── */
QLineEdit {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 6px;
    color: #e2e8f0;
    padding: 7px 12px;
    font-size: 13px;
    selection-background-color: #2563eb;
    min-height: 32px;
}

QLineEdit:focus {
    border-color: #3b82f6;
    background-color: #131d2e;
}

QLineEdit:disabled {
    background-color: #0d1117;
    color: #4b5563;
    border-color: #1a2030;
}

/* ── QTextEdit ──────────────────────────────── */
QTextEdit {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 8px;
    color: #94a3b8;
    padding: 12px;
    font-family: "Cascadia Code", "Fira Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    selection-background-color: #1e3a5f;
    line-height: 1.5;
}

QTextEdit:focus {
    border-color: #2563eb;
}

/* ── QProgressBar ───────────────────────────── */
QProgressBar {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 5px;
    color: #60a5fa;
    font-size: 11px;
    font-weight: 700;
    text-align: center;
    height: 20px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #1d4ed8, stop:1 #3b82f6);
    border-radius: 4px;
}

/* ── QCheckBox ──────────────────────────────── */
QCheckBox {
    color: #94a3b8;
    font-size: 13px;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #374151;
    border-radius: 4px;
    background-color: #111827;
}

QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
    image: none;
}

QCheckBox::indicator:hover {
    border-color: #3b82f6;
}

QCheckBox:hover {
    color: #cbd5e1;
}

/* ── QGroupBox ──────────────────────────────── */
QGroupBox {
    background-color: #111827;
    border: 1px solid #1e2533;
    border-radius: 10px;
    color: #94a3b8;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.6px;
    text-transform: uppercase;
    margin-top: 14px;
    padding: 16px 16px 12px 16px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    top: -1px;
    background-color: #111827;
    padding: 2px 8px;
    color: #64748b;
    border-radius: 4px;
}

/* ── QSplitter ──────────────────────────────── */
QSplitter::handle {
    background-color: #1e2533;
    width: 1px;
    height: 1px;
}

QSplitter::handle:horizontal {
    width: 1px;
}

/* ── Status bar ─────────────────────────────── */
QStatusBar {
    background-color: #080c13;
    border-top: 1px solid #1e2533;
    color: #4b5563;
    font-size: 12px;
    padding: 0 12px;
}

QStatusBar::item {
    border: none;
}

/* ── Scrollbars ─────────────────────────────── */
QScrollBar:vertical {
    background-color: #0e1117;
    border: none;
    width: 8px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background-color: #2a3347;
    border-radius: 4px;
    min-height: 30px;
}

QScrollBar::handle:vertical:hover {
    background-color: #3b4d6b;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
    background: none;
}

QScrollBar:horizontal {
    background-color: #0e1117;
    border: none;
    height: 8px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background-color: #2a3347;
    border-radius: 4px;
    min-width: 30px;
}

QScrollBar::handle:horizontal:hover {
    background-color: #3b4d6b;
}

QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal {
    width: 0;
    background: none;
}

/* ── QLabel variants ────────────────────────── */
QLabel#label_section {
    color: #3b82f6;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
}

QLabel#label_detail_heading {
    color: #e2e8f0;
    font-size: 15px;
    font-weight: 700;
}

QLabel#label_muted {
    color: #4b5563;
    font-size: 12px;
}

QLabel#status_dot_green {
    color: #10b981;
    font-size: 18px;
}

QLabel#status_dot_red {
    color: #ef4444;
    font-size: 18px;
}

QLabel#status_dot_gray {
    color: #374151;
    font-size: 18px;
}

/* ── Frame separators ───────────────────────── */
QFrame#separator {
    background-color: #1e2533;
    max-height: 1px;
    min-height: 1px;
}

QFrame#separator_v {
    background-color: #1e2533;
    max-width: 1px;
    min-width: 1px;
}

/* ── Sidebar ────────────────────────────────── */
QFrame#sidebar {
    background-color: #080c13;
    border-right: 1px solid #1e2533;
}

/* ── Card/Panel ─────────────────────────────── */
QFrame#card {
    background-color: #111827;
    border: 1px solid #1e2533;
    border-radius: 10px;
}

/* ── Connection badge ───────────────────────── */
QLabel#conn_badge_connected {
    background-color: #052e16;
    border: 1px solid #166534;
    border-radius: 10px;
    color: #4ade80;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 10px;
}

QLabel#conn_badge_disconnected {
    background-color: #1c0909;
    border: 1px solid #7f1d1d;
    border-radius: 10px;
    color: #f87171;
    font-size: 11px;
    font-weight: 700;
    padding: 2px 10px;
}

/* ── Tooltip ────────────────────────────────── */
QToolTip {
    background-color: #1e2533;
    border: 1px solid #3b4d6b;
    color: #e2e8f0;
    font-size: 12px;
    padding: 6px 10px;
    border-radius: 6px;
}

/* ── MessageBox ─────────────────────────────── */
QMessageBox {
    background-color: #111827;
}

QMessageBox QLabel {
    color: #e2e8f0;
    font-size: 13px;
}

/* ── Dialog ─────────────────────────────────── */
QDialog {
    background-color: #111827;
    border: 1px solid #1e2533;
}

/* ── QComboBox ──────────────────────────────── */
QComboBox {
    background-color: #111827;
    border: 1px solid #1f2937;
    border-radius: 6px;
    color: #e2e8f0;
    padding: 6px 32px 6px 12px;
    font-size: 13px;
    min-height: 32px;
    min-width: 120px;
}
QComboBox:hover { border-color: #3b5580; }
QComboBox:focus { border-color: #3b82f6; }
QComboBox::drop-down { border: none; width: 28px; }
QComboBox::down-arrow {
    width: 10px; height: 10px;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid #64748b;
    margin-right: 8px;
}
QComboBox QAbstractItemView {
    background-color: #111827;
    border: 1px solid #2a3347;
    color: #e2e8f0;
    selection-background-color: #1e3a5f;
    outline: none;
    padding: 4px;
}
QComboBox QAbstractItemView::item {
    padding: 7px 12px;
    min-height: 30px;
}

/* ── Registry cards ─────────────────────────── */
QFrame#registry_card {
    background-color: #111827;
    border: 1px solid #1e2533;
    border-radius: 10px;
}
QFrame#registry_card:hover {
    border-color: #3b5580;
    background-color: #141e30;
}

/* ── Benchmark result cards ─────────────────── */
QFrame#bench_result {
    background-color: #111827;
    border: 1px solid #1e2533;
    border-radius: 8px;
}
QFrame#bench_result_winner {
    background-color: #0d1f14;
    border: 1px solid #166534;
    border-radius: 8px;
}

/* ── Tag badges ─────────────────────────────── */
QLabel#tag_badge {
    background-color: #1e2a3d;
    border: 1px solid #2a3d5c;
    border-radius: 4px;
    color: #60a5fa;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 7px;
}
QLabel#tag_badge_installed {
    background-color: #052e16;
    border: 1px solid #166534;
    border-radius: 4px;
    color: #4ade80;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 7px;
}


/* ── Monitor gauges ─────────────────────────── */
QFrame#gauge_card {
    background-color: #0a0f18;
    border: 1px solid #1e2533;
    border-radius: 10px;
}
/* ── Modelfile editor ───────────────────────── */
QTextEdit#modelfile_editor {
    background-color: #080c13;
    border: 1px solid #1e2533;
    border-radius: 8px;
    color: #a5f3fc;
    font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
    font-size: 13px;
    padding: 14px;
}
QTextEdit#modelfile_preview {
    background-color: #080c13;
    border: 1px solid #1e2533;
    border-radius: 8px;
    color: #6ee7b7;
    font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
    font-size: 12px;
    padding: 14px;
}
/* ── Disk rows ──────────────────────────────── */
QFrame#orphan_row {
    background-color: #1c0909;
    border: 1px solid #7f1d1d;
    border-radius: 6px;
}
QFrame#disk_model_row {
    background-color: #111827;
    border: 1px solid #1e2533;
    border-radius: 6px;
}
"""


