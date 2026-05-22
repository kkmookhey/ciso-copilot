"""Make shasta_runner/app modules importable by bare name in tests.

At runtime in a scanner image, build.sh copies shared modules into
`app/`: `detectors/base.py` + `unified_writer.py` from ai_scanner, and
`scan_pipeline.py` + `scan_state.py` from scanner_core. For tests we add
those source directories to sys.path so the bare-name imports resolve
without needing the build-time copies."""
import sys
from pathlib import Path

_APP         = Path(__file__).resolve().parent.parent
_LAMBDA_ROOT = _APP.parent.parent
_AI_SCANNER  = _LAMBDA_ROOT / "ai_scanner"
_CORE        = _LAMBDA_ROOT / "scanner_core"

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_AI_SCANNER))
sys.path.insert(0, str(_CORE))
