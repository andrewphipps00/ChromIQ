"""Working-folder management for ChromIQ sessions."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from core.logger import get_logger

if TYPE_CHECKING:
    from core.settings import AppSettings

log = get_logger(__name__)

_ILLEGAL = re.compile(r"[^\w\-.]+", re.UNICODE)
_TRAIL   = re.compile(r"^[._]+|[._]+$")

# Trailing tag on a measurement stem produced by the "read again & average"
# flow: <base>_read1.ti3, <base>_read2.ti3, … and the averaged <base>_average.ti3.
_READ_VARIANT_RE = re.compile(r"_read(\d+)$")

# Extensions ChromIQ itself generates during a session. A user-entered target
# name (or a loaded file's stem) must never carry one of these: the name is
# used verbatim as the working-folder name and the stem of every derived file,
# so a name ending in e.g. ".icm" produces "<name>.icm.ti3", and colprof then
# writes "<name>.icm.icc" — which the Build-Profile step looks for under the
# wrong name and reports as "Profile file was not created".
# See workflow.profile_builder.expected_icc_path for the matching sink-side fix.
_WORKFILE_EXTS = frozenset({
    ".icc", ".icm", ".mpp",
    ".ti1", ".ti2", ".ti3",
    ".tif", ".tiff",
    ".cal",
})


class FileManager:
    def __init__(self, settings: "AppSettings") -> None:
        self._settings = settings
        self._target_name: str = ""

    # ------------------------------------------------------------------
    # Target name
    # ------------------------------------------------------------------

    @staticmethod
    def strip_workfile_ext(name: str) -> str:
        """Strip any trailing ChromIQ work-file extension(s) from a target name.

        Handles stacked extensions ("chart.icm.ti3" -> "chart") so a name
        pasted from an existing generated file can't poison a new session.
        Dots that are not a known extension (e.g. "Pro.1000") are preserved.
        """
        s = name.strip()
        while True:
            stem, dot, ext = s.rpartition(".")
            if dot and ("." + ext.lower()) in _WORKFILE_EXTS:
                s = stem.rstrip()
                continue
            return s

    @staticmethod
    def _sanitise(name: str) -> str:
        s = name.strip().replace(" ", "-")
        s = _ILLEGAL.sub("_", s)
        s = _TRAIL.sub("", s)
        return s or "session"

    def set_target_name(self, name: str) -> None:
        cleaned = self.strip_workfile_ext(name)
        if not cleaned.strip():
            self._target_name = self._auto_name()
        else:
            self._target_name = self._sanitise(cleaned)
        log.debug("Target name set to: %s", self._target_name)

    def get_target_name(self) -> str:
        if not self._target_name:
            self._target_name = self._auto_name()
        return self._target_name

    @classmethod
    def default_target_name(
        cls,
        printer: str = "Printer",
        paper: str = "Paper",
        papertype: str = "Type",
        instrument: str = "Instr",
    ) -> str:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        parts = [printer, paper, papertype, instrument, ts]
        return "_".join(cls._sanitise(p) for p in parts)

    def _auto_name(self) -> str:
        return self.default_target_name()

    # ------------------------------------------------------------------
    # Folder resolution
    # ------------------------------------------------------------------

    def root_dir(self) -> Path:
        custom = self._settings.get("custom_output_path", "")
        return Path(custom) if custom else Path.home() / "ChromIQ"

    def working_dir(self) -> Path:
        return self.root_dir() / self.get_target_name()

    def ensure_folder(self) -> Path:
        d = self.working_dir()
        d.mkdir(parents=True, exist_ok=True)
        log.debug("Working dir: %s", d)
        return d

    # ------------------------------------------------------------------
    # Averaging — repeated-read file naming
    # ------------------------------------------------------------------

    @staticmethod
    def read_variant_path(base_ti3: Path, n: int) -> Path:
        """`<base>_read{n}.ti3` next to base_ti3 (which must be the canonical file)."""
        return base_ti3.with_name(f"{base_ti3.stem}_read{n}{base_ti3.suffix}")

    @staticmethod
    def average_path(base_ti3: Path) -> Path:
        """`<base>_average.ti3` next to base_ti3."""
        return base_ti3.with_name(f"{base_ti3.stem}_average{base_ti3.suffix}")

    @staticmethod
    def existing_read_variants(work_dir: Path, base_stem: str) -> list[Path]:
        """`<base>_read{N}.ti3` files for base_stem, sorted by N.

        Stems carrying a ``pre_``/``cal_`` prefix (refinement / calibration
        files) are excluded — they aren't averaging reads.
        """
        if not work_dir.exists():
            return []
        found: list[tuple[int, Path]] = []
        for f in work_dir.glob(f"{base_stem}_read*.ti3"):
            if f.stem.startswith(("pre_", "cal_")):
                continue
            m = _READ_VARIANT_RE.search(f.stem)
            if m:
                found.append((int(m.group(1)), f))
        found.sort(key=lambda t: t[0])
        return [f for _, f in found]

    @staticmethod
    def next_read_index(work_dir: Path, base_stem: str) -> int:
        """Smallest unused read index (max existing + 1, or 1 if none)."""
        existing = FileManager.existing_read_variants(work_dir, base_stem)
        highest = 0
        for f in existing:
            m = _READ_VARIANT_RE.search(f.stem)
            if m:
                highest = max(highest, int(m.group(1)))
        return highest + 1

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def clean_folder(self, extensions: list[str] | None = None) -> None:
        """Delete files with the given extensions (or all) in working_dir."""
        d = self.working_dir()
        if not d.exists():
            return
        exts = {e.lstrip(".").lower() for e in extensions} if extensions else None
        deleted = 0
        for f in d.iterdir():
            if f.is_file():
                if exts is None or f.suffix.lstrip(".").lower() in exts:
                    try:
                        f.unlink()
                        deleted += 1
                    except OSError as exc:
                        log.warning("Could not delete %s: %s", f, exc)
        log.debug("Cleaned %d file(s) from %s", deleted, d)
