"""Make shasta_runner/app modules importable by bare name in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
