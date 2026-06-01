# platform/lambda/ai_scanner/tests/conftest.py
"""Make modules inside ai_scanner/ importable by bare name in tests."""
import sys
from pathlib import Path

# ai_scanner/ itself (detectors, unified_writer, scan_runner, …)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# scanner_core/ siblings (framework_registry, scan_pipeline, scan_state) —
# mirrors what build.sh copies flat into the image's app/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scanner_core"))

# _shared/ — parent of ai_scanner/ so that `from _shared import broadcast_fanout`
# resolves in tests the same way it does at Lambda runtime (where build.sh
# copies _shared/broadcast_fanout.py into the image alongside the app code).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
