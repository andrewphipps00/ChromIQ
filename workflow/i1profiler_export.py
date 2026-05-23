"""Convert an Argyll TI1 target into i1Profiler patch-set formats.

i1iSis chart scanners are driven by i1Profiler, not by Argyll. When the
user selects "i1iSis" as the measurement instrument in ChromIQ, we still
let `targen` generate the patch list, then re-emit it in the two formats
i1Profiler can load:

  - <stem>.txt   CGATS.5 ASCII patch set
  - <stem>.pxf   CxF3 XML patch set (with X-Rite Prism CustomResources)

Both files carry only RGB device values on the 0..255 scale. i1Profiler
re-lays-out the chart and asks the user which i1iSis variant to use, so
we do not need to differentiate i1iSis / i1iSis 2 / i1iSis XL here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

Patch = tuple[int, float, float, float]  # (sample_id, R, G, B) with RGB on 0..100


def parse_ti1(ti1_path: Path) -> list[Patch]:
    text = ti1_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    if not lines or lines[0].strip() != "CTI1":
        raise ValueError(f"{ti1_path}: not a CTI1 file (first line: {lines[0]!r})")

    fmt_fields: list[str] = []
    in_format = False
    in_data = False
    patches: list[Patch] = []

    for raw in lines:
        line = raw.strip()
        if line == "BEGIN_DATA_FORMAT":
            fmt_fields = []
            in_format = True
            continue
        if line == "END_DATA_FORMAT":
            in_format = False
            continue
        if line == "BEGIN_DATA":
            in_data = True
            continue
        if line == "END_DATA":
            in_data = False
            if patches:
                break
            continue
        if in_format:
            fmt_fields.extend(line.split())
            continue
        if in_data and line:
            cols = line.split()
            row = dict(zip(fmt_fields, cols))
            try:
                sid = int(row["SAMPLE_ID"])
                r = float(row["RGB_R"])
                g = float(row["RGB_G"])
                b = float(row["RGB_B"])
            except KeyError as exc:
                raise ValueError(
                    f"{ti1_path}: missing expected field {exc!s} in {fmt_fields!r}"
                ) from None
            patches.append((sid, r, g, b))

    if not patches:
        raise ValueError(f"{ti1_path}: no data rows found")
    return patches


def _to_255_float(v: float) -> float:
    return v * 2.55


def _to_255_int(v: float) -> int:
    return max(0, min(255, round(v * 2.55)))


def write_txt(patches: list[Patch], out_path: Path, descriptor: str) -> None:
    created = datetime.now().strftime("%B %d, %Y")
    lines = [
        "CGATS.5",
        "",
        'ORIGINATOR "ChromIQ"',
        f'DESCRIPTOR "{descriptor}"',
        f'CREATED "{created}"',
        'INSTRUMENTATION "Not specified"',
        'MEASUREMENT_SOURCE "Not specified"',
        'PRINT_CONDITIONS "Not specified"',
        "",
        'KEYWORD "SampleID"',
        "NUMBER_OF_FIELDS 4",
        "BEGIN_DATA_FORMAT",
        "SampleID RGB_R RGB_G RGB_B ",
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {len(patches)}",
        "BEGIN_DATA",
    ]
    for sid, r, g, b in patches:
        lines.append(
            f"{sid} {_to_255_float(r):.4f} {_to_255_float(g):.4f} {_to_255_float(b):.4f} "
        )
    lines.append("END_DATA")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def write_pxf(
    patches: list[Patch],
    out_path: Path,
    descriptor: str,
    measurement_device: str = "i1iSis",
) -> None:
    created = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    created = created[:-2] + ":" + created[-2:]
    desc_x = escape(descriptor)

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<cc:CxF xmlns:cc="http://colorexchangeformat.com/CxF3-core" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">',
        "\t<cc:FileInformation>",
        "\t\t<cc:Creator>ChromIQ</cc:Creator>",
        f"\t\t<cc:CreationDate>{created}</cc:CreationDate>",
        f"\t\t<cc:Description>{desc_x}</cc:Description>",
        "\t</cc:FileInformation>",
        "\t<cc:Resources>",
        "\t\t<cc:ObjectCollection>",
    ]

    for idx, (_sid, r, g, b) in enumerate(patches, start=1):
        ri, gi, bi = _to_255_int(r), _to_255_int(g), _to_255_int(b)
        parts.extend(
            [
                f'\t\t\t<cc:Object ObjectType="Target" Name="Target{idx}" Id="c{idx}">',
                f"\t\t\t\t<cc:CreationDate>{created}</cc:CreationDate>",
                "\t\t\t\t<cc:DeviceColorValues>",
                '\t\t\t\t\t<cc:ColorRGB ColorSpecification="Unknown">',
                f"\t\t\t\t\t\t<cc:R>{ri}</cc:R>",
                f"\t\t\t\t\t\t<cc:G>{gi}</cc:G>",
                f"\t\t\t\t\t\t<cc:B>{bi}</cc:B>",
                "\t\t\t\t\t</cc:ColorRGB>",
                "\t\t\t\t</cc:DeviceColorValues>",
                "\t\t\t</cc:Object>",
            ]
        )

    parts.append("\t\t</cc:ObjectCollection>")
    parts.append("\t</cc:Resources>")

    parts.append("\t<cc:CustomResources>")
    parts.append(
        '\t\t<xrp:Prism xmlns:xrp="http://www.xrite.com/products/prism" release="2.0">'
    )
    parts.append(
        '  <xrp:CustomAttributes '
        'ColorSpace="RGB" '
        f'MeasurementDevice="{escape(measurement_device)}" '
        'MeasurementScanningMode="Strip" '
        'NumberPatchPages="1" '
        'ScramblePatches="False" '
        'TestChartType="RGB Variable" '
        f'TitleString="{desc_x}" '
        'WriteProtected="False" '
        f'numberCorePatches="{len(patches)}" '
        'numberImagePatches="0" '
        'numberSpotPatches="0"/>'
    )
    parts.append("</xrp:Prism>")
    parts.append("")
    parts.append("\t</cc:CustomResources>")
    parts.append("</cc:CxF>")
    parts.append("")

    out_path.write_text("\n".join(parts), encoding="utf-8")


def export_from_ti1(ti1_path: Path, descriptor: str | None = None) -> tuple[Path, Path]:
    """Read TI1 and write .txt + .pxf next to it. Returns (txt_path, pxf_path)."""
    patches = parse_ti1(ti1_path)
    desc = descriptor or ti1_path.stem
    txt_out = ti1_path.with_suffix(".txt")
    pxf_out = ti1_path.with_suffix(".pxf")
    write_txt(patches, txt_out, desc)
    write_pxf(patches, pxf_out, desc)
    return txt_out, pxf_out
