/* Link stubs for instrument drivers excluded from the helper build.
 *
 * ex1.c does not compile in Argyll 3.5.0's SALONEINSTLIB mode (it was
 * modernised to the icmErr new_icmMD5() API, sa_conv.h was not). The EX1
 * is a spot-only pocket spectrometer — it cannot read strips, so the
 * chart-reading helper loses nothing by declining it. Returning NULL from
 * the constructor makes new_inst() fail cleanly with "no instrument",
 * and ChromIQ falls back to stock chartread for such devices.
 *
 * AGPL-3.0 — see ../instlib/License.txt. Part of ChromIQ issue #126. */

#ifdef SALONEINSTLIB
#include "sa_config.h"
#else
#include "aconfig.h"
#endif
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "conv.h"
#include "insttypes.h"
#include "icoms.h"
#include "inst.h"
#include "rspec.h"
#include "ex1.h"

ex1 *new_ex1(icoms *icom, instType dtype) {
	(void)icom; (void)dtype;
	a1logd(g_log, 1, "chromiq-chartread: EX1 not supported by this helper\n");
	return NULL;
}
