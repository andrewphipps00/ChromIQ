"""Tab 2: Print Chart."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.logger import get_logger
from ui.tiff_preview import TiffPreview
from ui.tooltip_button import TooltipButton
from workflow.cups_printer import CupsRawPrinter
from workflow.print_manager import PrintModule

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)


class TabPrint(QWidget):
    """Step 2: print the test chart via CUPS."""

    def __init__(
        self,
        settings: "AppSettings",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._module   = PrintModule()
        self._printer  = CupsRawPrinter()
        self._tiff_pages: list[Path] = []

        self._build_ui()

    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # ---- Left controls ----
        left = QWidget(self)
        left.setMaximumWidth(560)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 12, 16, 12)
        ll.setSpacing(10)

        # Printer selection
        printer_grp = QGroupBox("Printer", left)
        pg = QVBoxLayout(printer_grp)

        pr_row = QHBoxLayout()
        pr_row.addWidget(QLabel("Printer:", left))
        self._printer_combo = QComboBox(left)
        pr_row.addWidget(self._printer_combo, stretch=1)

        refresh_btn = QPushButton(left)
        refresh_btn.setIcon(
            QApplication.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        refresh_btn.setFixedSize(34, 34)
        refresh_btn.setStyleSheet("QPushButton { padding: 0; min-height: 0; }")
        refresh_btn.setToolTip("Refresh printer list")
        refresh_btn.clicked.connect(self._refresh_printers)
        pr_row.addWidget(refresh_btn)

        pr_row.addWidget(TooltipButton(
            "Printer Selection",
            "Select the printer to send the chart to.  Only printers installed in\n"
            "the system CUPS print queue are listed.\n\n"
            "The TIFF is sent directly via lp with the options you configure below.\n"
            "To suppress colour management, select 'No Color Adjustment' (or equivalent)\n"
            "in the Print Options section.",
            left,
        ))
        pg.addLayout(pr_row)
        ll.addWidget(printer_grp)

        # Print options — dynamically built from CUPS lpoptions output
        self._opts_grp = QGroupBox("Print Options", left)
        self._opts_layout = QVBoxLayout(self._opts_grp)
        self._option_combos: dict[str, QComboBox] = {}
        self._opts_layout.addWidget(
            QLabel("Select a printer to see its options.", left)
        )
        ll.addWidget(self._opts_grp)

        self._printer_combo.currentIndexChanged.connect(self._on_printer_changed)

        # Warning label
        warn = QLabel(
            "⚠  Verify that all print settings above match the media you are printing on.\n"
            "   Wrong media type or quality settings will cause incorrect ink laydown\n"
            "   and invalid colour measurements. Allow the print to dry fully before\n"
            "   measuring (at least 15–30 min for pigment inks).",
            left,
        )
        warn.setObjectName("warning")
        warn.setWordWrap(True)
        ll.addWidget(warn)

        # Load TIFFs button
        load_btn = QPushButton("Load existing TIFF files…", left)
        load_btn.clicked.connect(self._on_load_tiffs)
        ll.addWidget(load_btn)

        # Print buttons
        btn_row = QHBoxLayout()
        self._print_page_btn = QPushButton("Print Current Page", left)
        self._print_page_btn.setObjectName("primary")
        self._print_page_btn.clicked.connect(self._on_print_current)

        self._print_all_btn = QPushButton("Print All Pages", left)
        self._print_all_btn.clicked.connect(self._on_print_all)

        self._save_defaults_btn = QPushButton("Save as Defaults", left)
        self._save_defaults_btn.clicked.connect(self._on_save_defaults)

        btn_row.addWidget(self._print_page_btn)
        btn_row.addWidget(self._print_all_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._save_defaults_btn)
        ll.addLayout(btn_row)

        # Status
        self._status_lbl = QLabel("", left)
        self._status_lbl.setWordWrap(True)
        ll.addWidget(self._status_lbl)

        ll.addStretch()
        splitter.addWidget(left)

        # ---- Right preview ----
        right = QWidget(self)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel("Print Preview", right)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet("color: #909090; font-size: 11px; padding: 4px;")
        rl.addWidget(lbl)
        self._preview = TiffPreview(right)
        rl.addWidget(self._preview, stretch=1)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        # Initial state
        self._set_print_buttons_enabled(False)
        self._refresh_printers()
        self._restore_defaults()

    # ------------------------------------------------------------------

    def load_tiffs(self, paths: list[Path]) -> None:
        """Called by main window after chart generation."""
        self._tiff_pages = paths
        self._preview.load_tiff(paths)
        self._set_print_buttons_enabled(bool(paths))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh_printers(self) -> None:
        self._printer_combo.blockSignals(True)
        self._printer_combo.clear()
        printers = self._module.detect_printers()
        for p in printers:
            self._printer_combo.addItem(p, p)
        if not printers:
            self._printer_combo.addItem("No printers found", "")
        self._printer_combo.blockSignals(False)

        last = self._settings.get("last_printer", "")
        if last:
            idx = self._printer_combo.findData(last)
            if idx >= 0:
                self._printer_combo.setCurrentIndex(idx)
        self._on_printer_changed()

    def _on_printer_changed(self) -> None:
        printer = self._printer_combo.currentData() or ""
        self._rebuild_option_rows(printer)

    def _rebuild_option_rows(self, printer: str) -> None:
        while self._opts_layout.count():
            item = self._opts_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._option_combos.clear()

        if not printer:
            self._opts_layout.addWidget(QLabel("Select a printer to see its options.", self))
            return

        opts = self._module.query_options(printer)
        if not opts:
            self._opts_layout.addWidget(
                QLabel("No configurable options detected for this printer.", self)
            )
            return

        saved_printer_opts: dict[str, str] = {}
        raw_saved = self._settings.get(f"print_opts_{printer}", "")
        if raw_saved:
            for pair in str(raw_saved).split("|"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    saved_printer_opts[k] = v

        for opt_name, (label, value_pairs) in opts.items():
            row = QHBoxLayout()
            lbl = QLabel(f"{label}:", self)
            lbl.setMinimumWidth(160)
            row.addWidget(lbl)
            combo = QComboBox(self)
            combo.setMaxVisibleItems(12)
            combo.addItem("(not set)", "")
            for display, raw_val in value_pairs:
                combo.addItem(display, raw_val)
            saved_val = saved_printer_opts.get(opt_name, "")
            if saved_val:
                idx = combo.findData(saved_val)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            row.addWidget(combo, stretch=1)
            self._opts_layout.addLayout(row)
            self._option_combos[opt_name] = combo

    def _on_load_tiffs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Load TIFF files", str(Path.home()),
            "TIFF files (*.tif *.tiff)",
        )
        if paths:
            self.load_tiffs([Path(p) for p in sorted(paths)])

    def _on_print_current(self) -> None:
        if not self._tiff_pages:
            return
        idx = self._preview._current
        page = self._tiff_pages[min(idx, len(self._tiff_pages) - 1)]
        self._send_page(page)

    def _on_print_all(self) -> None:
        for page in self._tiff_pages:
            self._send_page(page)

    def _send_page(self, tiff_path: Path) -> None:
        printer = self._printer_combo.currentData() or ""
        if not printer:
            self._status_lbl.setObjectName("error")
            self._status_lbl.setText("No printer selected.")
            self._status_lbl.setStyleSheet("")
            return

        selected_opts = {
            k: (combo.currentData() or "")
            for k, combo in self._option_combos.items()
        }
        config = self._module.build_config(printer=printer, options=selected_opts)
        self._status_lbl.setText(f"Sending {tiff_path.name} to {printer}…")

        self._printer.print_job(
            tiff_path, config,
            on_finish=self._on_print_done,
        )

    def _on_print_done(self, code: int) -> None:
        if code == 0:
            self._status_lbl.setText("Print job submitted successfully.")
        else:
            self._status_lbl.setText(f"Print failed (lp exit code {code}).")

    def _set_print_buttons_enabled(self, enabled: bool) -> None:
        self._print_page_btn.setEnabled(enabled)
        self._print_all_btn.setEnabled(enabled)

    def _on_save_defaults(self) -> None:
        s = self._settings
        printer = self._printer_combo.currentData() or ""
        s.set("last_printer", printer)
        if printer and self._option_combos:
            pairs = "|".join(
                f"{k}={combo.currentData() or ''}"
                for k, combo in self._option_combos.items()
            )
            s.set(f"print_opts_{printer}", pairs)
        self._status_lbl.setText("Print settings saved as defaults.")

    def _restore_defaults(self) -> None:
        pass
