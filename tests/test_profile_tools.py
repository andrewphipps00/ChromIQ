"""Tests for the Profile Info + Soft-proof tools (icc_info, OOG math, dialogs)."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from workflow.icc_info import IccParseError, is_v4, read_icc
from workflow.softproof_runner import _decode_lab_tiff, lab_d50_to_srgb_array


# ---------------------------------------------------------------------------
# Synthetic ICC profile (minimal valid header + empty tag table)
# ---------------------------------------------------------------------------

def _make_icc(version_major: int = 2, device_class: bytes = b"prtr",
              space: bytes = b"RGB ", pcs: bytes = b"Lab ",
              creator: bytes = b"argl") -> bytes:
    h = bytearray(132)
    struct.pack_into(">I", h, 0, 132)
    h[8] = version_major
    h[9] = 0x20                      # minor version nibble = 2
    h[12:16] = device_class
    h[16:20] = space
    h[20:24] = pcs
    h[36:40] = b"acsp"
    struct.pack_into(">I", h, 64, 1)  # rendering intent = relative
    struct.pack_into(">3i", h, 68, int(0.9642 * 65536), 65536, int(0.8249 * 65536))
    h[80:84] = creator
    struct.pack_into(">I", h, 128, 0)  # tag count = 0
    return bytes(h)


def test_read_icc_v2_fields(tmp_path: Path):
    p = tmp_path / "p.icc"
    p.write_bytes(_make_icc(version_major=2))
    info = read_icc(p)
    assert info.version == "2.2"
    assert not info.is_v4
    assert info.device_class_label == "Output (printer)"
    assert info.color_space_label == "RGB"
    assert info.pcs_label == "Lab"
    assert info.rendering_intent_label == "Media-relative colorimetric"


def test_read_icc_v4_detected(tmp_path: Path):
    p = tmp_path / "v4.icc"
    p.write_bytes(_make_icc(version_major=4))
    assert read_icc(p).is_v4
    assert is_v4(p)


def test_creator_friendly_label(tmp_path: Path):
    p = tmp_path / "xr.icc"
    p.write_bytes(_make_icc(creator=b"XRCM"))
    assert "X-Rite" in read_icc(p).creator_label


def test_non_icc_file_raises(tmp_path: Path):
    p = tmp_path / "bad.icc"
    p.write_bytes(b"not an icc profile at all, definitely missing acsp" * 4)
    with pytest.raises(IccParseError):
        read_icc(p)


def test_is_v4_false_on_missing_file(tmp_path: Path):
    assert is_v4(tmp_path / "nope.icc") is False


# ---------------------------------------------------------------------------
# Soft-proof colour maths
# ---------------------------------------------------------------------------

def test_lab_to_srgb_white_black_grey():
    lab = np.array([[[100.0, 0.0, 0.0], [0.0, 0.0, 0.0], [53.0, 0.0, 0.0]]])
    rgb = lab_d50_to_srgb_array(lab)
    assert tuple(rgb[0, 0]) == (255, 255, 255)        # white
    assert tuple(rgb[0, 1]) == (0, 0, 0)              # black
    r, g, b = rgb[0, 2]
    assert abs(int(r) - int(g)) <= 2 and abs(int(g) - int(b)) <= 2  # neutral grey


def test_decode_lab_tiff_signed_ab(tmp_path: Path):
    # 8-bit CIELab: L unsigned 0..255→0..100; a/b signed int8 in a uint8 byte.
    # bytes: white (255,0,0), a*=+80 (byte 80), b*=-58 (byte 198)
    raw = np.array([[[255, 0, 0], [128, 80, 198]]], dtype=np.uint8)
    p = tmp_path / "lab.tif"
    Image.fromarray(raw, "RGB").save(p)
    lab = _decode_lab_tiff(p)
    assert lab[0, 0, 0] == pytest.approx(100.0, abs=0.5)   # L*
    assert lab[0, 0, 1] == pytest.approx(0.0, abs=0.5)     # a*
    assert lab[0, 1, 1] == pytest.approx(80.0, abs=0.5)    # +a*
    assert lab[0, 1, 2] == pytest.approx(-58.0, abs=0.5)   # -b* (198-256)


def test_oog_mask_threshold():
    # ref vs proof Lab; pixel 0 unchanged (in gamut), pixel 1 shifted (clipped).
    ref = np.array([[[50.0, 10.0, 10.0], [50.0, 80.0, 0.0]]])
    proof = np.array([[[50.0, 10.0, 10.0], [50.0, 40.0, 0.0]]])
    de = np.sqrt(((ref - proof) ** 2).sum(-1))
    mask = de > 2.0
    assert not mask[0, 0]      # in gamut
    assert mask[0, 1]          # out of gamut (ΔE 40)
    assert 100.0 * mask.mean() == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Dialog smoke tests (offscreen)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


class _Settings:
    def __init__(self):
        self._d = {"appearance": "dark"}

    def get(self, key, default=None):
        return self._d.get(key, default)

    def set(self, key, value):
        self._d[key] = value


def _runner():
    from core.argyll_runner import ArgyllRunner
    return ArgyllRunner(_Settings())


def test_profile_info_v4_banner(tmp_path: Path):
    from ui.dialogs.profile_info_dialog import ProfileInfoDialog
    p = tmp_path / "v4.icc"
    p.write_bytes(_make_icc(version_major=4))
    dlg = ProfileInfoDialog(_runner(), _Settings())
    dlg.show()
    dlg.load_profile(p)
    assert dlg._banner.isVisible()
    assert "v4" in dlg._banner.text()
    dlg.close()


def test_profile_info_min_height_floor():
    from ui.dialogs.profile_info_dialog import ProfileInfoDialog
    dlg = ProfileInfoDialog(_runner(), _Settings())
    dlg.show()
    # The detail scroll has a 320px floor → window can't collapse to a sliver.
    assert dlg.minimumHeight() >= 500
    dlg.close()


def test_softproof_dialog_builds_and_floors():
    from ui.dialogs.softproof_dialog import SoftproofDialog
    dlg = SoftproofDialog(_runner(), _Settings())
    dlg.show()
    assert dlg.minimumWidth() == 1180
    assert dlg.minimumHeight() >= 600          # no-overlap floor
    assert not dlg._preview._nav.isVisible()    # single-image: nav hidden
    dlg._teardown_webengine()                   # must not raise (issue #38)
    dlg.close()


def test_bundled_test_target_present_and_v2():
    # The built-in PhotoDisc test target ships with its freeware license and an
    # embedded Adobe RGB v2 profile the soft-proof "Embedded" source can read.
    from core.resource_path import resource_path
    from PIL import Image
    img = resource_path("assets/test_images/photodisc-pdi-target.jpg")
    assert img.is_file()
    icc = Image.open(img).info.get("icc_profile")
    assert icc and icc[8] == 2, "test target needs an embedded ICC v2 profile"
    # The freeware license must ship alongside it.
    assert resource_path("assets/test_images/PhotoDisc-Freeware-License.pdf").is_file()


def test_test_target_button_loads_with_embedded_source():
    from ui.dialogs.softproof_dialog import SoftproofDialog
    dlg = SoftproofDialog(_runner(), _Settings())
    dlg.show()
    dlg._load_test_target()
    assert dlg._image_path is not None and dlg._image_path.is_file()
    assert dlg._source_combo.currentData() == "embedded"
    dlg._teardown_webengine()
    dlg.close()


def test_softproof_v4_printer_blocks_run(tmp_path: Path):
    from ui.dialogs.softproof_dialog import SoftproofDialog
    p = tmp_path / "v4printer.icc"
    p.write_bytes(_make_icc(version_major=4))
    dlg = SoftproofDialog(_runner(), _Settings())
    dlg.show()
    dlg._image_path = tmp_path / "img.tif"
    Image.new("RGB", (8, 8), (200, 100, 50)).save(dlg._image_path)
    dlg._image_edit.setText(str(dlg._image_path))
    dlg._profile_path = p
    dlg._check_profile_version()
    dlg._update_run_enabled()
    assert dlg._banner.isVisible()
    assert not dlg._run_btn.isEnabled()          # v4 printer profile blocks the run
    dlg._teardown_webengine()
    dlg.close()
