"""Convert an Argyll TI1 target into i1Profiler patch-set formats.

i1iSis chart scanners are driven by i1Profiler, not by Argyll. When the user
selects "i1iSis" as the measurement instrument in ChromIQ, we still let `targen`
generate the patch list, then re-emit it in the formats i1Profiler can load:

  - <stem>.txt   CGATS.5 ASCII patch set   (RGB and CMYK only)
  - <stem>.pxf   CxF3 XML patch set        (all colorspaces)

The export is **colorspace-aware** — it reads the device channels straight from
the TI1 and emits the matching i1Profiler structure:

  | Argyll device | i1Profiler element   | scale            | .txt? |
  |---------------|----------------------|------------------|-------|
  | RGB           | <cc:ColorRGB>        | 0..100 ->0..255  | yes   |
  | CMYK          | <cc:ColorCMYK>       | 0..100 (as-is)   | yes   |
  | CMYK+N        | <cc:ColorCMYKPlusN>  | 0..100 (as-is)   | no    |

For CMYK / CMYK+N the .pxf additionally carries the X-Rite Prism
`ColorSpecification` + `ProfileSettings` blocks and per-ink `PLUS_n_COLOR`
definitions, matching i1Profiler's own shipped reference patch sets so its
CMYK / extended-gamut loader accepts them.

i1Profiler re-lays-out the chart and asks which i1iSis variant to use, so we do
not differentiate i1iSis / i1iSis 2 / i1iSis XL here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

# --- colorant model -------------------------------------------------------

CMYK_NAME = {"C": "Cyan", "M": "Magenta", "Y": "Yellow", "K": "Black"}

# Argyll colorant letter -> (i1Profiler ink name, "L|a|b" reference Lab).
# O/G/V/B Lab values are copied verbatim from i1Profiler's shipped
# "Idealliance ECG ... CMYKOGV" PLUS_n_COLOR attributes; the others are
# best-effort placeholders flagged at export time.
EXTRA_INK = {
    "O": ("Orange", "71|33|71"),
    "G": ("Green", "57|-74|30"),
    "V": ("Violet", "39|75|-95"),
    "B": ("Blue", "29.57|67.02|-128"),
    "R": ("Red", "48|68|48"),            # placeholder Lab
    "c": ("Light Cyan", "83|-22|-22"),   # placeholder Lab
    "m": ("Light Magenta", "75|34|-13"), # placeholder Lab
}
_PLACEHOLDER_LAB = {"R", "c", "m"}

# i1Profiler's internal ink-colorspace enum. Verified against shipped
# references: CMYK (4 ch) -> 9, CMYKOGV (7 ch) -> 15. Both fit 2*ch+1, which is
# used (and flagged) for the CMYK+1/+2/+4 cases that ship no reference anywhere.
_VERIFIED_ENUM = {4: 9, 7: 15}

# Non-device field prefixes in a TI1 data-format line.
_NON_DEVICE = {"XYZ", "LAB", "SPECTRAL", "D50", "D65", "SAMPLE"}

Patch = tuple[int, float, float, float]  # legacy RGB tuple (sample_id, R, G, B)


@dataclass
class Target:
    """Parsed TI1: device channels (0..100) plus the detected colorspace."""

    color_rep: str               # raw COLOR_REP, e.g. "iRGB", "CMYK", "CMYKOGV"
    kind: str                    # "RGB" | "CMYK" | "CMYKPLUSN"
    channels: list[str]          # device channel letters, column order
    rows: list[tuple[int, dict[str, float]]]  # (sample_id, {letter: 0..100})

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def extras(self) -> list[str]:
        """Extra-ink channels beyond CMYK (empty unless this is CMYK+N)."""
        if self.kind != "CMYKPLUSN":
            return []
        return [c for c in self.channels if c not in CMYK_NAME]


def ink_colorspace_enum(n_channels: int) -> tuple[int, bool]:
    """Return (enum, verified). Verified only for 4-channel and 7-channel."""
    if n_channels in _VERIFIED_ENUM:
        return _VERIFIED_ENUM[n_channels], True
    return 2 * n_channels + 1, False


# --- parsing ---------------------------------------------------------------


def parse_ti1(ti1_path: Path) -> Target:
    text = ti1_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "CTI1":
        first = lines[0] if lines else ""
        raise ValueError(f"{ti1_path}: not a CTI1 file (first line: {first!r})")

    color_rep = ""
    fmt_fields: list[str] = []
    in_format = in_data = False
    raw_rows: list[tuple[int, dict[str, str]]] = []

    for raw in lines:
        line = raw.strip()
        if line.startswith("COLOR_REP"):
            color_rep = line.split('"')[1] if '"' in line else line.split()[1]
            continue
        if line == "BEGIN_DATA_FORMAT":
            fmt_fields, in_format = [], True
            continue
        if line == "END_DATA_FORMAT":
            in_format = False
            continue
        if line == "BEGIN_DATA":
            in_data = True
            continue
        if line == "END_DATA":
            in_data = False
            if raw_rows:
                break
            continue
        if in_format:
            fmt_fields.extend(line.split())
            continue
        if in_data and line:
            raw_rows.append((0, dict(zip(fmt_fields, line.split()))))

    if not raw_rows:
        raise ValueError(f"{ti1_path}: no data rows found")

    # Device fields = format fields with a channel suffix that aren't SAMPLE_ID
    # or colorimetric (XYZ/LAB/...). The text before the last '_' is the
    # colorant string (e.g. "RGB", "CMYK", "CMYKOGV"); the suffix is the channel.
    device_fields = [
        f for f in fmt_fields
        if "_" in f and f != "SAMPLE_ID" and f.split("_", 1)[0] not in _NON_DEVICE
    ]
    if not device_fields:
        raise ValueError(f"{ti1_path}: no device channels in {fmt_fields!r}")
    channels = [f.split("_", 1)[1] for f in device_fields]

    rows: list[tuple[int, dict[str, float]]] = []
    for _sid, row in raw_rows:
        sid = int(row["SAMPLE_ID"])
        vals = {ch: float(row[f]) for ch, f in zip(channels, device_fields)}
        rows.append((sid, vals))

    kind = _detect_kind(channels, color_rep)
    return Target(color_rep=color_rep, kind=kind, channels=channels, rows=rows)


def _detect_kind(channels: list[str], color_rep: str) -> str:
    if set(channels) == {"R", "G", "B"}:
        return "RGB"
    if channels[:4] == ["C", "M", "Y", "K"]:
        return "CMYK" if len(channels) == 4 else "CMYKPLUSN"
    raise ValueError(
        f"unsupported colorspace for i1Profiler export: COLOR_REP {color_rep!r} "
        f"channels {channels!r} (RGB, CMYK and CMYK+N are supported)"
    )


# --- value formatting ------------------------------------------------------


def _to_255_float(v: float) -> float:
    return v * 2.55


def _to_255_int(v: float) -> int:
    return max(0, min(255, round(v * 2.55)))


def _trim(v: float) -> str:
    """Trim trailing zeros the way the reference files do (0, 69.8, 100)."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s or "0"


def _now_iso() -> str:
    s = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


# --- CGATS .txt ------------------------------------------------------------


def _txt_header(descriptor: str, fields: str, n_fields: int, n_sets: int) -> list[str]:
    return [
        "CGATS.5",
        "",
        'ORIGINATOR "ChromIQ"',
        f'DESCRIPTOR "{descriptor}"',
        f'CREATED "{datetime.now().strftime("%B %d, %Y")}"',
        'INSTRUMENTATION "Not specified"',
        'MEASUREMENT_SOURCE "Not specified"',
        'PRINT_CONDITIONS "Not specified"',
        "",
        'KEYWORD "SampleID"',
        f"NUMBER_OF_FIELDS {n_fields}",
        "BEGIN_DATA_FORMAT",
        fields,
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {n_sets}",
        "BEGIN_DATA",
    ]


def write_txt(target: Target, out_path: Path, descriptor: str) -> bool:
    """Write the CGATS .txt patch set. Returns False (no file) for CMYK+N."""
    if target.kind == "RGB":
        lines = _txt_header(descriptor, "SampleID RGB_R RGB_G RGB_B ", 4, len(target.rows))
        for sid, v in target.rows:
            lines.append(
                f"{sid} {_to_255_float(v['R']):.4f} "
                f"{_to_255_float(v['G']):.4f} {_to_255_float(v['B']):.4f} "
            )
    elif target.kind == "CMYK":
        lines = _txt_header(descriptor, "SampleID CMYK_C CMYK_M CMYK_Y CMYK_K ", 5, len(target.rows))
        for sid, v in target.rows:
            lines.append(
                f"{sid} {v['C']:.4f} {v['M']:.4f} {v['Y']:.4f} {v['K']:.4f} "
            )
    else:
        return False  # CMYK+N: i1Profiler ships no CGATS .txt for extended gamut
    lines.append("END_DATA")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return True


# --- CxF3 .pxf -------------------------------------------------------------


def _color_specification() -> list[str]:
    return [
        "\t\t<cc:ColorSpecificationCollection>",
        '\t\t\t<cc:ColorSpecification Id="Unknown">',
        "\t\t\t\t<cc:MeasurementSpec>",
        "\t\t\t\t\t<cc:MeasurementType>Colorimetric_Reflectance</cc:MeasurementType>",
        "\t\t\t\t\t<cc:GeometryChoice>",
        "\t\t\t\t\t\t<cc:UnknownGeometry>Target</cc:UnknownGeometry>",
        "\t\t\t\t\t</cc:GeometryChoice>",
        "\t\t\t\t</cc:MeasurementSpec>",
        "\t\t\t</cc:ColorSpecification>",
        "\t\t</cc:ColorSpecificationCollection>",
    ]


def _profile_settings(n_channels: int, n_plus: int) -> list[str]:
    """Reproduce the reference <ProfileSettings>, parametrised by colorspace.

    Fixed sub-elements are copied verbatim from the shipped CMYK / CMYKOGV
    references; only the ink limit, the colorspace enum, and the number of
    InkGeneration_k blocks vary with the colorspace.
    """
    enum, _ = ink_colorspace_enum(n_channels)
    ink_limit = 100 * n_channels  # CMYK->400, CMYKOGV->700 (matches references)
    out = [
        '\t\t<ProfileSettings DeviceType="Printer">',
        '\t\t\t<ProfileVersion value="4"/>',
        '\t\t\t<EmbeddedProfileVersion value="-1"/>',
        "\t\t\t<ChromaticAdaptation>Bradford</ChromaticAdaptation>",
        "\t\t\t<BlackGeneration>",
        "\t\t\t\t<BlackType>2</BlackType>",
        "\t\t\t\t<UseMaximumBlack>0</UseMaximumBlack>",
        "\t\t\t\t<UseIntelligentBlack>1</UseIntelligentBlack>",
        "\t\t\t\t<BlackWidth>50</BlackWidth>",
        "\t\t\t\t<NumberOfCurvePoints>4</NumberOfCurvePoints>",
        "\t\t\t\t<KPoints>0 0 18.9465 100 </KPoints>",
        "\t\t\t\t<LPoints>0 30 65 100 </LPoints>",
        "\t\t\t\t<CurveType>4</CurveType>",
        "\t\t\t</BlackGeneration>",
        "\t\t\t<InkLimiting>",
        f"\t\t\t\t<InkLimit>{ink_limit}</InkLimit>",
        f"\t\t\t\t<ColorSpace>{enum}</ColorSpace>",
        "\t\t\t</InkLimiting>",
    ]
    for k in range(1, n_plus + 1):
        out += [
            f"\t\t\t<InkGeneration_{k}>",
            "\t\t\t\t<NeutralAmount>50</NeutralAmount>",
            "\t\t\t\t<HueLikeness>50</HueLikeness>",
            "\t\t\t\t<UseIntelligentInk>1</UseIntelligentInk>",
            "\t\t\t\t<NumberOfCurvePoints>3</NumberOfCurvePoints>",
            "\t\t\t\t<INKPoints>0 1 1 </INKPoints>",
            "\t\t\t\t<LPoints>0 56 100 </LPoints>",
            f"\t\t\t</InkGeneration_{k}>",
        ]
    out += [
        f"\t\t\t<ProfileInkColorSpace>{enum}</ProfileInkColorSpace>",
        "\t\t\t<Saturation>50</Saturation>",
        "\t\t\t<UseMediaSmoothness>0</UseMediaSmoothness>",
        "\t\t\t<SmoothnessMedianOverall>-1</SmoothnessMedianOverall>",
        "\t\t\t<Smoothness>50</Smoothness>",
        "\t\t\t<Contrast>50</Contrast>",
        "\t\t\t<NeutralizeGray>0</NeutralizeGray>",
        "\t\t\t<ChromaAdjust>50</ChromaAdjust>",
        "\t\t\t<DarkeningForHighChroma>0</DarkeningForHighChroma>",
        "\t\t\t<WhitePointPaperDeltaEThreshold>3.5</WhitePointPaperDeltaEThreshold>",
        "\t\t\t<TableSizeAToB>Medium</TableSizeAToB>",
        "\t\t\t<TableSizeBToA>Medium</TableSizeBToA>",
        "\t\t\t<BitDepth>16</BitDepth>",
        "\t\t</ProfileSettings>",
    ]
    return out


def _pxf_open(descriptor: str, created: str) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<cc:CxF xmlns:cc="http://colorexchangeformat.com/CxF3-core" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        "\t<cc:FileInformation>",
        "\t\t<cc:Creator>ChromIQ</cc:Creator>",
        f"\t\t<cc:CreationDate>{created}</cc:CreationDate>",
        f"\t\t<cc:Description>{escape(descriptor)}</cc:Description>",
        "\t</cc:FileInformation>",
        "\t<cc:Resources>",
        "\t\t<cc:ObjectCollection>",
    ]


def _rgb_object(idx: int, v: dict[str, float], created: str) -> list[str]:
    return [
        f'\t\t\t<cc:Object ObjectType="Target" Name="Target{idx}" Id="c{idx}">',
        f"\t\t\t\t<cc:CreationDate>{created}</cc:CreationDate>",
        "\t\t\t\t<cc:DeviceColorValues>",
        '\t\t\t\t\t<cc:ColorRGB ColorSpecification="Unknown">',
        f"\t\t\t\t\t\t<cc:R>{_to_255_int(v['R'])}</cc:R>",
        f"\t\t\t\t\t\t<cc:G>{_to_255_int(v['G'])}</cc:G>",
        f"\t\t\t\t\t\t<cc:B>{_to_255_int(v['B'])}</cc:B>",
        "\t\t\t\t\t</cc:ColorRGB>",
        "\t\t\t\t</cc:DeviceColorValues>",
        "\t\t\t</cc:Object>",
    ]


def _cmyk_object(idx: int, v: dict[str, float], extras: list[str], created: str) -> list[str]:
    elem = "ColorCMYK" if not extras else "ColorCMYKPlusN"
    out = [
        f'\t\t\t<cc:Object ObjectType="Target" Name="Target{idx}" Id="c{idx}">',
        f"\t\t\t\t<cc:CreationDate>{created}</cc:CreationDate>",
        "\t\t\t\t<cc:DeviceColorValues>",
        f'\t\t\t\t\t<cc:{elem} ColorSpecification="Unknown">',
    ]
    for letter in ("C", "M", "Y", "K"):
        out.append(f"\t\t\t\t\t\t<cc:{CMYK_NAME[letter]}>{_trim(v.get(letter, 0.0))}</cc:{CMYK_NAME[letter]}>")
    for letter in extras:
        out += [
            "\t\t\t\t\t\t<cc:SpotColor>",
            f"\t\t\t\t\t\t\t<cc:Name>{EXTRA_INK[letter][0]}</cc:Name>",
            f"\t\t\t\t\t\t\t<cc:Percentage>{_trim(v.get(letter, 0.0))}</cc:Percentage>",
            "\t\t\t\t\t\t</cc:SpotColor>",
        ]
    out += [
        f"\t\t\t\t\t</cc:{elem}>",
        "\t\t\t\t</cc:DeviceColorValues>",
        "\t\t\t</cc:Object>",
    ]
    return out


def write_pxf(
    target: Target,
    out_path: Path,
    descriptor: str,
    measurement_device: str = "i1iSis",
) -> None:
    created = _now_iso()
    desc_x = escape(descriptor)
    parts = _pxf_open(descriptor, created)

    if target.kind == "RGB":
        for idx, (_sid, v) in enumerate(target.rows, start=1):
            parts += _rgb_object(idx, v, created)
        parts.append("\t\t</cc:ObjectCollection>")
        parts.append("\t</cc:Resources>")
        parts.append("\t<cc:CustomResources>")
        parts.append('\t\t<xrp:Prism xmlns:xrp="http://www.xrite.com/products/prism" release="2.0">')
        parts.append(
            "  <xrp:CustomAttributes "
            'ColorSpace="RGB" '
            f'MeasurementDevice="{escape(measurement_device)}" '
            'MeasurementScanningMode="Strip" '
            'NumberPatchPages="1" '
            'ScramblePatches="False" '
            'TestChartType="RGB Variable" '
            f'TitleString="{desc_x}" '
            'WriteProtected="False" '
            f'numberCorePatches="{len(target.rows)}" '
            'numberImagePatches="0" '
            'numberSpotPatches="0"/>'
        )
        parts.append("</xrp:Prism>")
        parts.append("")
        parts.append("\t</cc:CustomResources>")
        parts.append("</cc:CxF>")
        parts.append("")
        out_path.write_text("\n".join(parts), encoding="utf-8")
        return

    # CMYK / CMYK+N: device patches + ColorSpecification + full ProfileSettings.
    extras = target.extras
    n_plus = len(extras)
    for idx, (_sid, v) in enumerate(target.rows, start=1):
        parts += _cmyk_object(idx, v, extras, created)
    parts.append("\t\t</cc:ObjectCollection>")
    parts += _color_specification()
    parts.append("\t</cc:Resources>")

    plus_attrs = ""
    for i, letter in enumerate(extras, start=1):
        name, lab = EXTRA_INK[letter]
        plus_attrs += f' PLUS_{i}_COLOR="{name}|{lab}|1|2|50|50|{i}"'
    colorspace = "CMYK" if n_plus == 0 else f"CMYK + {n_plus}"
    ink_limit = 100 * target.n_channels

    parts.append("\t<cc:CustomResources>")
    parts.append('\t\t<xrp:Prism xmlns:xrp="http://www.xrite.com/products/prism" release="2.0">')
    parts.append(
        "  <xrp:CustomAttributes "
        f'ColorSpace="{colorspace}" '
        f'InkLimit="{ink_limit}" '
        f'MeasurementDevice="{escape(measurement_device)}" '
        'MeasurementScanningMode="Strip" '
        'NumberPatchPages="1" '
        'ScramblePatches="False" '
        f'TestChartType="{colorspace}"'
        f"{plus_attrs} "
        f'TitleString="{desc_x}" '
        'WriteProtected="False" '
        f'numberCorePatches="{len(target.rows)}" '
        'numberImagePatches="0" '
        'numberSpotPatches="0"/>'
    )
    parts += _profile_settings(target.n_channels, n_plus)
    parts.append("</xrp:Prism>")
    parts.append("")
    parts.append("\t</cc:CustomResources>")
    parts.append("</cc:CxF>")
    parts.append("")
    out_path.write_text("\n".join(parts), encoding="utf-8")


def export_from_ti1(
    ti1_path: Path, descriptor: str | None = None
) -> tuple[Path | None, Path]:
    """Read TI1, write .pxf (always) and .txt (RGB/CMYK only) next to it.

    Returns (txt_path_or_None, pxf_path). txt_path is None for CMYK+N, which
    i1Profiler only accepts as a CxF3 .pxf.
    """
    target = parse_ti1(ti1_path)
    desc = descriptor or ti1_path.stem
    txt_out = ti1_path.with_suffix(".txt")
    pxf_out = ti1_path.with_suffix(".pxf")
    wrote_txt = write_txt(target, txt_out, desc)
    write_pxf(target, pxf_out, desc)
    return (txt_out if wrote_txt else None), pxf_out
