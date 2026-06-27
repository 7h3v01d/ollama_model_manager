"""
Mixin — Settings tab.

Sections:
  - Voice Gateway (TTS)  — URL, endpoint, payload key, test button, live log
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from window import MainWindow

import json
import requests as _requests

from PyQt6.QtCore import Qt, QObject, QThread, pyqtSignal, pyqtSlot, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea,
    QTextEdit, QVBoxLayout, QWidget,
)

from logger import log
from widgets import SectionLabel, Separator


# ── Test worker ────────────────────────────────────────────────────────────

class VGTestThread(QThread):
    """
    Subclass QThread directly — most reliable way to avoid slot-not-firing
    issues when moveToThread + started.connect is finicky.
    """
    log_line = pyqtSignal(str)
    done     = pyqtSignal(bool)

    def __init__(self, url: str, method: str, payload_key: str, text: str):
        super().__init__()
        self.url         = url
        self.method      = method
        self.payload_key = payload_key
        self.text        = text

    def run(self):
        import socket, json as _json
        import requests as _req
        from urllib.parse import urlparse

        # ── TCP check ─────────────────────────────────────────────────
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.log_line.emit(f"① TCP check {host}:{port} …")
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            self.log_line.emit("   ✓ reachable")
        except Exception as e:
            self.log_line.emit(f"   ✗ {e}")
            self.log_line.emit("   → Check Base URL / VG is running")
            self.done.emit(False)
            return

        # ── HTTP request — form-data first, then JSON ──────────────────
        payload = {self.payload_key: self.text}
        fn = getattr(_req, self.method.lower())

        for label, kwargs in [
            ("application/json", {"json": payload}),
            ("form-data",        {"data": payload}),
        ]:
            self.log_line.emit(f"\n② {self.method} {self.url}")
            self.log_line.emit(f"   encoding : {label}")
            self.log_line.emit(f"   payload  : {_json.dumps(payload)}")
            try:
                r = fn(self.url, timeout=60, **kwargs)
                self.log_line.emit(f"   status   : {r.status_code} {r.reason}")
                if r.status_code < 400:
                    wav_bytes = r.content
                    self.log_line.emit(f"   received : {len(wav_bytes):,} bytes")
                    if wav_bytes[:4] == b"RIFF":
                        self.log_line.emit("   Playing audio…")
                        import tempfile, os
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                            f.write(wav_bytes)
                            tmp = f.name
                        try:
                            import winsound
                            winsound.PlaySound(tmp, winsound.SND_FILENAME)
                            self.log_line.emit("✓ Playback complete")
                        except Exception as pe:
                            self.log_line.emit(f"   winsound error: {pe}")
                        finally:
                            try: os.unlink(tmp)
                            except Exception: pass
                    else:
                        self.log_line.emit(f"   body     : {wav_bytes[:120]!r}")
                    self.done.emit(True)
                    return
                else:
                    self.log_line.emit(f"   body     : {r.content[:120]!r}")
                    self.log_line.emit(f"   ✗ HTTP {r.status_code} — trying next…")
            except _req.exceptions.Timeout:
                self.log_line.emit(f"   ✗ Timed out with {label} — trying next…")
            except Exception as e:
                self.log_line.emit(f"   ✗ {type(e).__name__}: {e}")

        self.log_line.emit("\n✗ All encodings failed — check endpoint path.")
        self.done.emit(False)


class PlayAudioThread(QThread):
    """
    Identical code path to TTSThread in tab_chat.py.
    Downloads full WAV from VG and plays it via winsound.
    Used by the Settings 'Play Audio Test' button.
    """
    log_line = pyqtSignal(str)

    def __init__(self, url: str, method: str, payload_key: str, text: str, voice: str = ""):
        super().__init__()
        self.url         = url
        self.method      = method
        self.payload_key = payload_key
        self.text        = text
        self.voice       = voice

    def run(self):
        import tempfile, os
        import requests as _req

        self.log_line.emit("   Sending request…")
        payload = {self.payload_key: self.text}
        if self.voice:
            payload["voice"] = self.voice
        try:
            fn = getattr(_req, self.method.lower())
            r = fn(
                self.url,
                json=payload,
                timeout=60,
            )
            self.log_line.emit(f"   status  : {r.status_code} {r.reason}")
            if r.status_code >= 400:
                self.log_line.emit(f"   ✗ HTTP {r.status_code}")
                return

            wav_bytes = r.content
            self.log_line.emit(f"   received: {len(wav_bytes):,} bytes")
            if len(wav_bytes) < 44 or wav_bytes[:4] != b"RIFF":
                self.log_line.emit(f"   ✗ Not a WAV file (header={wav_bytes[:8]!r})")
                return

            self.log_line.emit("   Writing temp WAV…")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(wav_bytes)
                tmp_path = f.name
            self.log_line.emit(f"   Playing  : {tmp_path}")

            try:
                import winsound
                self.log_line.emit("   winsound.PlaySound() — blocking until done…")
                winsound.PlaySound(tmp_path, winsound.SND_FILENAME)
                self.log_line.emit("   ✓ Playback complete")
            except ImportError:
                self.log_line.emit("   winsound not available (non-Windows)")
                try:
                    import sounddevice as sd, soundfile as sf, io
                    data, sr = sf.read(io.BytesIO(wav_bytes))
                    sd.play(data, sr)
                    sd.wait()
                    self.log_line.emit("   ✓ Playback complete (sounddevice)")
                except Exception as e:
                    self.log_line.emit(f"   ✗ Playback failed: {e}")
            except Exception as e:
                self.log_line.emit(f"   ✗ winsound error: {type(e).__name__}: {e}")
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        except Exception as e:
            self.log_line.emit(f"   ✗ {type(e).__name__}: {e}")


class SettingsMixin:

    def _tab_settings(self) -> QWidget:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background:#0a0f18; border:none; }")

        inner = QWidget()
        inner.setStyleSheet("background:#0a0f18;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(24, 20, 24, 20)
        il.setSpacing(20)

        # ── Voice Gateway section ─────────────────────────────────────
        vg_box = QGroupBox("Voice Gateway  (TTS)")
        vg_box.setStyleSheet("""
            QGroupBox {
                color:#94a3b8; font-size:12px; font-weight:700;
                border:1px solid #1e2533; border-radius:8px;
                margin-top:8px; padding-top:12px;
            }
            QGroupBox::title {
                subcontrol-origin:margin; left:12px; padding:0 6px;
            }
        """)
        vg_l = QVBoxLayout(vg_box)
        vg_l.setSpacing(10)

        # Enable toggle
        enable_row = QHBoxLayout()
        self.vg_enabled_chk = QCheckBox("Enable Voice Gateway TTS")
        self.vg_enabled_chk.setStyleSheet("color:#e2e8f0; font-size:12px;")
        self.vg_enabled_chk.setChecked(bool(self._settings.get("vg_enabled")))
        self.vg_enabled_chk.toggled.connect(self._settings_vg_toggled)
        enable_row.addWidget(self.vg_enabled_chk)
        enable_row.addStretch(1)

        self.vg_status_dot = QLabel("●")
        self.vg_status_dot.setStyleSheet("color:#374151; font-size:18px;")
        enable_row.addWidget(self.vg_status_dot)
        vg_l.addLayout(enable_row)

        desc = QLabel(
            "When enabled, completed assistant replies in the Chat tab are "
            "sent to your Voice Gateway for text-to-speech playback.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#4b5563; font-size:11px;")
        vg_l.addWidget(desc)

        vg_l.addWidget(self._settings_sep())

        # URL row
        url_row = QHBoxLayout()
        url_lbl = QLabel("Base URL")
        url_lbl.setFixedWidth(110)
        url_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_url_input = QLineEdit(self._settings.get("vg_url"))
        self.vg_url_input.setPlaceholderText("http://192.168.0.163:8050")
        self.vg_url_input.setStyleSheet(self._settings_input_style())
        url_row.addWidget(url_lbl)
        url_row.addWidget(self.vg_url_input, 1)
        vg_l.addLayout(url_row)

        # Endpoint row
        ep_row = QHBoxLayout()
        ep_lbl = QLabel("Endpoint")
        ep_lbl.setFixedWidth(110)
        ep_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_endpoint_input = QLineEdit(self._settings.get("vg_endpoint"))
        self.vg_endpoint_input.setPlaceholderText("/tts")
        self.vg_endpoint_input.setStyleSheet(self._settings_input_style())
        ep_row.addWidget(ep_lbl)
        ep_row.addWidget(self.vg_endpoint_input, 1)

        ep_hint = QLabel("Full URL preview:")
        ep_hint.setStyleSheet("color:#374151; font-size:10px;")
        self.vg_full_url_lbl = QLabel(self._settings.vg_full_url)
        self.vg_full_url_lbl.setStyleSheet(
            "color:#60a5fa; font-size:10px; font-family:monospace;")
        vg_l.addLayout(ep_row)

        full_row = QHBoxLayout()
        full_row.addSpacing(114)
        full_row.addWidget(ep_hint)
        full_row.addWidget(self.vg_full_url_lbl, 1)
        vg_l.addLayout(full_row)

        # Update preview on change
        self.vg_url_input.textChanged.connect(self._settings_update_url_preview)
        self.vg_endpoint_input.textChanged.connect(self._settings_update_url_preview)

        # Method row
        method_row = QHBoxLayout()
        method_lbl = QLabel("Method")
        method_lbl.setFixedWidth(110)
        method_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_method_combo = QComboBox()
        self.vg_method_combo.addItems(["POST", "GET"])
        self.vg_method_combo.setCurrentText(self._settings.get("vg_method"))
        self.vg_method_combo.setFixedWidth(100)
        method_row.addWidget(method_lbl)
        method_row.addWidget(self.vg_method_combo)
        method_row.addStretch(1)
        vg_l.addLayout(method_row)

        # Payload key row
        pk_row = QHBoxLayout()
        pk_lbl = QLabel("JSON text key")
        pk_lbl.setFixedWidth(110)
        pk_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_payload_key_input = QLineEdit(self._settings.get("vg_payload_key"))
        self.vg_payload_key_input.setPlaceholderText("text")
        self.vg_payload_key_input.setFixedWidth(160)
        self.vg_payload_key_input.setStyleSheet(self._settings_input_style())
        pk_hint = QLabel('The key in the JSON body that carries the text — e.g. "text", "input", "content"')
        pk_hint.setStyleSheet("color:#374151; font-size:10px;")
        pk_row.addWidget(pk_lbl)
        pk_row.addWidget(self.vg_payload_key_input)
        pk_row.addSpacing(10)
        pk_row.addWidget(pk_hint, 1)
        vg_l.addLayout(pk_row)

        # Example payload preview
        self.vg_payload_preview = QLabel()
        self.vg_payload_preview.setStyleSheet(
            "color:#4b6a9e; font-size:10px; font-family:monospace; "
            "background:#080c13; border:1px solid #1e2533; border-radius:4px; padding:6px 10px;")
        self.vg_payload_key_input.textChanged.connect(self._settings_update_payload_preview)
        vg_l.addWidget(self.vg_payload_preview)
        self._settings_update_payload_preview()

        vg_l.addWidget(self._settings_sep())

        # Test phrase + test button
        test_row = QHBoxLayout()
        test_lbl = QLabel("Test phrase")
        test_lbl.setFixedWidth(110)
        test_lbl.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_test_phrase_input = QLineEdit(self._settings.get("vg_test_phrase"))
        self.vg_test_phrase_input.setStyleSheet(self._settings_input_style())
        test_row.addWidget(test_lbl)
        test_row.addWidget(self.vg_test_phrase_input, 1)
        vg_l.addLayout(test_row)

        btn_row = QHBoxLayout()
        self.vg_save_btn = QPushButton("💾  Save")
        self.vg_save_btn.setMinimumWidth(90)
        self.vg_save_btn.setObjectName("btn_primary")
        self.vg_save_btn.clicked.connect(self._settings_save_vg)

        self.vg_test_btn = QPushButton("▶  Send Test")
        self.vg_test_btn.setMinimumWidth(110)
        self.vg_test_btn.clicked.connect(self._settings_test_vg)

        self.vg_play_btn = QPushButton("🔊  Play Audio Test")
        self.vg_play_btn.setMinimumWidth(130)
        self.vg_play_btn.setObjectName("btn_primary")
        self.vg_play_btn.setToolTip(
            "Download full WAV from VG and play it via winsound — "
            "same code path as the Chat TTS. Use this to confirm audio works.")
        self.vg_play_btn.clicked.connect(self._settings_play_audio_test)

        self.vg_clear_log_btn = QPushButton("Clear log")
        self.vg_clear_log_btn.setMinimumWidth(80)
        self.vg_clear_log_btn.setStyleSheet("font-size:11px;")
        self.vg_clear_log_btn.clicked.connect(lambda: self.vg_log.clear())

        btn_row.addWidget(self.vg_save_btn)
        btn_row.addWidget(self.vg_test_btn)
        btn_row.addWidget(self.vg_play_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.vg_clear_log_btn)
        vg_l.addLayout(btn_row)

        # Test log
        self.vg_log = QTextEdit()
        self.vg_log.setReadOnly(True)
        self.vg_log.setFixedHeight(130)
        self.vg_log.setPlaceholderText("Test output appears here…")
        self.vg_log.setStyleSheet(
            "QTextEdit { background:#080c13; border:1px solid #1e2533; border-radius:4px; "
            "color:#4b6a9e; font-size:11px; font-family:'Cascadia Code','Consolas',monospace; "
            "padding:6px; }")
        vg_l.addWidget(self.vg_log)

        # Chat auto-TTS
        vg_l.addWidget(self._settings_sep())

        # ── Voice selection ───────────────────────────────────────────
        voice_hdr = QHBoxLayout()
        voice_title = QLabel("Voice")
        voice_title.setFixedWidth(110)
        voice_title.setStyleSheet("color:#64748b; font-size:11px; font-weight:600;")
        self.vg_voice_combo = QComboBox()
        self.vg_voice_combo.setEditable(True)
        self.vg_voice_combo.setMinimumWidth(220)
        self.vg_voice_combo.setToolTip(
            "Voice/speaker name sent as 'voice' in the TTS payload. "
            "Leave blank to use the VG default. Click Fetch to load from /engines.")
        # Pre-load known Kokoro voices
        kokoro_voices = [
            "", "af_heart", "af_alloy", "af_aoede", "af_bella",
            "af_jessica", "af_kore", "af_nicole", "af_nova", "af_river",
            "af_sarah", "af_sky", "am_adam", "am_echo", "am_eric",
            "am_fenrir", "am_liam", "am_michael", "am_onyx", "am_puck",
            "am_santa", "bf_alice", "bf_emma", "bf_isabella", "bf_lily",
            "bm_daniel", "bm_fable", "bm_george", "bm_lewis",
        ]
        self.vg_voice_combo.addItems(kokoro_voices)
        saved_voice = self._settings.get("vg_voice") or ""
        if saved_voice and saved_voice not in kokoro_voices:
            self.vg_voice_combo.insertItem(1, saved_voice)
        self.vg_voice_combo.setCurrentText(saved_voice)

        self.vg_fetch_voices_btn = QPushButton("⟳ Fetch")
        self.vg_fetch_voices_btn.setFixedWidth(70)
        self.vg_fetch_voices_btn.setToolTip("Fetch available voices from GET /engines")
        self.vg_fetch_voices_btn.clicked.connect(self._settings_fetch_voices)

        voice_hint = QLabel("empty = VG default")
        voice_hint.setStyleSheet("color:#374151; font-size:10px;")

        voice_hdr.addWidget(voice_title)
        voice_hdr.addWidget(self.vg_voice_combo, 1)
        voice_hdr.addWidget(self.vg_fetch_voices_btn)
        voice_hdr.addWidget(voice_hint)
        vg_l.addLayout(voice_hdr)

        vg_l.addWidget(self._settings_sep())

        # ── Auto-TTS ──────────────────────────────────────────────────
        auto_row = QHBoxLayout()
        self.vg_auto_chk = QCheckBox("Enable TTS by default when opening Chat tab")
        self.vg_auto_chk.setStyleSheet("color:#94a3b8; font-size:11px;")
        self.vg_auto_chk.setChecked(bool(self._settings.get("chat_tts_auto")))
        auto_row.addWidget(self.vg_auto_chk)
        auto_row.addStretch(1)
        vg_l.addLayout(auto_row)

        il.addWidget(vg_box)
        il.addStretch(1)

        scroll.setWidget(inner)
        root.addWidget(scroll)

        self._vg_test_thread: QThread | None = None
        return container

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _settings_input_style() -> str:
        return (
            "QLineEdit { background:#111827; border:1px solid #1f2937; border-radius:6px; "
            "color:#e2e8f0; font-size:12px; padding:5px 10px; }"
            "QLineEdit:focus { border-color:#3b82f6; }"
        )

    @staticmethod
    def _settings_sep() -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color:#1e2533;")
        return sep

    def _settings_update_url_preview(self):
        base = self.vg_url_input.text().rstrip("/")
        ep   = self.vg_endpoint_input.text()
        if ep and not ep.startswith("/"):
            ep = "/" + ep
        self.vg_full_url_lbl.setText(base + ep)

    def _settings_update_payload_preview(self):
        key = self.vg_payload_key_input.text().strip() or "text"
        self.vg_payload_preview.setText(
            f'  POST body: {{"{key}": "<assistant reply>"}}'
        )

    def _settings_vg_toggled(self, checked: bool):
        self.vg_status_dot.setStyleSheet(
            f"color:{'#22c55e' if checked else '#374151'}; font-size:18px;")

    def _settings_save_vg(self):
        self._settings.set_many({
            "vg_enabled":     self.vg_enabled_chk.isChecked(),
            "vg_url":         self.vg_url_input.text().strip(),
            "vg_endpoint":    self.vg_endpoint_input.text().strip(),
            "vg_method":      self.vg_method_combo.currentText(),
            "vg_payload_key": self.vg_payload_key_input.text().strip() or "text",
            "vg_voice":       self.vg_voice_combo.currentText().strip(),
            "vg_test_phrase": self.vg_test_phrase_input.text().strip(),
            "chat_tts_auto":  self.vg_auto_chk.isChecked(),
        })
        if hasattr(self, "chat_tts_chk"):
            self.chat_tts_chk.setChecked(self.vg_enabled_chk.isChecked())
        self._set_status("Voice Gateway settings saved.")
        log.info("SettingsMixin: VG settings saved")

    def _settings_fetch_voices(self):
        """GET /engines and populate voice combo from Kokoro voices list."""
        base_url = self.vg_url_input.text().strip().rstrip("/")
        self.vg_fetch_voices_btn.setEnabled(False)
        self.vg_log.append(f"\nFetching voices from {base_url}/engines …")

        class FetchThread(QThread):
            result = pyqtSignal(list, str)  # voices, error
            def __init__(self, url):
                super().__init__()
                self.url = url
            def run(self):
                import requests as _req
                try:
                    r = _req.get(self.url, timeout=6)
                    r.raise_for_status()
                    data = r.json()
                    voices = []
                    # Parse /engines response — look for kokoro voices array
                    for engine_name, engine_data in data.items():
                        if isinstance(engine_data, dict):
                            for key in ("voices", "available_voices", "speakers"):
                                if key in engine_data:
                                    v = engine_data[key]
                                    if isinstance(v, list):
                                        voices.extend(str(x) for x in v)
                    self.result.emit(voices, "")
                except Exception as e:
                    self.result.emit([], str(e))

        def on_result(voices, err):
            self.vg_fetch_voices_btn.setEnabled(True)
            if err:
                self.vg_log.append(f"  ✗ {err}")
                self.vg_log.append("  (Using built-in Kokoro voice list)")
                return
            if not voices:
                self.vg_log.append("  No voices found in /engines response.")
                self.vg_log.append("  (Using built-in Kokoro voice list)")
                return
            current = self.vg_voice_combo.currentText()
            self.vg_voice_combo.clear()
            self.vg_voice_combo.addItem("")
            for v in voices:
                self.vg_voice_combo.addItem(v)
            if current in voices:
                self.vg_voice_combo.setCurrentText(current)
            self.vg_log.append(f"  ✓ Loaded {len(voices)} voices")

        t = FetchThread(f"{base_url}/engines")
        t.result.connect(on_result)
        t.finished.connect(t.deleteLater)
        self._fetch_thread = t
        t.start()

    def _settings_test_vg(self):
        if self._vg_test_thread and self._vg_test_thread.isRunning():
            self.vg_log.append("⚠ Test already running…")
            return

        self._settings_save_vg()

        url    = self._settings.vg_full_url
        method = self.vg_method_combo.currentText()
        key    = self.vg_payload_key_input.text().strip() or "text"
        phrase = self.vg_test_phrase_input.text().strip()

        self.vg_log.append(f"\n── Test {method} ──────────────────────────")
        self.vg_test_btn.setEnabled(False)

        thread = VGTestThread(url, method, key, phrase)
        thread.log_line.connect(self.vg_log.append)
        thread.done.connect(self._settings_test_done)
        thread.finished.connect(lambda: setattr(self, "_vg_test_thread", None))
        thread.finished.connect(thread.deleteLater)
        self._vg_test_thread = thread
        thread.start()

    def _settings_play_audio_test(self):
        """Full end-to-end: download WAV + play via winsound. Same as Chat TTS."""
        self._settings_save_vg()
        url    = self._settings.vg_full_url
        method = self.vg_method_combo.currentText()
        key    = self.vg_payload_key_input.text().strip() or "text"
        voice  = self.vg_voice_combo.currentText().strip()
        phrase = self.vg_test_phrase_input.text().strip()

        self.vg_log.append(f"\n── Audio playback test ──────────────────────")
        self.vg_log.append(f"   URL    : {url}")
        voice_label = repr(voice) if voice else "(VG default)"
        self.vg_log.append(f"   voice  : {voice_label}")
        self.vg_log.append(f"   phrase : {phrase!r}")
        self.vg_play_btn.setEnabled(False)

        self._play_thread = PlayAudioThread(url, method, key, phrase, voice=voice)
        self._play_thread.log_line.connect(self.vg_log.append)
        self._play_thread.finished.connect(
            lambda: self.vg_play_btn.setEnabled(True))
        self._play_thread.finished.connect(self._play_thread.deleteLater)
        self._play_thread.start()

    def _settings_test_done(self, ok: bool):
        self.vg_test_btn.setEnabled(True)
        self.vg_status_dot.setStyleSheet(
            f"color:{'#22c55e' if ok else '#f87171'}; font-size:18px;")
        # Reset dot colour after 5 s
        QTimer.singleShot(5000, lambda: self.vg_status_dot.setStyleSheet(
            f"color:{'#22c55e' if self.vg_enabled_chk.isChecked() else '#374151'}; font-size:18px;"))
