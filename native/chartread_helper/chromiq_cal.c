/* JSON-mode calibration handler for chromiq-chartread.
 *
 * A faithful port of instappsup.c's inst_handle_calibrate() state machine
 * (Argyll 3.5.0, GPLv2+) with the two console touch points replaced:
 * prompts become cal_* JSON events, and "hit any key" waits consume the
 * command channel instead of stdin characters. The calibrate()/calt/calc
 * loop is IDENTICAL — only the user-interaction edge differs.
 *
 * AGPL-3.0 for the combination — see ../instlib/License.txt.
 * Part of ChromIQ issue #126. */

#ifdef SALONEINSTLIB
#include "sa_config.h"
#else
#include "aconfig.h"
#endif
#include <stdio.h>
#include <string.h>
#include "numsup.h"
#include "cgats.h"
#include "xspect.h"
#include "conv.h"
#include "insttypes.h"
#include "icoms.h"
#include "inst.h"

#include "chromiq_ext.h"

/* Short machine-readable name for each calibration condition, plus the
 * human sentence chartread would have printed. The GUI shows its own
 * translated text keyed on `cond`; `text` is a debugging aid. */
static const char *cq_calc_name(inst_cal_cond calc) {
	switch (calc & inst_calc_cond_mask) {
		case inst_calc_uop_ref_white:   return "uop_ref_white";
		case inst_calc_uop_trans_white: return "uop_trans_white";
		case inst_calc_uop_trans_dark:  return "uop_trans_dark";
		case inst_calc_man_ref_white:   return "man_ref_white";
		case inst_calc_man_ref_whitek:  return "man_ref_whitek";
		case inst_calc_man_ref_dark:    return "man_ref_dark";
		case inst_calc_man_dark_gloss:  return "man_dark_gloss";
		case inst_calc_man_em_dark:     return "man_em_dark";
		case inst_calc_man_am_dark:     return "man_am_dark";
		case inst_calc_man_cal_smode:   return "man_cal_smode";
		case inst_calc_man_trans_white: return "man_trans_white";
		case inst_calc_man_trans_dark:  return "man_trans_dark";
		case inst_calc_change_filter:   return "change_filter";
		case inst_calc_message:         return "message";
		case inst_calc_emis_white:      return "emis_white";
		case inst_calc_emis_80pc:       return "emis_80pc";
		case inst_calc_emis_grey:
		case inst_calc_emis_grey_darker:
		case inst_calc_emis_grey_ligher: return "emis_grey";
		default:                        return "unknown";
	}
}

inst_code cq_handle_calibrate(inst *p, inst_cal_type calt, inst_cal_cond calc,
	int doimmediately) {
	inst_code ev;
	int usermes = 0;
	inst_calc_id_type idtype;
	char id[200];
	int ch;

	a1logd(p->log, 1, "cq_handle_calibrate called\n");
	p->last_cal_ec = 0;

	for (;;) {
		ev = p->calibrate(p, &calt, &calc, &idtype, id);

		if ((ev & inst_mask) == inst_ok) {
			if ((calc & inst_calc_cond_mask) == inst_calc_message) {
				char esc[256];
				cq_json_escape(esc, sizeof(esc), id);
				cq_emit_raw("{\"event\":\"cal_message\",\"text\":\"%s\"}", esc);
			}
			if (usermes)
				cq_emit_simple("cal_done");
			return ev;
		}

		if ((ev & inst_mask) == inst_user_abort)
			return ev;

		if ((ev & inst_mask) != inst_cal_setup) {
			if ((ev & inst_mask) == inst_unsupported)
				return inst_unsupported;

			cq_emit_error("cal_failed", p->inst_interp_error(p, ev));
			p->last_cal_ec = ev;

			if (doimmediately)
				return inst_user_abort;

			/* Wait for retry (any command) or quit */
			ch = cq_wait_char();
			if (ch == 0x1b || ch == 0x3 || ch == 'q' || ch == 'Q')
				return inst_user_abort;

		} else {
			char esc[256];
			cq_json_escape(esc, sizeof(esc), id);
			cq_emit_raw(
				"{\"event\":\"cal_required\",\"cond\":\"%s\",\"id\":\"%s\","
				"\"optional\":%s}",
				cq_calc_name(calc), esc,
				(calc & inst_calc_optional_flag) ? "true" : "false");
			usermes = 1;

			/* Identical wait rule to upstream: no wait when immediate, or
			 * when the condition is click-on-tile (whitek). */
			if (!doimmediately
			 && (calc & inst_calc_cond_mask) != inst_calc_man_ref_whitek) {
				ch = cq_wait_char();
				if ((calc & inst_calc_optional_flag) != 0 && (ch == 's' || ch == 'S')) {
					cq_emit_simple("cal_skipped");
					goto oloop;
				}
				if (ch == 0x1b || ch == 0x3 || ch == 'q' || ch == 'Q')
					return inst_user_abort;
			}
			calc &= inst_calc_cond_mask;
		}
 oloop:;
	}
}
