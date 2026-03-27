"""
Mixin — imported via multiple inheritance into MainWindow.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

from datetime import datetime
from PyQt6.QtCore import QThread, QTimer, Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from utils import human_bytes, parse_time, get_system_stats
from workers import ResourceMonitorWorker
from widgets import SectionLabel, Separator, GaugeBar, GaugeCard, MonitorSparkWidget
from models import RunningModelRow, RunningModelsTableModel
class MonitorMixin:
    def _tab_monitor(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        ctrl = QHBoxLayout()
        self.monitor_toggle_btn = QPushButton("▶  Start Monitor")
        self.monitor_toggle_btn.setObjectName("btn_primary")
        self.monitor_toggle_btn.setMinimumWidth(150)
        self.monitor_toggle_btn.clicked.connect(self._monitor_toggle)

        self.monitor_interval_combo = QComboBox()
        for label in ["1 s", "2 s", "5 s", "10 s"]:
            self.monitor_interval_combo.addItem(label)
        self.monitor_interval_combo.setCurrentIndex(1)
        self.monitor_interval_combo.currentIndexChanged.connect(self._monitor_update_interval)
        interval_lbl = QLabel("Interval:")
        interval_lbl.setStyleSheet("color:#64748b; font-size:12px; font-weight:600;")

        self.monitor_status_lbl = QLabel("Stopped")
        self.monitor_status_lbl.setObjectName("label_muted")

        ctrl.addWidget(self.monitor_toggle_btn)
        ctrl.addSpacing(12)
        ctrl.addWidget(interval_lbl)
        ctrl.addWidget(self.monitor_interval_combo)
        ctrl.addStretch(1)
        ctrl.addWidget(self.monitor_status_lbl)
        layout.addLayout(ctrl)
        layout.addWidget(Separator())

        gauges_row = QHBoxLayout()
        gauges_row.setSpacing(12)

        self.gauge_cpu = GaugeCard("CPU")
        self.cpu_bar = self.gauge_cpu.add_bar("Usage", "#3b82f6")

        self.gauge_ram = GaugeCard("RAM")
        self.ram_bar = self.gauge_ram.add_bar("Used", "#8b5cf6")

        self.gauge_vram = GaugeCard("GPU / VRAM")
        self.vram_bar = self.gauge_vram.add_bar("VRAM", "#10b981")
        self.gpu_util_bar = self.gauge_vram.add_bar("GPU util", "#06b6d4")

        for g in (self.gauge_cpu, self.gauge_ram, self.gauge_vram):
            gauges_row.addWidget(g, 1)
        layout.addLayout(gauges_row)

        spark_hdr = QHBoxLayout()
        spark_hdr.addWidget(SectionLabel("History  (last 60 samples)"))
        spark_hdr.addStretch(1)
        layout.addLayout(spark_hdr)

        self.monitor_spark = MonitorSparkWidget()
        self.monitor_spark.setMinimumHeight(120)
        layout.addWidget(self.monitor_spark)

        layout.addWidget(SectionLabel("Loaded Models  (/api/ps)"))
        self.monitor_running_lbl = QLabel("Not yet polled")
        self.monitor_running_lbl.setObjectName("label_muted")
        layout.addWidget(self.monitor_running_lbl)

        self.monitor_running_table = QTableView()
        self.monitor_running_table.setModel(self.running_model)
        self.monitor_running_table.setMaximumHeight(150)
        self.monitor_running_table.setAlternatingRowColors(True)
        self.monitor_running_table.setShowGrid(False)
        self.monitor_running_table.verticalHeader().hide()
        self.monitor_running_table.verticalHeader().setDefaultSectionSize(34)
        hh = self.monitor_running_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 5):
            hh.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.monitor_running_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.monitor_running_table)
        layout.addStretch(1)
        return container


    def _monitor_toggle(self):
        if self._monitor_timer and self._monitor_timer.isActive():
            self._monitor_timer.stop()
            self.monitor_toggle_btn.setText("▶  Start Monitor")
            self.monitor_toggle_btn.setObjectName("btn_primary")
            self.monitor_toggle_btn.setStyle(self.monitor_toggle_btn.style())
            self.monitor_status_lbl.setText("Stopped")
        else:
            if self._monitor_timer is None:
                self._monitor_timer = QTimer(self)
                self._monitor_timer.timeout.connect(self._monitor_poll)
            ms = [1000, 2000, 5000, 10000][self.monitor_interval_combo.currentIndex()]
            self._monitor_timer.setInterval(ms)
            self._monitor_timer.start()
            self.monitor_toggle_btn.setText("■  Stop Monitor")
            self.monitor_toggle_btn.setObjectName("btn_cancel")
            self.monitor_toggle_btn.setStyle(self.monitor_toggle_btn.style())
            self.monitor_status_lbl.setText("Running…")
            self._monitor_poll()


    def _monitor_update_interval(self):
        if self._monitor_timer and self._monitor_timer.isActive():
            ms = [1000, 2000, 5000, 10000][self.monitor_interval_combo.currentIndex()]
            self._monitor_timer.setInterval(ms)


    def _monitor_poll(self):
        worker = ResourceMonitorWorker(self.client)
        worker.stats_ready.connect(self._monitor_on_stats)
        worker.failed.connect(lambda _: None)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stats_ready.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        # Hold strong ref to prevent GC before thread fires
        self._monitor_worker = worker
        thread.finished.connect(lambda: setattr(self, "_monitor_worker", None))
        self._register_thread(thread)
        thread.start()


    def _monitor_on_stats(self, stats: dict):
        cpu = stats.get("cpu_pct")
        if cpu is not None:
            self.gauge_cpu.set_value(f"{cpu:.0f}%")
            self.cpu_bar.set_value(cpu)
            self._monitor_history["cpu"].append(cpu)
        else:
            self.gauge_cpu.set_value("n/a  (install psutil)")

        ram_used, ram_total = stats.get("ram_used"), stats.get("ram_total")
        if ram_used is not None and ram_total:
            pct = ram_used / ram_total * 100
            self.gauge_ram.set_value(
                f"{human_bytes(ram_used)} / {human_bytes(ram_total)}")
            self.ram_bar.set_value(pct)
            self._monitor_history["ram"].append(pct)
        else:
            self.gauge_ram.set_value("n/a  (install psutil)")

        vram_used = stats.get("gpu_vram_used")
        vram_total = stats.get("gpu_vram_total")
        gpu_util = stats.get("gpu_util_pct")
        gpu_name = stats.get("gpu_name") or "GPU"
        if vram_used is not None and vram_total:
            pct = vram_used / vram_total * 100
            self.gauge_vram._title_lbl.setText(gpu_name[:26].upper())
            self.gauge_vram.set_value(
                f"{human_bytes(vram_used)} / {human_bytes(vram_total)}")
            self.vram_bar.set_value(pct)
            self._monitor_history["vram"].append(pct)
            if gpu_util is not None:
                self.gpu_util_bar.set_value(gpu_util)
        else:
            self.gauge_vram.set_value("No GPU detected")

        for key in self._monitor_history:
            if len(self._monitor_history[key]) > 60:
                self._monitor_history[key] = self._monitor_history[key][-60:]

        self.monitor_spark.update_data(
            self._monitor_history["cpu"],
            self._monitor_history["ram"],
            self._monitor_history["vram"])

        running = stats.get("running_models", [])
        rows = []
        for m in running:
            rows.append(RunningModelRow(
                name=str(m.get("name", "")),
                size=human_bytes(m.get("size")),
                processor=str(m.get("processor", "—")),
                context=str(m.get("context", "—")),
                until=parse_time(m.get("expires_at") or m.get("until")),
                raw=m))
        self.running_model.set_rows(rows)
        n = len(rows)
        self.monitor_running_lbl.setText(
            f"{n} model{'s' if n != 1 else ''} loaded in memory")
        self.monitor_status_lbl.setText(
            f"Last polled {datetime.now().strftime('%H:%M:%S')}")

    # ══════════════════════════════════════════
    # MODELFILE EDITOR TAB
    # ══════════════════════════════════════════

    _MODELFILE_TEMPLATE = 'FROM {base_model}\n\n# System prompt\nSYSTEM """\nYou are a helpful assistant.\n"""\n\n# Inference parameters\nPARAMETER temperature 0.7\nPARAMETER top_p 0.9\nPARAMETER top_k 40\nPARAMETER num_ctx 4096\nPARAMETER repeat_penalty 1.1\n'
