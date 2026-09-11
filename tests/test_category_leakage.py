"""
tests/test_category_leakage.py
------------------------------------------------------------------
Evidence that the fsn_class leak found in scripts/model_benchmark_category.py
(see docs/POOLING_AND_CLUSTERING_EXPERIMENTS.md's correction section) was
real, and evidence that fold_scoped_speed_labels actually closes it - not
just that it runs without error.

The leak: Dim_Product.fsn_class (forecasting.category.speed_label) is
computed by step3_fsn_classification.py from the WHOLE Fact_Sales table,
no date bound. Using it to choose a walk-forward fold's pooling group or
safety-stock class means an early fold's decision is partly determined by
sales that happen after that fold's own origin.

These tests use small synthetic series, not ustore.db, so they run without
a live database and pin the MECHANISM rather than today's specific numbers.
The classifier is a cross-sectional 80th-percentile cutoff, so every test
here uses a pool of "filler" comparator SKUs with known, spread-out ADUS
values - a lone SKU (or two) makes the percentile trivial (self-referential
"fast") and would pass even if the function were broken.
"""
import numpy as np

from forecasting.category import fold_scoped_speed_labels


def _fillers(values, n_days):
    """One SKU per value in `values`, selling at a constant rate on every
    other day for the whole series - a spread-out, stable comparison pool
    so the 80th-percentile cutoff falls in a real gap, not on a tie."""
    out = {}
    for v in values:
        s = np.zeros(n_days)
        s[::2] = v
        out[f"filler{v}"] = s
    return out


def test_fold_scoped_label_can_differ_from_full_history_by_construction():
    """A SKU whose selling rate genuinely changes over time must be able to
    get a DIFFERENT label from a fold-scoped classifier (sees only the
    first half) than from one scoped to the whole series - demonstrated
    directly, with two changing SKUs (one rising, one falling) plus two
    STABLE control SKUs that must NOT flip, proving the flips above are
    the leak's mechanism and not just noise in the function."""
    n_days = 800
    train_end = n_days // 2

    def make(rate_first, rate_second):
        v = np.zeros(n_days)
        v[0:train_end:2] = rate_first
        v[train_end::2] = rate_second
        return v

    series = _fillers(range(1, 21), n_days)   # ADUS 1..20, stable both halves
    series["rises_late"] = make(1.0, 60.0)    # quiet, then a late surge
    series["falls_late"] = make(20.0, 1.0)    # busy, then goes quiet
    series["stays_fast"] = make(20.0, 20.0)
    series["stays_slow"] = make(1.0, 1.0)

    fold_scoped = fold_scoped_speed_labels(series, train_end)
    full_history = fold_scoped_speed_labels(series, n_days)

    assert fold_scoped["rises_late"] == "slow"
    assert full_history["rises_late"] == "fast"
    assert fold_scoped["falls_late"] == "fast"
    assert full_history["falls_late"] == "slow"

    # controls: a rate that never changes must classify the SAME way
    # whether scoped to the first half or the whole series
    assert fold_scoped["stays_fast"] == full_history["stays_fast"] == "fast"
    assert fold_scoped["stays_slow"] == full_history["stays_slow"] == "slow"


def test_fold_scoped_labels_only_see_the_training_window():
    """A SKU with near-nothing before train_end and a huge, frequent burst
    only AFTER it must classify 'slow' when scoped to train_end - a 'fast'
    result here could only happen if the function looked past the origin."""
    n_days = 400
    train_end = 200

    series = _fillers(range(1, 21), n_days)   # ADUS 1..20 comparison pool

    v = np.zeros(n_days)
    v[10] = 1.0
    v[50] = 1.0                    # two token pre-origin sales - ADUS is defined
    v[train_end::2] = 500.0        # a massive, frequent burst, but only after origin
    series["future_bestseller"] = v

    labels = fold_scoped_speed_labels(series, train_end)
    assert labels["future_bestseller"] == "slow", (
        "a SKU with heavy sales ONLY after train_end must not be 'fast' - "
        "a fast label here would mean the function saw past the fold origin"
    )


def test_real_offset_excludes_synthetic_prehistory():
    """The synthetic-augmentation path (--synthetic-years) prepends
    bootstrapped days before the real history. fold_scoped_speed_labels
    must classify using ONLY series[real_offset:train_end] - a wildly
    different synthetic prehistory (near-zero vs. enormous) prepended in
    front of the SAME real pattern must not change the label. Uses a
    filler pool so the assertion is a concrete, non-trivial label rather
    than "the two happen to agree", which a single-SKU quantile would
    satisfy trivially even if real_offset were ignored entirely."""
    real_offset = 2000
    train_end = real_offset + 200

    def build(syn_value):
        real = np.zeros(200)
        real[::4] = 3.0                       # a modest, real, always-on pattern
        syn = np.full(real_offset, syn_value)  # would dominate ADUS if leaked
        return np.concatenate([syn, real])

    series = _fillers(range(1, 6), n_days=550 * 4)   # ADUS 1..5 comparison pool
    series["sku_high_synthetic"] = build(1000.0)
    series["sku_low_synthetic"] = build(0.0)

    labels = fold_scoped_speed_labels(series, train_end, real_offset)
    assert labels["sku_high_synthetic"] == "slow"
    assert labels["sku_low_synthetic"] == "slow"


def test_zero_sales_in_training_window_classifies_slow_not_error():
    """A SKU with no sales at all before train_end has an undefined ADUS
    (0/0) - must fall to 'slow' cleanly, not raise or return NaN/'fast'."""
    n_days = 300
    series = _fillers(range(1, 11), n_days)
    series["dead"] = np.zeros(n_days)

    labels = fold_scoped_speed_labels(series, n_days)
    assert labels["dead"] == "slow"


# ---------------------------------------------------------------------
# The SECOND leak: the safety-stock service class.
#
# forecasting/category.fold_scoped_speed_labels closed the leak in the
# POOLING group. The same full-history Dim_Product.fsn_class was also
# feeding step5_prescriptive.Z_BY_CLASS to size safety stock, on every
# --by path and in scripts/model_benchmark.py and
# scripts/model_benchmark_ml.py, which is the more consequential half:
# fill rate is the project's actual objective metric
# (docs/DEGENERATE_FORECAST.md #21), so a leaked Z contaminated the one
# number B3 is supposed to be decided on.
#
# That half was fixed in scripts/model_benchmark_category.py first and
# NOT in the other two, which left the committed benchmark's fill rates
# computed on a leaked class while the experiment CSVs used the fixed one
# - and then compared the two directly. These tests pin the shared
# implementation all three now call.

from forecasting.category import (
    build_service_class_fn, fold_scoped_service_classes,
)


def test_service_class_is_fold_scoped_not_full_history():
    """The service class must be able to differ between a fold-scoped and
    a full-history view for a SKU whose rate genuinely changes - same
    mechanism as the pooling-label test above, asserted through the
    service-class entry point the benchmark scripts actually call."""
    n_days = 800
    train_end = n_days // 2

    def make(rate_first, rate_second):
        v = np.zeros(n_days)
        v[0:train_end:2] = rate_first
        v[train_end::2] = rate_second
        return v

    series = _fillers(range(1, 21), n_days)
    series["rises_late"] = make(1.0, 60.0)
    series["stays_slow"] = make(1.0, 1.0)

    class_fn = build_service_class_fn(series)

    assert class_fn(train_end)["rises_late"] == "S"
    assert class_fn(n_days)["rises_late"] == "F", (
        "a SKU that only becomes a fast mover AFTER the fold origin must "
        "still read F on the full history - otherwise this test is not "
        "demonstrating a difference at all")
    # control: a rate that never changes gets the same class either way
    assert class_fn(train_end)["stays_slow"] == class_fn(n_days)["stays_slow"] == "S"


def test_service_classes_are_keys_z_by_class_actually_knows():
    """Every label must be a key step5_prescriptive.Z_BY_CLASS recognises.

    This guards a SILENT failure, not a loud one: the lookup at the call
    site is `Z_BY_CLASS.get(label, 0.0)`, so a label drifting to 'fast'/
    'slow' (what fold_scoped_speed_labels returns) instead of 'F'/'S'
    would not raise - it would quietly set every safety stock to zero and
    report a uniformly lower fill rate for every method. A benchmark that
    silently stops buffering is exactly the kind of defect that gets
    written up as a finding about the data.
    """
    from step5_prescriptive import Z_BY_CLASS

    series = _fillers(range(1, 21), n_days=400)
    labels = set(fold_scoped_service_classes(series, 400).values())

    assert labels, "no SKUs classified - the assertion below would be vacuous"
    assert labels <= set(Z_BY_CLASS), (
        f"service classes {sorted(labels)} are not all keys of Z_BY_CLASS "
        f"{sorted(Z_BY_CLASS)} - Z_BY_CLASS.get(..., 0.0) would silently "
        f"zero the safety stock rather than raising")
    assert all(Z_BY_CLASS[lab] > 0 for lab in labels)
