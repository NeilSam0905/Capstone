"""Put the repo root, scripts/, and tools/ on sys.path for the test suite.

Without this, a bare `pytest tests/` fails at collection with
"No module named 'forecasting'": pytest inserts the *test* directory into
sys.path, not the project root, so `from forecasting.metrics import ...`
does not resolve. `python -m pytest` happens to work because that form
adds the working directory, which is why the difference is easy to miss.

The Part C verification checklist runs the bare form, so it needs to work.

scripts/ and tools/ are added separately: pipeline scripts
(step5_prescriptive.py etc.) and analysis tools (service_frontier.py
etc.) live there, not at the repo root, but tests still do a bare
`import step5_prescriptive` / `import service_frontier` - this keeps
that working without turning either directory into a package or
rewriting every such import.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
