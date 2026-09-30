"""
tools/tier_operating_point.py
------------------------------------------------------------------
Is the service tiering's operating point still the right one, now that the
buffers underneath it are measured correctly?

Why this exists
---------------
The tier thresholds - `DEFAULT_TIER_TARGET = 0.90`,
`DEFAULT_TIER_MIN_EFFICIENCY = 0.10`, `DEFAULT_BUFFER_QUANTILE = 0.80` in
`forecasting/policy.py` - were chosen against buffers computed over a panel
whose last 23 days were step0 zero-fill. `bf08ca7` removed those days, which
moved every buffer (mean safety stock 8.850 -> 13.442) and every tier count
(servable 24 -> 17, not_stockable 14 -> 9). Nobody re-fitted the thresholds
that produce those counts, so the operating point is inherited from a
measurement that no longer exists.

The question is not "can the numbers be improved". It is whether the tiering
sits on the efficient frontier - whether any single population-wide quantile
delivers at least as much service for no more stock. If one does, the
per-SKU machinery is complexity that buys nothing.

The bar, stated before the numbers
----------------------------------
A configuration qualifies only if **no flat quantile dominates it at ANY of
the rolling origins** - 4 of 4, not a majority.

That is deliberately stricter than the gate in
`scripts/validate_policy_holdout.py`, which asks for a majority. The reason is
that this script SEARCHES. A majority bar over four origins means three, and a
search that is allowed to miss one window will find a configuration that
happens to miss the awkward one. The live default already clears 3 of 4 (it is
dominated on the development set by 297 units, 1.5%), so anything this finds
has to be robustly better rather than better on the window the search picked.

A configuration that only closes that 297-unit gap is noise-fitting and is
rejected by this bar, which is the point.

How it measures
---------------
By running `scripts/validate_policy_holdout.py` as a subprocess, once per
configuration, and reading its rolling-origins output.

That is slower than scoring in-process - roughly 13 seconds a configuration -
and it is the right trade anyway. Re-implementing the fit/tier/score
orchestration here would create a second copy of it, and a sweep whose scoring
drifts from the gate's scoring is worse than a slow sweep: it would answer a
question the gate never asks. `forecasting.policy.eligible_population`'s
docstring makes the same argument about two copies of a population loop.

Nothing here writes to the database or to `data/`/`docs/` except its own
output CSV - every holdout run is redirected into a temporary directory.

Run (from the repo root):
    python tools/tier_operating_point.py                  # the full grid
    python tools/tier_operating_point.py --quick          # a 12-point probe
------------------------------------------------------------------
"""
import argparse
import os
import subprocess
import sys
import tempfile
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLDOUT = os.path.join(ROOT, "scripts", "validate_policy_holdout.py")
OUT_CSV = "data/tier_operating_point.csv"

# Around the live defaults, wide enough to show which way each knob pushes
# rather than just whether the current point is a local best.
TIER_TARGETS = [0.75, 0.80, 0.85, 0.90, 0.95]
MIN_EFFICIENCIES = [0.02, 0.05, 0.10, 0.15, 0.20, 0.30]

# buffer_quantile is NOT swept, because in this harness it does nothing.
# A first pass varied it over 0.70-0.85 and produced byte-identical results at
# every tier_target/min_efficiency pair. The reason is in the code:
# validate_policy_holdout.py::fit() used to take `q` and never pass it on (the
# dead parameter is gone now), and
# assign_service_tier()'s `default_q` is reached only on the "no scoreable
# folds" branch, which no SKU here takes - the tiered arm scores at each SKU's
# OWN tier quantile. It is a live knob for step4b/step5, where
# DEFAULT_BUFFER_QUANTILE sets the published buffer, but it is inert for the
# arm this script compares against the frontier. Sweeping it would have
# tripled the runtime to re-measure the same twelve points.
BUFFER_QUANTILE = 0.80

QUICK_TARGETS = [0.85, 0.90]
QUICK_EFFICIENCIES = [0.10, 0.20]

LIVE = (0.90, 0.10, BUFFER_QUANTILE)


def run_one(target, min_eff, buffer_q, tmp, db):
    """One holdout run. Returns its rolling-origins frame, or None if it failed."""
    paths = {k: os.path.join(tmp, f"{k}.csv") for k in
             ("frontier", "origins", "comparison", "origin_frontier")}
    cmd = [sys.executable, HOLDOUT, "--db", db,
           "--tier-target", str(target),
           "--tier-min-efficiency", str(min_eff),
           "--buffer-quantile", str(buffer_q),
           "--out-csv", paths["frontier"],
           "--out-origins-csv", paths["origins"],
           "--out-comparison-csv", paths["comparison"],
           "--out-origin-frontier-csv", paths["origin_frontier"],
           "--out-md", os.path.join(tmp, "holdout.md")]
    # A non-zero exit is expected and carries no information here: the holdout
    # gates on the LIVE operating point, and this script is asking about others.
    # What matters is whether the origins frame came out.
    subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if not os.path.exists(paths["origins"]):
        return None
    df = pd.read_csv(paths["origins"])
    return df if "n_frontier_dominators" in df.columns else None


def summarise(df, target, min_eff, buffer_q):
    n = len(df)
    dominated = df[df["n_frontier_dominators"] > 0]
    clean = n - len(dominated)
    # Demand-weighted, because the origins carry different windows and a plain
    # mean of rates would let the quietest window count as much as the busiest.
    w = df["demand"]
    return {
        "tier_target": target, "min_efficiency": min_eff, "buffer_quantile": buffer_q,
        "origins": n, "clean_origins": clean,
        "qualifies": clean == n,
        "fill_weighted": round(float((df["fill_rate"] * w).sum() / w.sum()), 4),
        "held_total": round(float(df["units_held"].sum()), 1),
        "served_per_held": round(float((df["fill_rate"] * w).sum() / df["units_held"].sum()), 4),
        "dominated_at": ";".join(f"{r.origin}:q{r.dominated_by_q}"
                                 for r in dominated.itertuples()),
        "is_live_default": (target, min_eff, buffer_q) == LIVE,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="ustore.db")
    ap.add_argument("--quick", action="store_true",
                    help="a 12-point probe around the defaults instead of the full grid")
    ap.add_argument("--out-csv", default=OUT_CSV)
    args = ap.parse_args()

    targets = QUICK_TARGETS if args.quick else TIER_TARGETS
    effs = QUICK_EFFICIENCIES if args.quick else MIN_EFFICIENCIES
    grid = [(t, e, BUFFER_QUANTILE) for t in targets for e in effs]

    print("=" * 78)
    print("Service-tier operating point - is the tiering on the efficient frontier?")
    print("=" * 78)
    print(f"{len(grid)} configuration(s). A configuration QUALIFIES only if no flat")
    print("quantile dominates it at ANY origin - 4 of 4, stricter than the holdout's")
    print("majority gate, because this script searches. See the module docstring.")
    print(f"Live default: tier_target={LIVE[0]} min_efficiency={LIVE[1]}. "
          f"buffer_quantile is held at {BUFFER_QUANTILE}")
    print("and not swept - it is inert for this arm (see the module docstring).\n")

    rows, t0 = [], time.time()
    with tempfile.TemporaryDirectory(prefix="tier_sweep_") as tmp:
        for i, (t, e, q) in enumerate(grid, 1):
            df = run_one(t, e, q, tmp, args.db)
            if df is None:
                print(f"  [{i:>3}/{len(grid)}] target={t} eff={e} q={q}  "
                      f"no origins produced - skipped")
                continue
            r = summarise(df, t, e, q)
            rows.append(r)
            mark = "  <- live default" if r["is_live_default"] else ""
            print(f"  [{i:>3}/{len(grid)}] target={t} eff={e} q={q}  "
                  f"clean {r['clean_origins']}/{r['origins']}  "
                  f"fill {r['fill_weighted']:.4f}  held {r['held_total']:>9,.0f}  "
                  f"{'QUALIFIES' if r['qualifies'] else ''}{mark}")

    if not rows:
        print("\nNothing scored - is the database built? Run scripts/step5_prescriptive.py.")
        return 1

    out = pd.DataFrame(rows).sort_values(
        ["qualifies", "served_per_held"], ascending=[False, False])
    out.to_csv(args.out_csv, index=False, lineterminator="\n")

    good = out[out["qualifies"]]
    live = out[out["is_live_default"]]
    print("\n" + "=" * 78)
    print(f"RESULT  ({time.time() - t0:.0f}s, wrote {args.out_csv})")
    print("=" * 78)
    if len(live):
        L = live.iloc[0]
        print(f"  live default: clean {L['clean_origins']}/{L['origins']}  "
              f"fill {L['fill_weighted']:.4f}  held {L['held_total']:,.0f}  "
              f"served/held {L['served_per_held']:.4f}")
    if not len(good):
        print(f"\n  NO configuration clears 4 of 4. The tiering cannot be placed on the")
        print("  frontier at every origin by moving these three knobs.")
        print("  That is an argument about the tiering itself, not about the knobs -")
        print("  and it is the answer, not a reason to lower the bar.")
        best = out.iloc[0]
        print(f"\n  closest: target={best['tier_target']} eff={best['min_efficiency']} "
              f"q={best['buffer_quantile']}  clean {best['clean_origins']}/{best['origins']}"
              f"  dominated at {best['dominated_at'] or '-'}")
        return 1

    print(f"\n  {len(good)} configuration(s) clear 4 of 4:")
    for _, r in good.iterrows():
        print(f"    target={r['tier_target']} eff={r['min_efficiency']} "
              f"q={r['buffer_quantile']}   fill {r['fill_weighted']:.4f}  "
              f"held {r['held_total']:>9,.0f}  served/held {r['served_per_held']:.4f}"
              f"{'   <- live default' if r['is_live_default'] else ''}")
    if len(live) and bool(live.iloc[0]["qualifies"]):
        print("\n  The live default is among them - no change is indicated.")
    else:
        b = good.iloc[0]
        print(f"\n  Best by served-per-held: target={b['tier_target']} "
              f"eff={b['min_efficiency']} q={b['buffer_quantile']}.")
        print("  Changing forecasting/policy.py's defaults to it is a DECISION, not a")
        print("  result - it moves every reorder point. Record the before/after in")
        print("  docs/SERVICE_LEVEL_FRONTIER.md if it is taken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
