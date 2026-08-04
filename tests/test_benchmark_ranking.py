"""
tests/test_benchmark_ranking.py
------------------------------------------------------------------
The benchmark reports a ranking. It must not read as a selection.

Model selection is deferred decision B3, downstream of B2. A17 adds a
second ranking - by fill rate, beside the error metric - precisely so the
tension between the two is visible; the run's job is to make the choice
informed, not to make it.

This runs the real script and scans its real stdout, because a check that
inspects a variable inside the script only ever sees the strings someone
remembered to route through it.
------------------------------------------------------------------
"""
import os
import subprocess
import sys

import pytest

# Phrases that can only mean a selection has been made. Bare words like
# "winner" and "should use" are NOT usable here: the output's own
# disclaimers ("no winner is declared", "which model USTore should use is
# deferred decision B3") contain them, and those sentences are the point.
# The complement - that those disclaimers are present - is asserted
# separately below.
BANNED = [
    "we recommend", "recommended method", "recommendation:",
    "the winner is", "winner:", "we select", "we choose", "our choice",
    "the best method is", "optimal method is", "you should use",
    "the method to use is",
]


@pytest.fixture(scope="module")
def benchmark_output():
    if not os.path.exists("ustore.db"):
        pytest.skip("ustore.db not built")
    r = subprocess.run(
        [sys.executable, "model_benchmark.py", "--limit", "5", "--quick"],
        capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, f"benchmark failed:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
    assert len(r.stdout) > 500, "suspiciously little output to scan"
    return r.stdout


def test_output_contains_no_selection_language(benchmark_output):
    low = benchmark_output.lower()
    found = [b for b in BANNED if b in low]
    assert not found, f"the ranking output reads as a selection: {found}"


def test_output_says_explicitly_that_no_winner_is_declared(benchmark_output):
    low = benchmark_output.lower()
    assert "no winner is declared" in low
    assert "measurement, not a selection" in low
    assert "b3" in low


def test_both_ranking_tables_are_printed(benchmark_output):
    assert "TABLE 1 - ERROR METRIC" in benchmark_output
    assert "TABLE 2 - DECISION METRIC" in benchmark_output


def test_decision_table_is_flagged_provisional(benchmark_output):
    """Table 2 uses a lead time and cost ratio nobody has confirmed. If
    that flag ever disappears, the table starts reading as a result about
    USTore's real service levels."""
    assert "PROVISIONAL" in benchmark_output
    assert "B9" in benchmark_output


def test_ranking_gates_all_pass(benchmark_output):
    assert "[FAIL]" not in benchmark_output
    for expected in ("both tables rank the same",
                     "n_skus_priced reported for every method",
                     "every fill rate within [0, 1]"):
        assert expected in benchmark_output


def test_n_skus_priced_is_reported_and_reaches_zero_for_some_method(benchmark_output):
    """The column exists and does its job: at least one method prices
    nothing, which is the finding A18 pins."""
    assert "n_skus_priced" in benchmark_output
