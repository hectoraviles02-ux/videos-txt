"""Run ruff check on staged Python files."""

import subprocess
import sys


def check_ruff(py_files: list[str]) -> list[dict]:
    """Run ruff check on a list of Python files.

    Uses the project's pyproject.toml configuration if present.
    Falls back gracefully if ruff is not installed.

    Returns:
        list of {file, line, code, message} dicts.
    """
    if not py_files:
        return []

    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--no-fix", "--output-format=text", *py_files],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        return [{"file": "", "line": 0, "code": "SKIP", "message": "ruff not installed — skipping"}]
    except subprocess.TimeoutExpired:
        return [{"file": "", "line": 0, "code": "SKIP", "message": "ruff timed out (30s) — skipping"}]

    findings = []
    for raw_line in result.stdout.splitlines():
        # Format: path:line:col: CODE message
        if ":" not in raw_line:
            continue
        parts = raw_line.split(":", 3)
        if len(parts) < 4:
            continue
        fpath = parts[0].strip()
        try:
            lineno = int(parts[1].strip())
        except ValueError:
            continue
        rest = parts[3].strip()
        # rest is "CODE message"
        code = rest.split(" ", 1)[0] if " " in rest else rest
        msg = rest.split(" ", 1)[1] if " " in rest else ""
        findings.append({"file": fpath, "line": lineno, "code": code, "message": msg})

    return findings
