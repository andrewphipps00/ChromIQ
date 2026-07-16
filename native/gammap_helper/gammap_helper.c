/*
 * chromiq-gammap — ChromIQ's bit-exact gamut-mapping helper.
 *
 * Runs ArgyllCMS's REAL gamut mapper (new_gammap / domap) on a destination
 * colour shell built from a point cloud via Argyll's own gamut library
 * (gamut->expand). Because the mapper operates on the 3-D colour surface and
 * not on ink channels, this handles CMY+N shells that colprof's ICC ink
 * separation refuses — while remaining byte-for-byte Argyll for the maths.
 *
 * ChromIQ builds the source/destination gamuts and the query lattice in
 * CIECAM02 Jab (the appearance space colprof maps in); this helper only runs
 * Argyll's Jab->Jab gamut map and writes the mapped lattice back.
 *
 * The gamut-mapping intent parameters come from Argyll's own
 * xicc_enum_gmapintent(icxPerceptualGMIntent / icxSaturationGMIntent) — the
 * exact call colprof makes — so "bit-exact" means literally Argyll's defaults.
 *
 * Usage:
 *   chromiq-gammap --src SRC.gam (--dst-gam DST.gam | --dst-cloud CLOUD.txt)
 *                  --wp L a b --bp L a b
 *                  --intent p|s --mapres N
 *                  --query QUERY.txt --out OUT.txt
 *
 *   SRC.gam    : source colourspace gamut (Jab), e.g. iccgamut of ClayRGB.
 *   --dst-gam  : destination gamut as an Argyll .gam (Jab) — used for <=4 ink,
 *                where iccgamut can build it, giving the byte-identical gamut
 *                object colprof would feed new_gammap.
 *   --dst-cloud: destination shell as a point cloud, one "L a b" (Jab) per
 *                line — the only route for CMY+N, built via gamut->expand.
 *   --wp/--bp  : destination white/black point (Jab). Required with
 *                --dst-cloud; with --dst-gam they override the file's wb.
 *   --query    : lattice to map, one "L a b" (Jab) per line.
 *   --out      : mapped lattice, one "x y z" per line, in query order.
 *
 * This program is licensed under the GNU Affero General Public License v3,
 * the licence of the ArgyllCMS sources it is built from (see
 * native/argyll/LICENSE).
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <math.h>
#include "aconfig.h"
#include "icc.h"
#include "numlib.h"
#include "conv.h"
#include "xicc.h"
#include "gamut.h"
#include "rspl.h"
#include "gammap.h"
#include "vrml.h"

static void usage(const char *prog) {
	fprintf(stderr,
		"usage: %s --src SRC.gam (--dst-gam DST.gam | --dst-cloud CLOUD.txt) "
		"--wp L a b --bp L a b --intent p|s --mapres N "
		"--query QUERY.txt --out OUT.txt\n", prog);
	exit(2);
}

int main(int argc, char *argv[]) {
	const char *src_path = NULL, *cloud_path = NULL, *dstgam_path = NULL;
	const char *query_path = NULL, *out_path = NULL;
	char intent = 'p';
	int mapres = 33;
	double wp[3], bp[3];
	int have_wp = 0, have_bp = 0;
	char line[512];
	int i;

	error_program = argv[0];

	for (i = 1; i < argc; i++) {
		if (strcmp(argv[i], "--src") == 0 && i + 1 < argc) {
			src_path = argv[++i];
		} else if (strcmp(argv[i], "--dst-cloud") == 0 && i + 1 < argc) {
			cloud_path = argv[++i];
		} else if (strcmp(argv[i], "--dst-gam") == 0 && i + 1 < argc) {
			dstgam_path = argv[++i];
		} else if (strcmp(argv[i], "--query") == 0 && i + 1 < argc) {
			query_path = argv[++i];
		} else if (strcmp(argv[i], "--out") == 0 && i + 1 < argc) {
			out_path = argv[++i];
		} else if (strcmp(argv[i], "--intent") == 0 && i + 1 < argc) {
			intent = argv[++i][0];
		} else if (strcmp(argv[i], "--mapres") == 0 && i + 1 < argc) {
			mapres = atoi(argv[++i]);
		} else if (strcmp(argv[i], "--wp") == 0 && i + 3 < argc) {
			wp[0] = atof(argv[++i]); wp[1] = atof(argv[++i]);
			wp[2] = atof(argv[++i]); have_wp = 1;
		} else if (strcmp(argv[i], "--bp") == 0 && i + 3 < argc) {
			bp[0] = atof(argv[++i]); bp[1] = atof(argv[++i]);
			bp[2] = atof(argv[++i]); have_bp = 1;
		} else {
			usage(argv[0]);
		}
	}
	if (src_path == NULL || query_path == NULL || out_path == NULL)
		usage(argv[0]);
	if ((cloud_path == NULL) == (dstgam_path == NULL))
		error("exactly one of --dst-gam / --dst-cloud is required");
	if (cloud_path != NULL && (!have_wp || !have_bp))
		error("--wp and --bp are required with --dst-cloud");
	if (intent != 'p' && intent != 's')
		error("intent must be 'p' or 's'");
	if (mapres < 2)
		error("mapres must be >= 2");

	/* Source gamut (Jab) from the .gam file. */
	gamut *gin = new_gamut(0.0, 1, 0);      /* isJab = 1 */
	if (gin == NULL || gin->read_gam(gin, (char *)src_path))
		error("reading source gamut %s failed", src_path);

	/* Destination gamut (Jab). <=4 ink: read the iccgamut .gam directly
	 * (byte-identical to colprof's gout). CMY+N: build it from the cloud
	 * via Argyll's own gamut->expand — hand-built meshes fail
	 * vector_isect(); expand() triangulates. */
	gamut *gout = new_gamut(0.0, 1, 0);
	int nc = 0;
	if (dstgam_path != NULL) {
		if (gout->read_gam(gout, (char *)dstgam_path))
			error("reading dest gamut %s failed", dstgam_path);
		if (have_wp && have_bp)
			gout->setwb(gout, wp, bp, NULL);
	} else {
		FILE *cf = fopen(cloud_path, "r");
		if (cf == NULL)
			error("opening cloud %s failed", cloud_path);
		double p[3];
		while (fgets(line, sizeof(line), cf) != NULL) {
			if (sscanf(line, "%lf %lf %lf", &p[0], &p[1], &p[2]) == 3) {
				gout->expand(gout, p);
				nc++;
			}
		}
		fclose(cf);
		if (nc < 4)
			error("cloud %s had only %d points", cloud_path, nc);
		gout->setwb(gout, wp, bp, NULL);
	}

	/* Gamut-mapping intent: Argyll's own defaults, exactly as colprof sets
	 * them (profile/colprof.c). */
	icxGMappingIntent gmi;
	int gm_no = (intent == 's') ? icxSaturationGMIntent
	                            : icxPerceptualGMIntent;
	if (xicc_enum_gmapintent(&gmi, gm_no, NULL) == icxIllegalGMIntent)
		error("xicc_enum_gmapintent failed");

	gammap *map = new_gammap(0, gin, NULL, gout, &gmi, NULL, 0, 0, 0, 0,
	                         mapres, NULL, NULL, NULL);
	if (map == NULL)
		error("new_gammap failed");

	/* Map the query lattice. */
	FILE *qf = fopen(query_path, "r");
	if (qf == NULL)
		error("opening query %s failed", query_path);
	FILE *of = fopen(out_path, "w");
	if (of == NULL)
		error("opening output %s failed", out_path);
	double in[3], out[3];
	int nq = 0;
	while (fgets(line, sizeof(line), qf) != NULL) {
		if (sscanf(line, "%lf %lf %lf", &in[0], &in[1], &in[2]) == 3) {
			map->domap(map, out, in);
			fprintf(of, "%.9f %.9f %.9f\n", out[0], out[1], out[2]);
			nq++;
		}
	}
	fclose(qf);
	fclose(of);
	if (dstgam_path != NULL)
		fprintf(stderr, "mapped %d query points (dst .gam, intent %c, "
		        "mapres %d)\n", nq, intent, mapres);
	else
		fprintf(stderr, "mapped %d query points (%d-point cloud, intent %c, "
		        "mapres %d)\n", nq, nc, intent, mapres);

	map->del(map);
	gin->del(gin);
	gout->del(gout);
	return 0;
}
