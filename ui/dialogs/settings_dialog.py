"""Settings / Preferences dialog."""
from __future__ import annotations

import os
import platform
import re
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from data.patch_db import INSTRUMENT_LABELS, PAPER_LABELS, PAPER_SIZES
from ui.tooltip_button import TooltipButton
from ui.widgets import make_browse_button

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

_ARGYLL_DOWNLOAD_PAGE = "https://www.argyllcms.com/downloaddev.html"
_ARGYLL_BASE_URL      = "https://www.argyllcms.com/"


class SettingsDialog(QDialog):
    def __init__(self, settings: "AppSettings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("ChromIQ Preferences")
        self.setMinimumWidth(540)
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )
        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 16, 20, 16)

        # ---- ArgyllCMS ----
        argyll_grp = QGroupBox("ArgyllCMS Binaries", self)
        ag = QVBoxLayout(argyll_grp)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Binary path:", self))
        self._argyll_edit = QLineEdit(self)
        path_row.addWidget(self._argyll_edit, stretch=1)
        browse_btn = make_browse_button(self, "Select ArgyllCMS bin folder")
        browse_btn.clicked.connect(self._browse_argyll)
        path_row.addWidget(browse_btn)
        path_row.addWidget(TooltipButton(
            "ArgyllCMS Binary Path",
            "Directory containing targen, printtarg, chartread, and colprof.\n"
            "Default: /Applications/Argyll/bin\n"
            "You can download the latest version from argyllcms.com.",
            self,
        ))
        ag.addLayout(path_row)

        btn_row = QHBoxLayout()
        test_btn = QPushButton("Test binaries", self)
        test_btn.clicked.connect(self._test_argyll)
        dl_btn = QPushButton("Download latest ArgyllCMS…", self)
        dl_btn.clicked.connect(self._open_argyll_download)
        btn_row.addWidget(test_btn)
        btn_row.addWidget(dl_btn)
        btn_row.addStretch()
        ag.addLayout(btn_row)

        self._argyll_status = QLabel("", self)
        self._argyll_status.setWordWrap(True)
        ag.addWidget(self._argyll_status)

        layout.addWidget(argyll_grp)

        # ---- Preferences ----
        pref_grp = QGroupBox("Defaults", self)
        pf = QFormLayout(pref_grp)

        self._instr_combo = QComboBox(self)
        for code, label in INSTRUMENT_LABELS.items():
            self._instr_combo.addItem(label, code)
        pf.addRow("Preferred instrument:", self._instr_combo)

        self._paper_combo = QComboBox(self)
        for size in PAPER_SIZES:
            self._paper_combo.addItem(PAPER_LABELS.get(size, size), size)
        pf.addRow("Preferred paper size:", self._paper_combo)

        layout.addWidget(pref_grp)

        # ---- Output folder ----
        folder_grp = QGroupBox("Output Folder", self)
        fl = QVBoxLayout(folder_grp)

        folder_lbl = QLabel(
            "Default output folder (leave blank to use ~/ChromIQ/):", self
        )
        folder_lbl.setWordWrap(True)
        fl.addWidget(folder_lbl)

        folder_row = QHBoxLayout()
        self._folder_edit = QLineEdit(self)
        self._folder_edit.setPlaceholderText("~/ChromIQ/  (default)")
        folder_row.addWidget(self._folder_edit, stretch=1)
        folder_browse = make_browse_button(self, "Select output folder")
        folder_browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(folder_browse)
        fl.addLayout(folder_row)

        layout.addWidget(folder_grp)

        # ---- Bottom row: Restore Defaults left, Cancel/OK right ----
        bottom_row = QHBoxLayout()
        reset_btn = QPushButton("Restore Factory Defaults", self)
        reset_btn.setObjectName("danger")
        reset_btn.clicked.connect(self._restore_defaults)
        bottom_row.addWidget(reset_btn)
        bottom_row.addStretch()

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        bb.accepted.connect(self._save_and_close)
        bb.rejected.connect(self.reject)
        bottom_row.addWidget(bb)
        layout.addLayout(bottom_row)

    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        s = self._settings
        self._argyll_edit.setText(s.get("argyll_bin_path", "/Applications/Argyll/bin"))

        instr = s.get("preferred_instrument", "i1")
        idx = self._instr_combo.findData(instr)
        if idx >= 0:
            self._instr_combo.setCurrentIndex(idx)

        paper = s.get("preferred_paper_size", "A4")
        idx = self._paper_combo.findData(paper)
        if idx >= 0:
            self._paper_combo.setCurrentIndex(idx)

        self._folder_edit.setText(s.get("custom_output_path", ""))

    def _save_and_close(self) -> None:
        s = self._settings
        s.set("argyll_bin_path",       self._argyll_edit.text().strip())
        s.set("preferred_instrument",  self._instr_combo.currentData() or "i1")
        s.set("preferred_paper_size",  self._paper_combo.currentData() or "A4")
        s.set("custom_output_path",    self._folder_edit.text().strip())
        log.info("Settings saved")
        self.accept()

    def _browse_argyll(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Select ArgyllCMS bin directory",
            self._argyll_edit.text() or "/Applications",
        )
        if d:
            self._argyll_edit.setText(d)

    def _browse_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Select output folder",
            self._folder_edit.text() or str(Path.home()),
        )
        if d:
            self._folder_edit.setText(d)

    def _test_argyll(self) -> None:
        bin_dir = Path(self._argyll_edit.text().strip())
        results = []
        for tool in ("targen", "printtarg", "chartread", "colprof"):
            p = bin_dir / tool
            if tool == "chartread":
                # chartread probes USB hardware even with -?, causing a hang.
                # Existence + executable check is sufficient here.
                if p.exists() and os.access(str(p), os.X_OK):
                    results.append(f"✓ {tool}")
                else:
                    results.append(f"✗ {tool} (not found)")
                continue
            if p.exists():
                try:
                    subprocess.run(
                        [str(p), "-?"], capture_output=True, timeout=5,
                    )
                    results.append(f"✓ {tool}")
                except Exception:
                    results.append(f"✗ {tool} (error)")
            else:
                results.append(f"✗ {tool} (not found)")
        msg = "  ".join(results)
        all_ok = all(r.startswith("✓") for r in results)
        self._argyll_status.setStyleSheet(
            "color: #4caf50;" if all_ok else "color: #ff9800;"
        )
        self._argyll_status.setText(msg)
        log.info("ArgyllCMS test: %s", msg)

    def _open_argyll_download(self) -> None:
        arch = platform.machine()
        pattern = (
            r"Argyll_V[\d.]+_macos_arm64_bin\.tar\.gz"
            if arch == "arm64"
            else r"Argyll_V[\d.]+_osx64_bin\.tar\.gz"
        )
        self._argyll_status.setStyleSheet("")
        self._argyll_status.setText("Finding latest ArgyllCMS for your platform…")

        def _fetch() -> None:
            url = _ARGYLL_DOWNLOAD_PAGE
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:
                    html = resp.read().decode("utf-8", errors="replace")
                m = re.search(pattern, html)
                if m:
                    direct = _ARGYLL_BASE_URL + m.group(0)
                    QTimer.singleShot(0, lambda: _open(direct, "Opening download…"))
                else:
                    QTimer.singleShot(0, lambda: _open(url, "Version not detected — opening download page."))
            except Exception:
                QTimer.singleShot(0, lambda: _open(url, "Could not reach argyllcms.com — opening download page."))

        def _open(target_url: str, msg: str) -> None:
            self._argyll_status.setText(msg)
            QDesktopServices.openUrl(QUrl(target_url))

        threading.Thread(target=_fetch, daemon=True).start()

    def _restore_defaults(self) -> None:
        self._settings.reset_to_defaults()
        self._load_settings()
        log.info("Factory defaults restored")
