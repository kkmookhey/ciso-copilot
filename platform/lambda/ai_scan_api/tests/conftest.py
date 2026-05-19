"""Make modules inside ai_scan_api/ importable by bare name in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
