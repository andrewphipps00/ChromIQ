"""Argyll gammap intent weight tables (gammap.c, ArgyllCMS 3.5.0).

Extracted programmatically for the gammap port (P4b, issue #122).
The gammapweights struct layout (nearsmth.h): cusp align (l,c,h),
twist power, chroma expansion; radial (weight, hue-dom, l-dom);
absolute (weight, hue-dom, white-l-dom, grey-l-dom, black-l-dom,
white-blend-start, black-blend-pow, l-power, l-xover); relative
(smooth-L, smooth-H, degree); depth (compression, expansion);
fine (expansion weight). -1 = inherit from default entry.
"""

PERCEPTUAL_WEIGHTS = [
    ('gmm_default', [0.1, 0.0, 0.2, 2.0, 1.0, 0.0, 0.5, 0.5, 1.0, 0.8, 0.8, 0.45, 0.94, 0.4, 0.7, 1.5, 10.0, 20.0, 30.0, 0.9, 5.0, 5.0, 0.0]),
    ('gmm_light_yellow', [0.9, 0.8, 0.7, 4.0, 1.2, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 20.0, 10.0, 0.5, -1.0, -1.0, 0.5]),
]

SATURATION_WEIGHTS = [
    ('gmm_default', [0.6, 0.5, 0.6, 1.0, 1.05, 0.0, 0.5, 0.5, 1.0, 0.4, 0.6, 0.3, 0.7, 0.5, 1.0, 1.5, 20.0, 15.0, 20.0, 0.8, 5.0, 5.0, 0.5]),
    ('gmm_light_yellow', [1.0, 1.0, 1.0, 1.0, 1.2, -1.0, -1.0, -1.0, 1.0, 0.3, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, -1.0, 10.0, 15.0, 0.5, -1.0, -1.0, -1.0]),
]

PSMOOTH = 2.0   # rspl smoothing level for perceptual
XVRA = 3.0      # mapping vertex ratio over gamut tri verts
