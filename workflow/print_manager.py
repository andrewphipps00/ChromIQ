"""Printer detection and option querying via CUPS."""
from __future__ import annotations

import re
import subprocess
from typing import Optional

from core.logger import get_logger
from workflow.cups_printer import PrintConfig

log = get_logger(__name__)


class PrintModule:
    """Detect installed printers and query their supported options."""

    def detect_printers(self) -> list[str]:
        """Return list of installed printer names from CUPS."""
        try:
            # lpstat -a: "<name> accepting requests since ..." — name is always first word,
            # locale-independent (unlike lpstat -p whose first word is localised)
            r = subprocess.run(
                ["lpstat", "-a"],
                capture_output=True, text=True, timeout=10,
            )
            printers: list[str] = []
            for line in r.stdout.splitlines():
                m = re.match(r"(\S+)\s+", line)
                if m:
                    printers.append(m.group(1))
            printers = [p for p in printers if "airprint" not in p.lower()]
            log.debug("Detected printers: %s", printers)
            return printers
        except Exception as exc:
            log.warning("detect_printers error: %s", exc)
            return []

    # For each of the 4 print setting categories:
    # (exact_cups_names_to_try_first, label_keywords_as_fallback)
    # Vendor drivers (e.g. EPSON EPIJ_*) use non-standard names but have readable labels.
    _CATEGORY_SEARCHES: list[tuple[set[str], list[str]]] = [
        ({"InputSlot", "EPIJ_FdSo"},          ["input slot", "paper source", "feed source"]),
        ({"media", "PageSize", "EPIJ_Size"},   ["paper size", "media size", "page size"]),
        ({"media-type", "MediaType", "EPIJ_Medi"}, ["media type", "paper type"]),
        ({"print-quality", "EPIJ_Qual"},       ["print quality", "output quality"]),
    ]

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
                    # Pair each raw CUPS value with its human-readable display label
                    pairs = [(val_labels.get(v, v), v) for v in raw_vals]
                    result[matched_name] = (opt_label, pairs)

        except Exception as exc:
            log.warning("query_options(%s) error: %s", printer, exc)

        log.debug("Options for %s: %d configurable options", printer, len(result))
        return result

    @staticmethod
    def _parse_ppd_labels(printer: str) -> dict[str, dict[str, str]]:
        """Parse PPD file to get human-readable labels for option values.

        Returns dict: opt_name → {raw_value → human_label}.
        """
        import pathlib
        ppd_path = pathlib.Path(f"/etc/cups/ppd/{printer}.ppd")
        if not ppd_path.exists():
            ppd_path = pathlib.Path(f"/private/etc/cups/ppd/{printer}.ppd")
        if not ppd_path.exists():
            return {}
        labels: dict[str, dict[str, str]] = {}
        try:
            pattern = re.compile(r'^\*(\S+)\s+(\S+)/([^:]+):')
            for line in ppd_path.read_text(errors="replace").splitlines():
                m = pattern.match(line)
                if m:
                    opt, val, label = m.group(1), m.group(2), m.group(3).strip()
                    labels.setdefault(opt, {})[val] = label
        except Exception:
            pass
        return labels

    def build_config(self, printer: str, options: dict[str, str] | None = None) -> PrintConfig:
        return PrintConfig(printer_name=printer, options=options or {})
