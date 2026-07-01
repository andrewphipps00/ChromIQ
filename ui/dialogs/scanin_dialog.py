"""Tools → "Build scanner profile" — profile a scanner from a printed chart (#98).

Workflow: pick a **measured** ChromIQ chart and a **scan** of the printed chart,
drag the four corners over the patch area (a live grid confirms the fit), and
ChromIQ runs ``scanin`` (manual ``-F`` registration + perspective) to read the
scan against the chart's measured colours, then ``colprof`` to build the scanner
ICC. Multi-page charts: one scan + placement per page; the reads are combined
before profiling.

Needs the chart's ``.cht`` + ``.cie`` (built by "Create scanner target" / the
measure-tab checkbox); this tool builds them on the fly if they're missing but
the chart was measured. Green (measure/scanner family), ⓘ per option,
non-native pickers, readable helper text in both themes.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QCheckBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget)

from core.i18n import tr
from core.logger import get_logger
from ui.dialogs.tools_dialogs import (
    _ToolDialogBase, _initial_dir, _remember_dir, neutral_controls_qss)
from ui.scan_grid_marquee import GridSpec, ScanGridMarquee
from ui.styles import SPEC_GREEN
from ui.theme import resolve_mode
from ui.tooltip_button import TooltipButton
from ui.widgets import NoScrollComboBox, make_browse_button, open_file_dialog
from workflow.profile_builder import ProfileBuilder, ProfileParams
from workflow.scanin_runner import ScaninParams, ScaninRunner
from workflow.scanin_target import (
    ScaninTargetError, build_scanin_target_from_paths, is_engine_geometry)

log = get_logger(__name__)

_TI3_FILTER = "Measured chart (*.ti3);;All files (*)"
_SCAN_FILTER = "Scans (*.tif *.tiff);;All files (*)"


def _chart_base(ti3: Path) -> Path:
    stem = ti3.stem
    if stem.endswith("-verify"):
        stem = stem[: -len("-verify")]
    return ti3.with_name(stem)


class ScannerProfileDialog(_ToolDialogBase):
    TOOL_KEY    = "scanner_profile"
    TITLE       = tr("Build scanner profile")
    EYEBROW     = tr("MEASURE · SCANNER PROFILE")
    ACCENT      = SPEC_GREEN
    RUN_LABEL   = tr("Build scanner profile")
    MIN_WIDTH   = 760

    HELP = tr(
        "Profiles your scanner from a printed ChromIQ chart — no target-chart "
        "purchase needed.\n\n"
        "1. Print and measure a chart as usual, and keep its scanner files "
        "(.cht + .cie) — tick 'Also save scanner-profiling files' after "
        "measuring, or use Tools ▸ Create scanner target.\n"
        "2. Scan the printed chart on the scanner you want to profile, as a "
        "plain RGB TIFF, with the scanner's own auto-correction and colour "
        "management turned OFF.\n"
        "3. Here: pick the measured chart and the scan, drag the four corners "
        "over the patch area until the green grid lines up with the real "
        "patches, and click Build scanner profile.\n\n"
        "ChromIQ compares how your scanner saw each patch against the real "
        "measured colours and writes a scanner ICC profile next to the scan. "
        "Multi-page charts: pick each page's scan and place its grid.\n\n"
        "───────────────\n"
        "Using your scanner profile\n\n"
        "The profile tells any colour-managed program how your scanner sees "
        "colour, so your scans come out accurate instead of dull or colour-cast "
        "— great for digitising prints, artwork and photos so they match the "
        "original.\n\n"
        "Two common ways to use it:\n\n"
        "• In your scanner software (VueScan, SilverFast, Epson Scan, etc.): "
        "set this .icc file as the scanner's input / ICC profile, and choose a "
        "working space such as sRGB or Adobe RGB as the output. New scans are "
        "then corrected automatically.\n\n"
        "• In Photoshop or another editor: scan with correction OFF, open the "
        "scan, then Assign Profile ▸ this scanner profile (so the app knows how "
        "the scanner saw the colours), and Convert to Profile ▸ your working "
        "space (e.g. sRGB or Adobe RGB). The colours now match the original.\n\n"
        "Good to know:\n"
        "• The profile is specific to this scanner and the settings you scanned "
        "with — keep the scanner's brightness / auto-correction OFF, the same as "
        "when you scanned the chart, or the profile won't fit.\n"
        "• It's most accurate for media like the paper you profiled; rescan a "
        "chart on very different paper (e.g. glossy vs. matte) if you switch.\n"
        "• A scanner profile characterises the scanner — it does not sharpen or "
        "retouch; it just makes the colours faithful.")
    DESCRIPTION = tr(
        "Turn a scan of a measured chart into a colour profile for your scanner.")

    def __init__(self, runner, settings, parent: QWidget | None = None) -> None:
        super().__init__(settings, parent)
        self._runner = runner
        self._scanin = ScaninRunner(runner)
        self._profiler = ProfileBuilder(runner)
        self._ti3: Path | None = None
        self._layout: dict | None = None
        self._pages: list[int] = []
        self._page = 0
        self._scans: dict[int, Path] = {}
        self._corners: dict[int, list[tuple[float, float]]] = {}
        self._jobs: list[dict] = []
        light = resolve_mode(settings.get("appearance", "auto")) == "light"
        self._hint = "#4a4a4a" if light else "#b8b8b8"
        self._build_inputs()
        self._run_btn.setObjectName("primary")
        self.setStyleSheet(self.styleSheet() + neutral_controls_qss(SPEC_GREEN))
        self._style_primary_button()
        self._refresh()

    def _style_primary_button(self) -> None:
        c = SPEC_GREEN
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        hover = "#{:02x}{:02x}{:02x}".format(int(r * .86), int(g * .86), int(b * .86))
        light = resolve_mode(self._settings.get("appearance", "auto")) == "light"
        dis_bg, dis_fg = ("#e8e6e1", "#a8a4a0") if light else ("#1e1e1e", "#484848")
        self._run_btn.setStyleSheet(
            f"QPushButton {{ background:{c}; border:1px solid {c}; color:#0a0a0a;"
            f" font-weight:700; }}"
            f"QPushButton:hover {{ background:{hover}; border-color:{hover}; }}"
            f"QPushButton:disabled {{ background:{dis_bg}; border:1px solid {c};"
            f" color:{dis_fg}; }}")

    def _tip(self, title: str, body: str) -> TooltipButton:
        return TooltipButton(title, body, self, min_width=500, color=SPEC_GREEN)

    # ------------------------------------------------------------------ UI
    def _labelled(self, text: str, tip_t: str, tip_b: str):
        h = QHBoxLayout()
        h.addWidget(QLabel(text, self))
        h.addStretch(1)
        h.addWidget(self._tip(tip_t, tip_b), 0, Qt.AlignmentFlag.AlignVCenter)
        return h

    def _hint_label(self, text: str) -> QLabel:
        lbl = QLabel(text, self)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{self._hint}; font-size:12px;")
        return lbl

    def _build_inputs(self) -> None:
        form = self._content

        form.addLayout(self._labelled(
            tr("Measured chart (.ti3):"), tr("Measured chart"),
            tr("The chart you printed and measured, whose printed copy you're "
            "scanning. Pick its .ti3. ChromIQ uses the chart's exact layout + "
            "measured colours to read the scan.")))
        row = QHBoxLayout()
        self._ti3_field = QLineEdit(self)
        self._ti3_field.setReadOnly(True)
        self._ti3_field.setPlaceholderText(tr("Pick the measured chart (.ti3)…"))
        row.addWidget(self._ti3_field, 1)
        b = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        b.clicked.connect(self._pick_chart)
        row.addWidget(b)
        form.addLayout(row)
        self._chart_note = self._hint_label("")
        form.addWidget(self._chart_note)

        # Page selector (multi-page charts only)
        self._page_row = QHBoxLayout()
        self._page_row.addWidget(QLabel(tr("Page:"), self))
        self._page_combo = NoScrollComboBox(self)
        self._page_combo.currentIndexChanged.connect(self._on_page_changed)
        self._page_row.addWidget(self._page_combo)
        self._page_row.addStretch(1)
        self._page_widget = QWidget(self)
        self._page_widget.setLayout(self._page_row)
        self._page_widget.setVisible(False)
        form.addWidget(self._page_widget)

        form.addLayout(self._labelled(
            tr("Scan of the printed chart (TIFF):"), tr("Scan"),
            tr("A scan of the printed chart on the scanner you want to profile, "
            "saved as a plain RGB TIFF. For a multi-page chart, pick each page's "
            "scan under its Page number.")))
        row2 = QHBoxLayout()
        self._scan_field = QLineEdit(self)
        self._scan_field.setReadOnly(True)
        self._scan_field.setPlaceholderText(tr("Pick the scan for this page (TIFF)…"))
        row2.addWidget(self._scan_field, 1)
        b2 = make_browse_button(self, tr("Browse…"), icon="folder_measure")
        b2.clicked.connect(self._pick_scan)
        row2.addWidget(b2)
        form.addLayout(row2)

        self._marquee = ScanGridMarquee(self)
        self._marquee.setMinimumHeight(320)
        form.addWidget(self._marquee)
        form.addWidget(self._hint_label(tr(
            "Drag the four corners onto the printed patch area until the green "
            "grid sits on the real patches. ChromIQ then reads each patch and "
            "builds the profile.")))

        opts = QHBoxLayout()
        self._perspective = QCheckBox(tr("Correct perspective (slightly skewed scan)"), self)
        self._perspective.setChecked(True)
        self._diag = QCheckBox(tr("Save a diagnostic image of what was read"), self)
        opts.addWidget(self._perspective)
        opts.addWidget(self._diag)
        opts.addStretch(1)
        opts.addWidget(self._tip(
            tr("Reading options"),
            tr("Correct perspective compensates for a slightly skewed scan (keep "
            "it on). The diagnostic image saves a copy of the scan with the "
            "patches ChromIQ read drawn on it, so you can check the alignment "
            "if the profile looks off.")), 0, Qt.AlignmentFlag.AlignVCenter)
        form.addLayout(opts)

    # ------------------------------------------------------------------ chart
    def _pick_chart(self) -> None:
        path = open_file_dialog(self, tr("Choose the measured chart"), _TI3_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._set_chart(Path(path))

    def _set_chart(self, ti3: Path) -> None:
        import json
        self._ti3 = ti3
        self._ti3_field.setText(str(ti3))
        _remember_dir(self._settings, self.TOOL_KEY, ti3.parent)
        self._scans.clear()
        self._corners.clear()
        base = _chart_base(ti3)
        channels = base.with_name(base.name + ".channels.json")
        if not is_engine_geometry(channels):
            self._layout = None
            self._pages = []
            self._chart_note.setText(tr(
                "⚠ Not a layout-engine chart — scanner profiling needs a chart "
                "created with ChromIQ's layout engine."))
            self._refresh()
            return
        self._layout = json.loads(channels.read_text())["layout"]
        self._pages = sorted({int(p.get("page", 0)) for p in self._layout["patches"]})
        # Ensure the .cht/.cie exist (build from the measurement if missing).
        try:
            build_scanin_target_from_paths(channels, ti3, base)
        except ScaninTargetError as exc:
            self._chart_note.setText(f"⚠ {exc}")
            self._layout = None
            self._refresh()
            return
        self._chart_note.setText(tr(
            "✓ Ready — {n} patches on {p} page(s)."
        ).format(n=len(self._layout["patches"]), p=len(self._pages)))
        self._page_widget.setVisible(len(self._pages) > 1)
        self._page_combo.blockSignals(True)
        self._page_combo.clear()
        for pg in self._pages:
            self._page_combo.addItem(tr("Page {n}").format(n=pg + 1), pg)
        self._page_combo.blockSignals(False)
        self._page = self._pages[0] if self._pages else 0
        self._load_page_grid()
        self._refresh()

    def _load_page_grid(self) -> None:
        if self._layout is None:
            return
        pg = self._page
        patches = [p for p in self._layout["patches"] if int(p.get("page", 0)) == pg]
        self._marquee.set_grid(GridSpec.from_patches(patches))
        scan = self._scans.get(pg)
        self._scan_field.setText(str(scan) if scan else "")
        if scan and scan.is_file():
            self._marquee.set_image(QImage(str(scan)))
            # Restore this page's saved corner placement (set_image reset it).
            if pg in self._corners:
                self._marquee.set_corners(self._corners[pg])
        else:
            self._marquee.set_image(QImage())

    def _on_page_changed(self, idx: int) -> None:
        self._capture_current_corners()
        if 0 <= idx < len(self._pages):
            self._page = self._pages[idx]
            self._load_page_grid()

    def _capture_current_corners(self) -> None:
        if self._marquee.has_placement():
            self._corners[self._page] = self._marquee.corners_image_px()

    # ------------------------------------------------------------------ scan
    def _pick_scan(self) -> None:
        if self._layout is None:
            return
        path = open_file_dialog(self, tr("Choose the scan"), _SCAN_FILTER,
                                start_dir=str(_initial_dir(self._settings, self.TOOL_KEY)))
        if not path:
            return
        self._scans[self._page] = Path(path)
        self._scan_field.setText(path)
        _remember_dir(self._settings, self.TOOL_KEY, Path(path).parent)
        self._marquee.set_image(QImage(path))
        self._refresh()

    # ------------------------------------------------------------------ run
    def _can_run(self) -> bool:
        return self._layout is not None and bool(self._pages) and all(
            self._scans.get(pg) is not None for pg in self._pages)

    def _execute(self) -> None:
        self._capture_current_corners()
        self._log.clear()
        base = _chart_base(self._ti3)
        single = len(self._pages) == 1
        page_ti3s: list[Path] = []
        self._jobs = []
        for pg in self._pages:
            scan = self._scans[pg]
            cht = (base.with_suffix(".cht") if single
                   else base.parent / f"{base.name}_{pg + 1:02d}.cht")
            cie = base.with_suffix(".cie")
            corners = self._corners.get(pg)
            diag = (scan.with_name(scan.stem + "-diag.tif")
                    if self._diag.isChecked() else None)
            params = ScaninParams(scan, cht, cie, corners=corners,
                                  perspective=self._perspective.isChecked(), diag=diag)
            page_ti3s.append(params.out_ti3)
            self._jobs.append({"kind": "scanin", "params": params,
                               "label": tr("Reading page {n} from the scan…").format(n=pg + 1)})
        self._jobs.append({"kind": "colprof", "ti3s": page_ti3s, "base": base})
        self._run_job(0)

    def _run_job(self, i: int) -> None:
        if i >= len(self._jobs):
            return
        job = self._jobs[i]
        if job["kind"] == "scanin":
            self._log.appendPlainText(job["label"])

            def _done(code: int, i=i, job=job) -> None:
                fail = self._scanin.primary_failure()
                if code != 0 or fail is not None or not job["params"].out_ti3.exists():
                    msg = fail[1] if fail else tr("ScanIn couldn't read this page.")
                    self._log.appendPlainText(f"[ERROR] {msg}")
                    self._finish(False)
                    return
                self._run_job(i + 1)

            self._scanin.run(job["params"], on_line=self._log_line, on_finish=_done)
        else:
            self._build_profile(job["ti3s"], job["base"])

    def _build_profile(self, page_ti3s: list[Path], base: Path) -> None:
        # Combine multi-page reads into one .ti3, then colprof → scanner ICC.
        try:
            combined = self._combine_ti3(page_ti3s, base)
        except OSError as exc:
            self._log.appendPlainText(f"[ERROR] {exc}")
            self._finish(False)
            return
        self._log.appendPlainText(tr("Building the scanner profile…"))
        params = ProfileParams(
            ti3_path=combined, algorithm="s", quality="m",
            description=f"{base.name} scanner", manufacturer="ChromIQ",
            model=f"{base.name} scanner")

        def _done(code: int) -> None:
            icc = combined.with_suffix(".icc")
            if code != 0 or not icc.exists():
                fail = self._profiler.primary_failure()
                self._log.appendPlainText(
                    f"[ERROR] {fail[1] if fail else tr('Building the profile failed — see messages above.')}")
                self._finish(False)
                return
            self._log.appendPlainText(tr("[OK] Scanner profile saved: {p}").format(p=icc))
            self._log.appendPlainText(tr(
                "Install it as your scanner's input profile. Use the diagnostic "
                "image (if you saved one) to check the patches were read correctly."))
            self._finish(True)

        self._profiler.build(params, on_line=self._log_line, on_finish=_done)

    def _combine_ti3(self, page_ti3s: list[Path], base: Path) -> Path:
        """Single page → use it directly; multi-page → concatenate the data rows
        into one scanner ``.ti3`` for colprof (same DEVICE_CLASS/format)."""
        if len(page_ti3s) == 1:
            return page_ti3s[0]
        # "-scanner" so the combined read / built profile can never collide with
        # the chart's own <stem>.ti3 / <stem>.icc (the printer profile).
        merged = base.with_name(base.name + "-scanner.ti3")
        header, rows = None, []
        for tp in page_ti3s:
            text = tp.read_text()
            lines = text.splitlines()
            ds = next(i for i, l in enumerate(lines) if l.strip() == "BEGIN_DATA")
            de = next(i for i, l in enumerate(lines) if l.strip() == "END_DATA")
            if header is None:
                header = lines[:ds + 1]
            rows += [l for l in lines[ds + 1:de] if l.strip()]
        # renumber SET count
        out = []
        for l in header:
            if l.strip().startswith("NUMBER_OF_SETS"):
                out.append(f"NUMBER_OF_SETS {len(rows)}")
            else:
                out.append(l)
        out += rows + ["END_DATA", ""]
        merged.write_text("\n".join(out))
        return merged

    def _log_line(self, line: str) -> None:
        text = line.rstrip()
        if text and not text.endswith("%"):
            self._log.appendPlainText(text)
            self._log.ensureCursorVisible()
