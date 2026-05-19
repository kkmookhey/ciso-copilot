"""Make shasta_runner/app modules importable by bare name in tests.

At runtime in Lambda, build.sh copies `detectors/base.py` and
`unified_writer.py` from ai_scanner into `app/`. For tests we add
ai_scanner's directory to sys.path so the bare-name imports
(`from detectors.base import ...`, `import unified_writer`) resolve
without needing the copy."""
import sys
from pathlib import Path

_APP        = Path(__file__).resolve().parent.parent
_AI_SCANNER = _APP.parent.parent / "ai_scanner"

sys.path.insert(0, str(_APP))
sys.path.insert(0, str(_AI_SCANNER))
