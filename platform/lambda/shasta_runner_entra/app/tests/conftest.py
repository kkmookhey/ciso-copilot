"""Make shasta_runner_entra/app modules importable in tests + put
scanner_core/ on the path so framework_registry resolves by bare name
(mirrors the build-time flat copy into app/).
"""
import sys
from pathlib import Path

_APP         = Path(__file__).resolve().parent.parent
_LAMBDA_ROOT = _APP.parent.parent
_CORE        = _LAMBDA_ROOT / "scanner_core"

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_CORE))
