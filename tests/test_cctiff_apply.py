"""cctiff argument construction for the "Apply device-link" tool (no binary).

Covers the two chains the tool builds:
  * link_args    — apply a device-link (no -i; intent baked in), TIFF output;
  * convert_args — colorimetric conversion into the link's source space first.
"""
from __future__ import annotations

from pathlib import Path

from workflow.cctiff_apply import CctiffApplyParams, CctiffApplyRunner, convert_args, link_args


def test_link_args_order_and_flags(tmp_path: Path):
    args = link_args(tmp_path / "link.icc", tmp_path / "in.tif", tmp_path / "out.tif")
    assert args == [
        "-v", "-p", "-f", "T",
        str(tmp_path / "link.icc"),
        str(tmp_path / "in.tif"),
        str(tmp_path / "out.tif"),
    ]


def test_link_args_no_intent_flag(tmp_path: Path):
    # A device-link bakes its intent in — cctiff must not get -i.
    args = link_args(tmp_path / "l.icc", tmp_path / "i.tif", tmp_path / "o.tif")
    assert "-i" not in args


def test_link_args_forces_tiff_output(tmp_path: Path):
    # -f T so a JPEG source still yields a print-ready TIFF.
    args = link_args(tmp_path / "l.icc", tmp_path / "photo.jpg", tmp_path / "o.tif")
    assert args[args.index("-f") + 1] == "T"


def test_convert_args_relative_both_profiles_then_paths(tmp_path: Path):
    args = convert_args(tmp_path / "emb.icc", tmp_path / "src.icc",
                        tmp_path / "in.tif", tmp_path / "out.tif")
    # -i r before each profile, source-from then source-to, then in/out last.
    assert args == [
        "-v", "-p", "-f", "T",
        "-i", "r", str(tmp_path / "emb.icc"),
        "-i", "r", str(tmp_path / "src.icc"),
        str(tmp_path / "in.tif"),
        str(tmp_path / "out.tif"),
    ]


def test_runner_delegates_to_link_args(tmp_path: Path):
    p = CctiffApplyParams(
        link_path=tmp_path / "l.icc", in_path=tmp_path / "i.tif",
        out_path=tmp_path / "o.tif")
    assert CctiffApplyRunner(None)._build_args(p) == link_args(
        tmp_path / "l.icc", tmp_path / "i.tif", tmp_path / "o.tif")


def test_error_parsing_v4(tmp_path: Path):
    r = CctiffApplyRunner(None)
    r._scan_line("Error - ICC V4 not supported!")
    fail = r.primary_failure()
    assert fail is not None and fail[0] == "icc_v4"
