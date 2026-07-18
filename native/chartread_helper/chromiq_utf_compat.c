/* icmUTF16toUTF8 for the vendored instrument library — see the header.
 * A self-contained UTF-16 → UTF-8 converter (BMP + surrogate pairs),
 * behaviourally equivalent to Argyll's icc_util.c for the USB
 * serial-number strings the drivers feed it. ChromIQ code (issue #126),
 * AGPL-3.0. */
#include "chromiq_utf_compat.h"

size_t icmUTF16toUTF8(void *pillegal, unsigned char *out, icmUTF16 *in) {
	size_t n = 0;
	(void)pillegal;

	for (;;) {
		uint32_t ch = *in++;
		int nb;

		if (ch == 0)
			break;

		/* Combine a valid high/low surrogate pair into one code point. */
		if (ch >= 0xD800 && ch <= 0xDBFF) {
			uint32_t lo = *in;
			if (lo >= 0xDC00 && lo <= 0xDFFF) {
				ch = 0x10000 + ((ch - 0xD800) << 10) + (lo - 0xDC00);
				in++;
			} else {
				ch = 0xFFFD;    /* stranded high surrogate */
			}
		} else if (ch >= 0xDC00 && ch <= 0xDFFF) {
			ch = 0xFFFD;        /* unexpected low surrogate */
		}

		if (ch < 0x80)          nb = 1;
		else if (ch < 0x800)    nb = 2;
		else if (ch < 0x10000)  nb = 3;
		else                    nb = 4;

		if (out != NULL) {
			switch (nb) {
			case 1:
				out[n] = (unsigned char)ch;
				break;
			case 2:
				out[n]     = (unsigned char)(0xC0 | (ch >> 6));
				out[n + 1] = (unsigned char)(0x80 | (ch & 0x3F));
				break;
			case 3:
				out[n]     = (unsigned char)(0xE0 | (ch >> 12));
				out[n + 1] = (unsigned char)(0x80 | ((ch >> 6) & 0x3F));
				out[n + 2] = (unsigned char)(0x80 | (ch & 0x3F));
				break;
			default:
				out[n]     = (unsigned char)(0xF0 | (ch >> 18));
				out[n + 1] = (unsigned char)(0x80 | ((ch >> 12) & 0x3F));
				out[n + 2] = (unsigned char)(0x80 | ((ch >> 6) & 0x3F));
				out[n + 3] = (unsigned char)(0x80 | (ch & 0x3F));
				break;
			}
		}
		n += (size_t)nb;
	}

	if (out != NULL)
		out[n] = '\0';
	return n;
}
