"""tiffgamut argument construction (no Argyll binary needed).

Focuses on the levers the device-link tool relies on: the popularity filter
(-f), CIECAM02 appearance space (-pj) + viewing conditions (-c), and building
one shared gamut from *several* images (all trailing after the profile).
"""
from __future__ import annotations

from pathlib import Path

from workflow.tiffgamut_runner import TiffgamutParams, TiffgamutRunner


class _FakeRunner:
    is_running = False

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def run(self, tool, args, cwd, on_line=None, on_finish=None):  # noqa: ANN001
        self.calls.append((tool, list(args), cwd))


def _touch(p: Path) -> Path:
    p.write_bytes(b"x")
    return p


def _run(tmp_path: Path, **kw) -> list[str]:
    profile = _touch(tmp_path / "src.icc")
    img = _touch(tmp_path / "a.tif")
    fake = _FakeRunner()
    params = TiffgamutParams(image_path=img, profile_path=profile, **kw)
    TiffgamutRunner(fake).run(params)
    assert fake.calls, "tiffgamut runner never launched"
    return fake.calls[0][1]


def test_defaults_have_no_filter_or_appearance(tmp_path: Path):
    args = _run(tmp_path)
    assert "-f" not in args          # filter_perc 0 → omitted
    assert "-p" not in args          # Lab PCS (soft-proof default)
    assert "-c" not in args


def test_filter_appearance_and_viewcond(tmp_path: Path):
    args = _run(tmp_path, filter_perc=80.0, appearance=True, viewcond="mt")
    assert args[args.index("-f") + 1] == "80"
    assert args[args.index("-p") + 1] == "j"
    assert args[args.index("-c") + 1] == "mt"


def test_multiple_images_all_trail_the_profile(tmp_path: Path):
    profile = _touch(tmp_path / "src.icc")
    imgs = [_touch(tmp_path / f"img{i}.tif") for i in range(3)]
    fake = _FakeRunner()
    TiffgamutRunner(fake).run(
        TiffgamutParams(image_path=imgs[0], image_paths=imgs, profile_path=profile))
    args = fake.calls[0][1]
    # profile immediately after -O <base>, then every image, in order.
    assert args[-4] == str(profile)
    assert args[-3:] == [str(imgs[0]), str(imgs[1]), str(imgs[2])]


def test_missing_image_reports_error(tmp_path: Path):
    profile = _touch(tmp_path / "src.icc")
    errors: list[str] = []
    fake = _FakeRunner()
    r = TiffgamutRunner(fake)
    r.error.connect(errors.append)
    r.run(TiffgamutParams(image_path=tmp_path / "nope.tif", profile_path=profile))
    assert not fake.calls          # never launched
    assert errors and "nope.tif" in errors[0]
