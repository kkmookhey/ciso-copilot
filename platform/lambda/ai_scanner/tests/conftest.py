# platform/lambda/ai_scanner/tests/conftest.py
"""Make modules inside ai_scanner/ importable by bare name in tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
