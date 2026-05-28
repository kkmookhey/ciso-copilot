"""Make shasta_runner_entra/app modules importable in tests + put
scanner_core/ on the path so framework_registry resolves by bare name
(mirrors the build-time flat copy into app/).

Also adds lambda/ to the path so 'from _shared import ...' resolves
without a Docker build (mirrors the runtime COPY .build/_shared/ path).
"""
import sys
from pathlib import Path

_APP         = Path(__file__).resolve().parent.parent
_LAMBDA_ROOT = _APP.parent.parent
_CORE        = _LAMBDA_ROOT / "scanner_core"
_LAMBDA_DIR  = _LAMBDA_ROOT          # _shared/ lives directly under lambda/

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_CORE))
sys.path.insert(0, str(_LAMBDA_DIR))
