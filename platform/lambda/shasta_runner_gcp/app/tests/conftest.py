"""Make shasta_runner_gcp/app modules importable by bare name in tests.
At runtime build.sh copies shared modules into app/ (detectors/base.py +
unified_writer.py from ai_scanner; scan_pipeline.py + scan_state.py from
scanner_core); for tests we add those source directories to sys.path so
the bare-name imports resolve."""
import sys
from pathlib import Path

_APP         = Path(__file__).resolve().parent.parent
_LAMBDA_ROOT = _APP.parent.parent
_AI_SCANNER  = _LAMBDA_ROOT / "ai_scanner"
_CORE        = _LAMBDA_ROOT / "scanner_core"

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_AI_SCANNER))
sys.path.insert(0, str(_CORE))
