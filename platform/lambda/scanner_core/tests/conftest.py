"""Put scanner_core/ on sys.path so the package's tests can import its
modules by bare name (`from scan_pipeline import ...`), mirroring how
the modules are imported at runtime once build.sh copies them flat into
a scanner image's app/ directory."""
import sys
from pathlib import Path

_CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_CORE))
