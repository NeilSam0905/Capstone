"""
scripts/search_groupings.py
------------------------------------------------------------------
Would FEWER (or different) categories forecast better? Tries many groupings of
the catalogue - built by merging categories step by step - and scores each one
with the item forecast the app uses, which borrows its category's sales.

Why item accuracy is the yardstick
----------------------------------
Every grouping forecasts the SAME items against the SAME actual sales; only
the category each item borrows from changes, so the comparison is fair. The
category-level error is reported too, but it always improves as groups merge
(a sum of more items is smoother), so it cannot choose how many groups to have.

Keeping the search honest
-------------------------
Trying hundreds of groupings on the same months and keeping the best one
makes the winner look better than it is. So the 12 past 30-day windows are
split in time:
  selection  the older 6 windows - the search sees only these
  test       the newer 6 windows - never used to choose, only to report
A grouping that wins on selection but not on test was luck.

The search
----------
Start from "atoms": products that share BOTH their current category and their
CICS section (scripts/compare_cics_categorization.py), so either scheme can be
rebuilt by merging atoms. Then repeatedly merge the pair of groups whose merge
lowers the selection score most, down to one group for the whole store. Every
step is scored on the test windows. Hand-made groupings (current 12, CICS 17,
CICS with its tiny sections folded in, apparel / non-apparel, whole store) are
scored the same way for comparison.

Speed: the item model (forecasting/topdown.py, 50/50 top-down share + TSB,
calendar-adjusted) needs only a category's 6-month mean, last-30-day total and
calendar multiplier per window, so a grouping is scored from those without
re-running the harness. The fast scorer is checked against the real harness
on the current categories (must match ustore.db).

Outputs (data/):
  grouping_search_path.csv        one row per merge step
  grouping_search_candidates.csv  the hand-made groupings
  grouping_search_best.csv        the selected grouping: group -> atoms

Run:  python scripts/search_groupings.py
------------------------------------------------------------------
"""
import os
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
from forecasting.calendar_adjust import calendar_multiplier
from forecasting.evaluate import aggregate_blocks, make_folds
from forecasting.metrics import naive_scale
from forecasting.topdown import LEVEL_WINDOW, SHARE_WINDOW, TSB_ALPHA, TSB_BETA
from forecasting.intermittent import tsb_fit_predict
import test_history_length as thl
import compare_cics_categorization as ccc

DATA = os.path.join(ROOT, "data")
H = 30
N_SELECT = 6            # older windows used to choose; the rest are the test


# ------------------------------------------------------------ fast scorer
class Scorer:
    def __init__(self, daily, items, types):
        self.T = len(daily)
        self.folds = make_folds(self.T, H, thl.MIN_FOLDS, thl.MAX_FOLDS, thl.MIN_TRAIN)
        self.ends = np.array([f.train_end for f in self.folds])
        self.types = types
        self.items = items
        v = daily[items].to_numpy(float)                       # T x items
        F = len(self.folds)
        self.actual = np.array([[v[f.test_start:f.test_end, j].sum() for f in self.folds]
                                for j in range(len(items))])   # items x F
        self.scale = np.array([[naive_scale(b) if (b := aggregate_blocks(v[:f.train_end, j], H)).size > 1
                                else np.nan for f in self.folds] for j in range(len(items))])
        tsb = tsb_fit_predict(TSB_ALPHA, TSB_BETA)
        self.tsb = np.array([[tsb(v[:f.train_end, j], H)[0] for f in self.folds]
                             for j in range(len(items))])
        self.i30 = np.array([[v[f.train_end - SHARE_WINDOW:f.train_end, j].sum() for f in self.folds]
                             for j in range(len(items))])
        assert self.actual.shape == (len(items), F)

    def group_stats(self, g):
        """(level, last-30 total, calendar multiplier) per window for series g."""
        lvl = np.array([g[max(0, n - LEVEL_WINDOW):n].mean() for n in self.ends])
        c30 = np.array([g[n - SHARE_WINDOW:n].sum() for n in self.ends])
        mult = np.array([calendar_multiplier(g, self.types, n, H) for n in self.ends])
        return lvl, c30, mult

    def item_pred(self, idx, stats):
        lvl, c30, mult = stats
        share = np.divide(self.i30[idx], c30, out=np.zeros_like(self.i30[idx]), where=c30 > 0)
        td = np.maximum(lvl * share, 0.0)
        blend = np.maximum(0.5 * td + 0.5 * self.tsb[idx], 0.0)
        return H * np.maximum(blend * mult, 0.0)

    def cat_pred(self, g, stats):
        lvl, _, mult = stats
        return H * np.maximum(lvl * mult, 0.0)


def item_mase(err, scale, cols):
    """Per-item MASE over the windows in `cols` (NaN when no usable scale)."""
    s = scale[:, cols]
    good = np.isfinite(s) & (s > 0)
    den = np.where(good.any(1), np.nanmean(np.where(good, s, np.nan), axis=1), np.nan)
    return err[:, cols].mean(1) / den


# -------------------------------------------------------------- evaluation
def evaluate(sc, groups, atom_series, atom_of_item, sel, test, valid_sel, valid_test, fast_mask):
    """Score a grouping (list of atom-id lists) on both window sets."""
    group_of_atom = {a: gi for gi, g in enumerate(groups) for a in g}
    pred = np.zeros_like(sc.actual)
    cat_rows = []
    for gi, g in enumerate(groups):
        series = sum(atom_series[a] for a in g)
        stats = sc.group_stats(series)
        idx = np.array([j for j, a in enumerate(atom_of_item) if group_of_atom[a] == gi], dtype=int)
        if idx.size:
            pred[idx] = sc.item_pred(idx, stats)
        ca = np.array([series[f.test_start:f.test_end].sum() for f in sc.folds])
        cs = np.array([naive_scale(b) if (b := aggregate_blocks(series[:f.train_end], H)).size > 1 else np.nan
                       for f in sc.folds])
        cat_rows.append((ca, sc.cat_pred(series, stats), cs))
    err = np.abs(sc.actual - pred)
    out = {}
    for label, cols, valid in (("sel", sel, valid_sel), ("test", test, valid_test)):
        m = item_mase(err, sc.scale, cols)
        out[f"{label}_all_mase"] = np.mean(m[valid])
        out[f"{label}_fast_mase"] = np.mean(m[valid & fast_mask])
        out[f"{label}_slow_mase"] = np.mean(m[valid & ~fast_mask])
        out[f"{label}_item_wmape"] = 100 * err[:, cols].sum() / sc.actual[:, cols].sum()
        ce = [np.abs(a[cols] - p[cols]) for a, p, _ in cat_rows]
        cm = [e.mean() / np.nanmean(s[cols]) for e, (_, _, s) in zip(ce, cat_rows)]
        out[f"{label}_cat_mase"] = float(np.nanmean(cm))
        out[f"{label}_cat_wmape"] = 100 * sum(e.sum() for e in ce) / sum(a[cols].sum() for a, _, _ in cat_rows)
    out["_item_mase_test"] = item_mase(err, sc.scale, test)
    return out


def describe(groups, atom_names):
    return " | ".join(" + ".join(atom_names[a] for a in g) for g in groups)


def main():
    # all-NaN slices (items / groups with no usable scale in a window set) are expected
    warnings.filterwarnings("ignore", category=RuntimeWarning)
    prod, daily, cats, types, stored_cat, stored_item = thl.load()
    placed = [ccc.cics_section(p, n) for p, n in zip(prod.product_id, prod.item_name)]
    prod["cics"] = [s for s, _, _ in placed]
    sold = daily.sum()
    prod["units"] = prod.product_id.map(sold).fillna(0.0)

    # Atoms: current category x CICS section, only those that ever sold.
    prod["atom"] = prod.category + " / " + prod.cics
    atoms = sorted(prod.loc[prod.units > 0, "atom"].unique())
    aid = {a: i for i, a in enumerate(atoms)}
    atom_of_pid = prod.set_index("product_id").atom
    atom_series = []
    for a in atoms:
        pids = [p for p in prod.loc[prod.atom == a, "product_id"] if p in daily.columns]
        atom_series.append(daily[pids].sum(axis=1).to_numpy(float))
    assert abs(sum(s.sum() for s in atom_series) - daily.to_numpy().sum()) < 1e-6

    fsn = prod.set_index("product_id").fsn_class
    items = [p for p in daily.columns if fsn.get(p) in ("F", "S") and sold[p] > 0]
    atom_of_item = [aid[atom_of_pid[p]] for p in items]
    fast_mask = np.array([fsn[p] == "F" for p in items])
    sc = Scorer(daily, items, np.asarray(types))
    F = len(sc.folds)
    sel, test = np.arange(N_SELECT), np.arange(N_SELECT, F)
    print(f"{len(atoms)} atoms, {len(items)} items ({fast_mask.sum()} Fast), {F} windows: "
          f"selection = older {len(sel)}, test = newer {len(test)}")

    def valid_for(cols):
        s = sc.scale[:, cols]
        return (np.isfinite(s) & (s > 0)).any(1)
    valid_sel, valid_test = valid_for(sel), valid_for(test)

    def grouping_from(label_of_atom):
        names = sorted(set(label_of_atom.values()))
        return [[aid[a] for a in atoms if label_of_atom[a] == n] for n in names], names

    # --- self-check: the fast scorer on the CURRENT categories == the real harness.
    cur_groups, cur_names = grouping_from({a: a.split(" / ")[0] for a in atoms})
    group_of_atom = {a: gi for gi, g in enumerate(cur_groups) for a in g}
    pred = np.zeros_like(sc.actual)
    for gi, g in enumerate(cur_groups):
        stats = sc.group_stats(sum(atom_series[a] for a in g))
        idx = np.array([j for j, a in enumerate(atom_of_item) if group_of_atom[a] == gi], dtype=int)
        pred[idx] = sc.item_pred(idx, stats)
    m_all = item_mase(np.abs(sc.actual - pred), sc.scale, np.arange(F))
    chk = pd.Series(m_all, index=items)
    fast_ids = [p for p in items if fsn[p] == "F" and p in stored_item.index]
    gap = (chk[fast_ids] - stored_item[fast_ids]).abs().max()
    print(f"self-check vs ustore.db (Fast item MASE, current categories): max gap {gap:.2e}")
    assert gap < 1e-9, "fast scorer does not reproduce the app's item forecasts"

    ev = lambda g: evaluate(sc, g, atom_series, atom_of_item, sel, test, valid_sel, valid_test, fast_mask)

    # --- hand-made candidates
    fold_in = {"Bottomwear": ccc.SHIRTS, "Jersey": ccc.SHIRTS, "Costumes": ccc.SHIRTS,
               "Vouchers": ccc.GIFT, "Bundles & Gift Sets": ccc.GIFT}
    lto_home = {"Bags": ccc.BAGS, "Stationery": ccc.STATIONERY, "Shirts & Tops": ccc.SHIRTS}

    def cics_merged(a):
        cur, cics = a.split(" / ")
        if cics == ccc.LTO:
            return lto_home.get(cur, ccc.SHIRTS)
        return fold_in.get(cics, cics)
    apparel = {ccc.SHIRTS, ccc.LTO, ccc.BOTTOM, ccc.POLO, ccc.JACKETS, ccc.COSTUME, ccc.JERSEY}
    candidates = {
        "Current categories": {a: a.split(" / ")[0] for a in atoms},
        "CICS sections": {a: a.split(" / ")[1] for a in atoms},
        "CICS, tiny sections folded in": {a: cics_merged(a) for a in atoms},
        "Apparel / non-apparel": {a: "Apparel" if a.split(" / ")[1] in apparel else "Non-apparel" for a in atoms},
        "Whole store as one group": {a: "All" for a in atoms},
        "Finest (every atom its own group)": {a: a for a in atoms},
    }
    cand_rows, cand_err = [], {}
    for label, mapping in candidates.items():
        g, names = grouping_from(mapping)
        r = ev(g)
        cand_err[label] = r.pop("_item_mase_test")
        cand_rows.append(dict(grouping=label, n_groups=len(g), **r))
    cand = pd.DataFrame(cand_rows)

    # --- greedy merge path from the atoms, chosen on the selection windows only
    groups = [[i] for i in range(len(atoms))]
    stats = [sc.group_stats(atom_series[i]) for i in range(len(atoms))]
    series = [atom_series[i].copy() for i in range(len(atoms))]
    members = [np.array([j for j, a in enumerate(atom_of_item) if a == i], dtype=int) for i in range(len(atoms))]
    sel_valid_idx = valid_sel

    def sel_mase_of(idx, st):
        if idx.size == 0:
            return np.array([])
        e = np.abs(sc.actual[idx][:, sel] - sc.item_pred(idx, st)[:, sel])
        s = sc.scale[idx][:, sel]
        good = np.isfinite(s) & (s > 0)
        den = np.where(good.any(1), np.nanmean(np.where(good, s, np.nan), axis=1), np.nan)
        m = e.mean(1) / den
        return np.where(sel_valid_idx[idx], m, 0.0)

    contrib = [sel_mase_of(members[i], stats[i]).sum() for i in range(len(atoms))]
    n_valid = int(sel_valid_idx.sum())
    path = []

    def record(step):
        r = ev(groups)
        r.pop("_item_mase_test")
        path.append(dict(step=step, n_groups=len(groups), **r,
                         groups=describe(groups, atoms)))

    record(0)
    step = 0
    while len(groups) > 1:
        best = None
        base = sum(contrib)
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                s_ij = series[i] + series[j]
                st = sc.group_stats(s_ij)
                idx = np.concatenate([members[i], members[j]])
                c = sel_mase_of(idx, st).sum()
                total = base - contrib[i] - contrib[j] + c
                if best is None or total < best[0] - 1e-12:
                    best = (total, i, j, st, s_ij, idx, c)
        _, i, j, st, s_ij, idx, c = best
        groups[i] = groups[i] + groups[j]; series[i] = s_ij; stats[i] = st
        members[i] = idx; contrib[i] = c
        for lst in (groups, series, stats, members, contrib):
            del lst[j]
        step += 1
        record(step)
        if step % 5 == 0:
            print(f"  merged down to {len(groups)} groups")
    path = pd.DataFrame(path)
    path.to_csv(os.path.join(DATA, "grouping_search_path.csv"), index=False, lineterminator="\n")

    # --- pick on SELECTION only, then read its test score
    pick = path.loc[path.sel_all_mase.idxmin()]
    # rebuild the picked grouping to compare item by item with the current one on the test windows
    picked_groups = [[aid[a] for a in grp.split(" + ")] for grp in pick.groups.split(" | ")]
    r = ev(picked_groups)
    pe = r.pop("_item_mase_test")
    base_e = cand_err["Current categories"]
    ok = valid_test & np.isfinite(pe) & np.isfinite(base_e)
    d = pe[ok] - base_e[ok]
    rng = np.random.default_rng(0)
    boot = np.array([d[rng.integers(0, d.size, d.size)].mean() for _ in range(4000)])
    lo, hi = np.percentile(boot, [2.5, 97.5])

    cand = pd.concat([cand, pd.DataFrame([dict(grouping=f"Search pick ({int(pick.n_groups)} groups)",
                                               n_groups=int(pick.n_groups), **r)])], ignore_index=True)
    cand["test_vs_current_ci_lo"] = np.nan
    cand["test_vs_current_ci_hi"] = np.nan
    cand.loc[cand.index[-1], ["test_vs_current_ci_lo", "test_vs_current_ci_hi"]] = [lo, hi]
    cand.to_csv(os.path.join(DATA, "grouping_search_candidates.csv"), index=False, lineterminator="\n")

    best_rows = []
    for gi, grp in enumerate(pick.groups.split(" | "), start=1):
        for a in grp.split(" + "):
            best_rows.append(dict(group=gi, atom=a, units=float(atom_series[aid[a]].sum()),
                                  products=int((prod.atom == a).sum())))
    pd.DataFrame(best_rows).to_csv(os.path.join(DATA, "grouping_search_best.csv"), index=False,
                                   lineterminator="\n")

    pd.set_option("display.width", 250)
    cols = ["n_groups", "sel_all_mase", "test_all_mase", "test_fast_mase", "test_slow_mase",
            "test_item_wmape", "test_cat_mase", "test_cat_wmape"]
    print("\n=== hand-made groupings ===")
    print(cand[["grouping"] + cols].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print("\n=== merge path (every 3rd step) ===")
    print(path[cols].iloc[::3].to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    print(f"\npicked on selection: {int(pick.n_groups)} groups; test item MASE {r['test_all_mase']:.3f} vs current "
          f"{cand.loc[cand.grouping == 'Current categories', 'test_all_mase'].iloc[0]:.3f}; "
          f"difference {d.mean():+.4f} [95% CI {lo:+.4f}, {hi:+.4f}]")
    print("groups:", pick.groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
