"""Map device values to a displayable sRGB-ish triple for the TIFF raster.

ChromIQ profiles **RGB** printers, so RGB / CMY (stored as RGB) render exactly.
Gray and CMYK are converted for a faithful *visual* chart; their measured
device values in the ``.ti2`` are always exact regardless of this preview.
True CMYK/DeviceN raster output (a CMYK TIFF) is a later addition.
"""
from __future__ import annotations


def to_display_rgb(device: tuple[float, ...], color_rep: str) -> tuple[int, int, int]:
    """Device values (0–100) → 8-bit (R, G, B) for rendering."""
    rep = color_rep.upper()

    def clamp(v: float) -> int:
        return max(0, min(255, round(v)))

    if rep in ("RGB", "IRGB") and len(device) == 3:
        # Stored RGB is the printable RGB (CMY targets are stored as RGB too).
        return tuple(clamp(c / 100.0 * 255.0) for c in device)  # type: ignore[return-value]

    if rep == "W" and len(device) == 1:
        v = clamp(device[0] / 100.0 * 255.0)
        return (v, v, v)

    if rep.startswith("CMYK") and len(device) >= 4:
        c, m, y, k = (d / 100.0 for d in device[:4])
        r = 255.0 * (1.0 - c) * (1.0 - k)
        g = 255.0 * (1.0 - m) * (1.0 - k)
        b = 255.0 * (1.0 - y) * (1.0 - k)
        return (clamp(r), clamp(g), clamp(b))

    if rep in ("CMY",) and len(device) == 3:
        c, m, y = (d / 100.0 for d in device)
        return (clamp(255.0 * (1.0 - c)), clamp(255.0 * (1.0 - m)), clamp(255.0 * (1.0 - y)))

    # Fallback: first channel as grey.
    v = clamp((device[0] if device else 0.0) / 100.0 * 255.0)
    return (v, v, v)


def luminance(rgb: tuple[int, int, int]) -> float:
    """Rec.709 relative luminance (0–255)."""
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def to_device_approx(rgb: tuple[int, int, int],
                     device_fields: list[str]) -> tuple[float, ...]:
    """Approximate device ink values (0–100) for a *display* colour.

    Used for **unmeasured** furniture whose colour carries meaning — chiefly the
    contrast spacers, so a red/yellow separator prints red/yellow on a CMYK+N
    device instead of collapsing to grey (matching printtarg's coloured spacers).
    Achromatic colours route to the single black ink (clean, low ink); chromatic
    colours invert into C/M/Y. Extra inks (O/G/V/light) stay 0 — approximate by
    design, never applied to a patch that gets measured.
    """
    r, g, b = (v / 255.0 for v in rgb)
    suf = [f.split("_")[-1].upper() for f in device_fields]
    out = [0.0] * len(suf)
    mx, mn = max(r, g, b), min(r, g, b)
    if (mx - mn) < 0.06 and "K" in suf:          # near-neutral → black ink only
        out[suf.index("K")] = 100.0 * (1.0 - mx)
        return tuple(out)
    for i, s in enumerate(suf):                  # chromatic → naive CMY inversion
        if s == "C":
            out[i] = 100.0 * (1.0 - r)
        elif s == "M":
            out[i] = 100.0 * (1.0 - g)
        elif s == "Y":
            out[i] = 100.0 * (1.0 - b)
    return tuple(out)


def to_device_approx_array(rgb, device_fields: list[str]):
    """Vectorised :func:`to_device_approx` over an ``(H, W, 3)`` uint8 image →
    ``(H, W, n)`` float device values (0–100).

    Used to carry a *rendered* colour region — chiefly the notes/clip strip —
    into the device raster so its artwork prints in colour (a CMY(K)
    approximation; extra inks stay 0) instead of flat black. Near-neutral pixels
    route to the black ink; black text therefore stays crisp K.
    """
    import numpy as np

    r = rgb[..., 0].astype(np.float32) / 255.0
    g = rgb[..., 1].astype(np.float32) / 255.0
    b = rgb[..., 2].astype(np.float32) / 255.0
    suf = [f.split("_")[-1].upper() for f in device_fields]
    n = len(suf)
    out = np.zeros(r.shape + (n,), dtype=np.float32)
    for i, s in enumerate(suf):                  # chromatic → CMY inversion
        if s == "C":
            out[..., i] = 100.0 * (1.0 - r)
        elif s == "M":
            out[..., i] = 100.0 * (1.0 - g)
        elif s == "Y":
            out[..., i] = 100.0 * (1.0 - b)
    if "K" in suf:                               # near-neutral → single K ink
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        ach = (mx - mn) < 0.06
        out[ach] = 0.0
        ki = suf.index("K")
        out[..., ki] = np.where(ach, 100.0 * (1.0 - mx), out[..., ki])
    return out
