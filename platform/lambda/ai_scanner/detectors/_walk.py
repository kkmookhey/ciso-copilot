"""Shared file-walker helper for detectors (wraps ripgrep)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def ripgrep(workdir: Path, pattern: str, *, types: list[str] | None = None,
            ignore_case: bool = False) -> list[tuple[Path, int, str]]:
    """Run ripgrep and return (path, line_number, line) tuples."""
    cmd = ["rg", "--no-heading", "--line-number", "--no-color"]
    if ignore_case:
        cmd.append("-i")
    if types:
        for t in types:
            cmd.extend(["-t", t])
    cmd.extend(["--", pattern, str(workdir)])
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode == 1:  # no matches
        return []
    if out.returncode != 0:
        raise RuntimeError(f"ripgrep failed: {out.stderr}")
    results = []
    for line in out.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        path, lineno_str, content = parts
        results.append((Path(path), int(lineno_str), content))
    return results
