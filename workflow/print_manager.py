"""Printer detection and option querying via CUPS."""
from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Optional

from core.logger import get_logger
from workflow.cups_printer import PrintConfig

log = get_logger(__name__)


class PrintModule:
    """Detect installed printers and query their supported options."""

    def detect_printers(self) -> list[str]:
        """Return filtered list of non-AirPrint printer names from CUPS."""
        try:
            import cups
            conn = cups.Connection()
            result: list[str] = []
            for name, attrs in conn.getPrinters().items():
                model = attrs.get("printer-make-and-model", "")
                if "airprint" in model.lower() or "airprint" in name.lower():
                    continue
                result.append(name)
            result.sort()
            log.debug("Detected printers: %s", result)
            return result
        except Exception as exc:
            log.warning("detect_printers error: %s", exc)
            return []

    # For each of the 4 print setting categories:
    # (exact_cups_names_to_try_first, label_keywords_as_fallback)
    # IMPORTANT: exact_names is a tuple (not a set) so iteration order is
    # deterministic across Python invocations (sets are hash-randomised).
    # Vendor-specific names come first so they are preferred when both a
    # vendor name and a generic name exist (e.g. Epson has both EPIJ_Medi
    # and MediaType — we want EPIJ_Medi to win for correct rule matching).
    _CATEGORY_SEARCHES: list[tuple[tuple[str, ...], list[str]]] = [
        (
            ("EPIJ_FdSo", "CNPaperSource", "InputSlot"),
            ["input slot", "paper source", "feed source", "tray", "cassette"],
        ),
        (
            ("EPIJ_Size", "media", "PageSize"),
            ["paper size", "media size", "page size"],
        ),
        (
            ("EPIJ_Medi", "CNMediaType", "BrMediaType", "media-type", "MediaType"),
            ["media type", "paper type", "media kind"],
        ),
        (
            (
                "EPIJ_Qual", "CNQuality", "BrQuality",
                "OutputMode", "HPOutputMode", "PrintoutMode",
                "cupsPrintQuality", "print-quality",
            ),
            ["print quality", "output quality", "quality mode", "printout mode"],
        ),
    ]

    # IPP standard integer quality codes used by driverless/IPP-Everywhere printers.
    _IPP_QUALITY_LABELS: dict[str, str] = {"3": "Draft", "4": "Normal", "5": "High"}

    # All option names that represent print quality (used by filtering logic).
    _QUALITY_OPT_NAMES: frozenset[str] = frozenset({
        "print-quality", "EPIJ_Qual", "CNQuality", "BrQuality",
        "OutputMode", "HPOutputMode", "PrintoutMode", "cupsPrintQuality",
    })

    # Exact allowlists for Epson EPIJ printers, keyed by raw EPIJ_Medi value.
    # Derived from the actual combinations the Epson ET-8550 driver presents in its
    # print dialog. The raw values are stable and language-independent.
    # EPIJ_Qual raw values: 302=Economy, 303=Normal, 308=Draft, 304=Fine,
    #                       305=Quality, 306=High Quality, 307=Best Quality
    _EPSON_QUALITY_RULES: dict[str, frozenset[str]] = {
        "0":   frozenset({"302", "303", "304", "307"}),  # Plain paper
        "142": frozenset({"302", "303", "304", "307"}),  # Letterhead
        "2":   frozenset({"305", "306"}),               # Epson Photo Quality Ink Jet
        "12":  frozenset({"305", "306"}),               # Epson Matte
        "92":  frozenset({"305", "306", "307"}),        # Epson Ultra Glossy
        "13":  frozenset({"308", "305", "306"}),        # Epson Premium Glossy
        "15":  frozenset({"308", "305", "306"}),        # Epson Premium Semigloss
        "145": frozenset({"308", "305", "306"}),        # Photo Paper Glossy
        "75":  frozenset({"305"}),                      # Epson Photo Stickers
        "93":  frozenset({"303", "304"}),               # Envelope
        "187": frozenset({"306"}),                      # Thin paper
        "159": frozenset({"306"}),                      # Thick paper 1 (0.8 mm)
        "160": frozenset({"306"}),                      # Thick paper 2 (1.3 mm)
    }

    def query_options(self, printer: str) -> dict[str, tuple[str, list[tuple[str, str]]]]:
        """Return up to 4 CUPS options covering the standard print settings.

        Checks exact CUPS option names first, then falls back to label-keyword
        matching — so both standard CUPS drivers and vendor-specific drivers
        (e.g. EPSON EPIJ_*) are handled correctly.
        Values are looked up in the printer's PPD file so human-readable names
        are shown instead of raw codes.
        Returns dict: CUPS_option_name → (category_label, [(display_label, raw_cups_value), ...]).
        """
        result: dict[str, tuple[str, list[tuple[str, str]]]] = {}
        try:
            r = subprocess.run(
                ["lpoptions", "-p", printer, "-l"],
                capture_output=True, text=True, timeout=15,
            )
            # Parse all options: opt_name → (label, [raw_value, ...])
            all_opts: dict[str, tuple[str, list[str]]] = {}
            for line in r.stdout.splitlines():
                if ":" not in line:
                    continue
                key_part, vals_part = line.split(":", 1)
                key_part = key_part.strip()
                opt_name  = key_part.split("/")[0].strip()
                opt_label = key_part.split("/")[1].strip() if "/" in key_part else opt_name
                vals = [v.lstrip("*") for v in vals_part.split() if v.strip()]
                if len(vals) >= 2:
                    all_opts[opt_name] = (opt_label, vals)

            ppd_labels = self._parse_ppd_labels(printer)

            # For each category, pick the first matching option
            for exact_names, label_keywords in self._CATEGORY_SEARCHES:
                matched_name: str | None = None
                for name in exact_names:
                    if name in all_opts:
                        matched_name = name
                        break
                if matched_name is None:
                    for opt_name, (opt_label, _) in all_opts.items():
                        if any(kw in opt_label.lower() for kw in label_keywords):
                            matched_name = opt_name
                            break

                if matched_name is not None:
                    opt_label, raw_vals = all_opts[matched_name]
                    val_labels = ppd_labels.get(matched_name, {})
                    # For quality options with no PPD label, map IPP integers to names.
                    if matched_name in self._QUALITY_OPT_NAMES:
                        for v in raw_vals:
                            if v not in val_labels and v in self._IPP_QUALITY_LABELS:
                                val_labels[v] = self._IPP_QUALITY_LABELS[v]
                    # Pair each raw CUPS value with its human-readable display label
                    pairs = [(val_labels.get(v, v), v) for v in raw_vals]
                    result[matched_name] = (opt_label, pairs)

        except Exception as exc:
            log.warning("query_options(%s) error: %s", printer, exc)

        log.debug("Options for %s: %d configurable options", printer, len(result))
        return result

    def get_valid_option_values(
        self,
        printer: str,
        preceding_opts: dict[str, str],
        preceding_labels: dict[str, str],
        opt_name: str,
        all_values: list[str],
        all_pairs: list[tuple[str, str]],
    ) -> list[str]:
        """Return the subset of *all_values* that don't conflict with preceding selections.

        Priority order:
        1. Epson EPIJ exact allowlists (derived from the actual driver dialog).
        2. PPD UIConstraints via cups.PPD (for printers that define them).
        3. General keyword heuristics as a conservative fallback.
        """
        # 1. Epson EPIJ: exact allowlists keyed by raw media value.
        #    Check all possible media-type key names because query_options() may
        #    have matched "MediaType" instead of "EPIJ_Medi" (both exist on Epson).
        if opt_name == "EPIJ_Qual":
            media_raw = next(
                (v for k, v in preceding_opts.items()
                 if k in {"EPIJ_Medi", "MediaType", "media-type",
                          "CNMediaType", "BrMediaType"}),
                "",
            )
            if media_raw in self._EPSON_QUALITY_RULES:
                allowed = self._EPSON_QUALITY_RULES[media_raw]
                filtered = [rv for _, rv in all_pairs if rv in allowed]
                return filtered or [rv for _, rv in all_pairs]

        # 2. PPD UIConstraints (most printers define none for quality/media, but some do).
        ppd_path = self._find_ppd_path(printer)
        if ppd_path:
            ppd_filtered = self._filter_via_ppd(ppd_path, preceding_opts, opt_name, all_values)
            # Only trust PPD filter if it actually removed something.
            if len(ppd_filtered) < len(all_values):
                return ppd_filtered

        # 3. General heuristics for all other drivers.
        return self._filter_via_rules(preceding_labels, opt_name, all_pairs)

    @staticmethod
    def _find_ppd_path(printer: str) -> str | None:
        for base in ("/etc/cups/ppd", "/private/etc/cups/ppd"):
            p = pathlib.Path(f"{base}/{printer}.ppd")
            if p.exists():
                return str(p)
        return None

    @staticmethod
    def _filter_via_ppd(
        ppd_path: str,
        preceding_opts: dict[str, str],
        opt_name: str,
        all_values: list[str],
    ) -> list[str]:
        try:
            import cups
            ppd = cups.PPD(ppd_path)
            valid: list[str] = []
            for v in all_values:
                ppd.markDefaults()
                for k, pv in preceding_opts.items():
                    if pv:
                        ppd.markOption(k, pv)
                ppd.markOption(opt_name, v)
                if ppd.conflicts() == 0:
                    valid.append(v)
            return valid or all_values  # never block user completely
        except Exception as exc:
            log.warning("PPD constraint filter failed: %s", exc)
            return all_values

    @classmethod
    def _filter_via_rules(
        cls,
        preceding_labels: dict[str, str],
        opt_name: str,
        all_pairs: list[tuple[str, str]],
    ) -> list[str]:
        """Conservative keyword heuristics for non-Epson, non-PPD-constrained printers.

        Matches against human-readable display labels so it works across driver
        families (HP text values, IPP integers mapped to Draft/Normal/High, etc.).
        Deliberately avoids over-filtering: only excludes combinations that are
        clearly wrong (e.g. a "Photo" quality mode on plain paper).
        """
        if opt_name not in cls._QUALITY_OPT_NAMES:
            return [rv for _, rv in all_pairs]

        # Find media-type display label from whichever vendor option was selected.
        media_label = next(
            (v for k, v in preceding_labels.items()
             if k in {"EPIJ_Medi", "media-type", "MediaType", "CNMediaType", "BrMediaType"}),
            "",
        ).lower()

        if not media_label:
            return [rv for _, rv in all_pairs]

        # IPP/PWG "stationery" = plain paper; other vendors use "plain", "bond", etc.
        _PLAIN_MEDIA = (
            "plain", "stationery", "letterhead", "bond", "recycled", "standard",
        )
        # Photo/specialty media keywords.
        _PHOTO_MEDIA = (
            "photo", "glossy", "premium", "matte", "velvet",
            "silk", "luster", "pearl", "satin", "coated", "brochure",
        )

        if any(k in media_label for k in _PLAIN_MEDIA):
            # Exclude quality labels that are explicitly photo-specific modes.
            # Deliberately conservative: only "Photo" as a standalone quality label.
            return [rv for disp, rv in all_pairs
                    if disp.lower() not in ("photo",)]

        if any(k in media_label for k in _PHOTO_MEDIA):
            # Exclude explicit economy/toner-saver modes for photo media.
            _EXCLUDE = ("economy", "toner saver", "toner save", "eco")
            return [rv for disp, rv in all_pairs
                    if not any(x in disp.lower() for x in _EXCLUDE)]

        return [rv for _, rv in all_pairs]

    @staticmethod
    def _parse_ppd_labels(printer: str) -> dict[str, dict[str, str]]:
        """Parse PPD file to get human-readable labels for option values.

        Returns dict: opt_name → {raw_value → human_label}.
        """
        ppd_file = PrintModule._find_ppd_path(printer)
        if not ppd_file:
            return {}
        labels: dict[str, dict[str, str]] = {}
        try:
            pattern = re.compile(r'^\*(\S+)\s+(\S+)/([^:]+):')
            for line in pathlib.Path(ppd_file).read_text(errors="replace").splitlines():
                m = pattern.match(line)
                if m:
                    opt, val, label = m.group(1), m.group(2), m.group(3).strip()
                    labels.setdefault(opt, {})[val] = label
        except Exception:
            pass
        return labels

    def get_stuck_jobs(self, printer: str) -> list[int]:
        """Return job IDs in definitively stuck states: held=4, stopped=6, aborted=8."""
        try:
            import cups
            conn = cups.Connection()
            jobs = conn.getJobs(which_jobs="not-completed", my_jobs=False)
            stuck = []
            for job_id, attrs in jobs.items():
                if printer not in attrs.get("job-printer-uri", ""):
                    continue
                if attrs.get("job-state", 0) in (4, 6, 8):
                    stuck.append(job_id)
            return stuck
        except Exception as exc:
            log.warning("get_stuck_jobs error: %s", exc)
            return []

    def cancel_all_jobs(self, printer: str) -> int:
        """Cancel all non-completed jobs for *printer*. Returns number cancelled."""
        try:
            import cups
            conn = cups.Connection()
            jobs = conn.getJobs(which_jobs="not-completed", my_jobs=False)
            count = 0
            for job_id, attrs in jobs.items():
                if printer not in attrs.get("job-printer-uri", ""):
                    continue
                try:
                    conn.cancelJob(job_id)
                    count += 1
                except Exception:
                    pass
            log.info("Cancelled %d job(s) for printer %s", count, printer)
            return count
        except Exception as exc:
            log.warning("cancel_all_jobs error: %s", exc)
            return 0

    def build_config(self, printer: str, options: dict[str, str] | None = None) -> PrintConfig:
        return PrintConfig(printer_name=printer, options=options or {})
