"""Put the repo root on sys.path for the test suite.

Without this, a bare `pytest tests/` fails at collection with
"No module named 'forecasting'": pytest inserts the *test* directory into
sys.path, not the project root, so `from forecasting.metrics import ...`
does not resolve. `python -m pytest` happens to work because that form
adds the working directory, which is why the difference is easy to miss.

The Part C verification checklist runs the bare form, so it needs to work.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
