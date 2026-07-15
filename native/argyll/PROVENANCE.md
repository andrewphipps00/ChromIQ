# Vendored ArgyllCMS sources

These files are an unmodified subset of **ArgyllCMS 3.5.0** by Graeme W. Gill,
copied verbatim from the official source distribution. They are licensed under
the **GNU Affero General Public License v3** (see LICENSE).

- Upstream: https://www.argyllcms.com/  (Argyll_V3.5.0)
- Subset: the 45 translation units + 114 headers required to build
  ChromIQ's bit-exact gamut-mapping helper (numlib, icc, cgats, rspl,
  gamut, xicc, plus spectro/conv and plot/vrml).
- No source changes. To bump: re-copy the same file list from the new
  Argyll release and re-verify the helper builds byte-identical.
