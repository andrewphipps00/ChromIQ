"""D50/D65 colorimetry fix (Knut): the device-derived reference (device RGB
treated as sRGB → XYZ under D65) must be Bradford-adapted to D50 before it is
compared with the measured values (which are D50, like xyz_to_lab). Before the
fix an imported measurement's neutral patches carried a ~19 b* error at white and
~1.5 ΔE on average."""
from workflow.i1profiler_import import WHITE_XYZ, _patch_xyz
from workflow.measurement_report import _bradford_d65_to_d50
from workflow.ti3_analysis import xyz_to_lab


def test_bradford_maps_d65_white_to_d50_white():
    x, y, z = _bradford_d65_to_d50(*WHITE_XYZ)          # D65 white → D50 white
    assert abs(x - 96.42) < 0.1
    assert abs(y - 100.0) < 0.1
    assert abs(z - 82.52) < 0.1


def test_neutral_device_reference_is_neutral_in_d50():
    """The core of the fix: a neutral device value (R=G=B) must land on the
    neutral axis (a*≈0, b*≈0) once adapted. Before the fix, white read b*≈-19."""
    for v in (100.0, 75.0, 50.0, 25.0):
        xyz = _bradford_d65_to_d50(*_patch_xyz(v, v, v))
        _L, a, b = xyz_to_lab(tuple(c / 100.0 for c in xyz))
        assert abs(a) < 0.5 and abs(b) < 0.5, f"neutral {v}: a*={a:.2f} b*={b:.2f}"


def test_unadapted_white_would_be_wrong():
    """Guards the reason the fix exists: feeding the D65 reference straight into
    the D50 xyz_to_lab (the old behaviour) is badly non-neutral."""
    _L, a, b = xyz_to_lab(tuple(c / 100.0 for c in _patch_xyz(100.0, 100.0, 100.0)))
    assert abs(b) > 10.0                               # ~-19 before the fix
