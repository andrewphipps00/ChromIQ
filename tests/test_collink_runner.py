"""Tools → "Create device-link profile" (collink wrapper).

Covers, without needing the Argyll binary:
  * collink argument construction (defaults + every optional lever);
  * the three profile paths always coming last, in src/dst/out order;
  * structured error parsing (the v4 case in particular).
"""
from __future__ import annotations

from pathlib import Path

from workflow.collink_runner import CollinkParams, CollinkRunner


def _params(tmp_path: Path, **kw) -> CollinkParams:
    base = dict(
        src_path=tmp_path / "src.icc",
        dst_path=tmp_path / "printer.icc",
        out_path=tmp_path / "link.icc",
    )
    base.update(kw)
    return CollinkParams(**base)


def test_build_args_defaults(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path))
    # -v, -q<h>, gamut-map mode, intent, src/dst viewconds, then the 3 paths.
    assert args == [
        "-v", "-qh", "-g", "-ip", "-cmt", "-dpp",
        str(tmp_path / "src.icc"),
        str(tmp_path / "printer.icc"),
        str(tmp_path / "link.icc"),
    ]


def test_paths_are_always_last_in_order(tmp_path: Path):
    args = CollinkRunner(None)._build_args(
        _params(tmp_path, black_point_hack=True, diagnostic=True,
                description="My Link", intent="r", quality="u")
    )
    assert args[-3:] == [
        str(tmp_path / "src.icc"),
        str(tmp_path / "printer.icc"),
        str(tmp_path / "link.icc"),
    ]


def test_quality_and_intent_attached(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path, quality="m", intent="s"))
    assert "-qm" in args
    assert "-is" in args


def test_black_point_and_diagnostic_flags(tmp_path: Path):
    args = CollinkRunner(None)._build_args(
        _params(tmp_path, black_point_hack=True, diagnostic=True)
    )
    assert "-b" in args
    assert "-P" in args


def test_no_black_point_or_diagnostic_by_default(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path))
    assert "-b" not in args
    assert "-P" not in args


def test_image_source_gamut_attached_to_g(tmp_path: Path):
    gam = tmp_path / "image.gam"
    args = CollinkRunner(None)._build_args(_params(tmp_path, src_gamut=gam))
    assert f"-g{gam}" in args
    assert "-g" not in args  # bare -g replaced by the attached form


def test_inverse_gamut_uses_capital_g(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path, inverse_gamut=True))
    assert "-G" in args
    assert "-g" not in args


def test_inverse_gamut_with_image_gamut(tmp_path: Path):
    gam = tmp_path / "image.gam"
    args = CollinkRunner(None)._build_args(
        _params(tmp_path, inverse_gamut=True, src_gamut=gam))
    assert f"-G{gam}" in args


def test_forced_white_flag(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path, forced_white=True))
    assert "-w" in args


def test_calibration_takes_separate_value(tmp_path: Path):
    cal = tmp_path / "printer.cal"
    args = CollinkRunner(None)._build_args(_params(tmp_path, calibration=cal))
    assert args[args.index("-a") + 1] == str(cal)


def test_lut3d_export_attached(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path, lut3d="c"))
    assert "-3c" in args


def test_lut3d_omitted_when_empty(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path))
    assert not any(a.startswith("-3") for a in args)


def test_abstract_profile_takes_separate_value(tmp_path: Path):
    abs_p = tmp_path / "tweak.icc"
    args = CollinkRunner(None)._build_args(_params(tmp_path, abstract=abs_p))
    i = args.index("-p")
    assert args[i + 1] == str(abs_p)


def test_identification_strings(tmp_path: Path):
    args = CollinkRunner(None)._build_args(
        _params(tmp_path, description="Desc", manufacturer="Maker",
                model="Model", copyright="(c) me")
    )
    for flag, val in (("-D", "Desc"), ("-A", "Maker"),
                      ("-M", "Model"), ("-C", "(c) me")):
        assert args[args.index(flag) + 1] == val


def test_empty_identification_strings_omitted(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path))
    for flag in ("-D", "-A", "-M", "-C", "-p"):
        assert flag not in args


def test_verbose_can_be_disabled(tmp_path: Path):
    args = CollinkRunner(None)._build_args(_params(tmp_path, verbose=False))
    assert "-v" not in args


# --- error parsing ---------------------------------------------------------

def test_scan_detects_v4():
    r = CollinkRunner(None)
    r._scan_line("Warning: ICC V4 not supported!")
    fail = r.primary_failure()
    assert fail is not None
    assert fail[0] == "icc_v4"
    assert "version 4" in fail[1].lower()


def test_scan_detects_no_conversion():
    r = CollinkRunner(None)
    r._scan_line("collink: Error - get xlookup object failed: 1, "
                 "icc_get_luobj: Unable to locate usable conversion")
    assert r.primary_failure()[0] == "no_conversion"


def test_clean_run_has_no_failure():
    r = CollinkRunner(None)
    for line in ("Got options", "Configured options", "Linking profiles"):
        r._scan_line(line)
    assert r.primary_failure() is None
