# platform/lambda/ai_scanner/tests/conftest.py
"""Make modules inside ai_scanner/ importable by bare name in tests."""
import sys
from pathlib import Path

# ai_scanner/ itself (detectors, unified_writer, scan_runner, …)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# scanner_core/ siblings (framework_registry, scan_pipeline, scan_state) —
# mirrors what build.sh copies flat into the image's app/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scanner_core"))
