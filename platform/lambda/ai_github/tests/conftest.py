"""Make modules inside ai_github/ importable by bare name in tests.

AWS Lambda's runtime auto-puts the function root on sys.path, so the
handler does `import helpers, state_jwt, github_app`. Tests mirror that.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
