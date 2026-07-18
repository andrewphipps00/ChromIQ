/* Compatibility shim for the vendored Argyll instrument library (#126).
 *
 * The non-macOS USB/HID backends (usbio_lx.c, usbio_w0.c, usbio_dk.c,
 * hidio.c) read a device's USB string descriptor and convert it from
 * UTF-16 to UTF-8 with two symbols from Argyll's icc library:
 * `icmUTF16` and `icmUTF16toUTF8()`. The macOS backend (usbio_ox.c) uses
 * IOKit instead and needs neither — which is why the mac build links
 * without the icc library while Linux/Windows do not.
 *
 * Rather than vendor the whole 4600-line icc_util.c, ChromIQ provides
 * these two symbols here (our own code, so the vendored Argyll sources
 * stay byte-identical). Force-included into the instlib compile via CMake.
 *
 * AGPL-3.0 — see ../instlib/License.txt. Part of ChromIQ issue #126. */
#ifndef CHROMIQ_UTF_COMPAT_H
#define CHROMIQ_UTF_COMPAT_H

#include <stddef.h>
#include <stdint.h>

#ifndef CHROMIQ_ICMUTF16_DEFINED
#define CHROMIQ_ICMUTF16_DEFINED
typedef uint16_t icmUTF16;      /* matches Argyll's `typedef ORD16 icmUTF16` */
#endif

/* Convert a NUL-terminated native-endian UTF-16 string to UTF-8.
 * Signature matches Argyll's icc_util.c: pillegal is ignored (callers pass
 * NULL). If `out` is NULL, returns the byte count the UTF-8 needs (excluding
 * the terminator); otherwise writes the UTF-8 (NUL-terminated) and returns
 * the same count. */
size_t icmUTF16toUTF8(void *pillegal, unsigned char *out, icmUTF16 *in);

#endif /* CHROMIQ_UTF_COMPAT_H */
