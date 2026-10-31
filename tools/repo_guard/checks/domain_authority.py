"""Check domain_authority.yaml for drift vs CLAUDE.md and config YAMLs."""

import subprocess
import sys
from pathlib import Path


def check_domain_authority(enabled: bool) -> list[dict]:
    """Run validate_domain_authority.py --strict and return findings.

    Returns:
        list of {file, severity, message} dicts. Empty list = pass.
    """
    if not enabled:
        return []

    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "validate_domain_authority.py"
    if not script.exists():
        return [{
            "file": "scripts/validate_domain_authority.py",
            "severity": "error",
            "message": "Validator script not found at expected path",
        }]

    try:
        result = subprocess.run(
            [sys.executable, str(script), "--strict"],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return [{
            "file": "domain/domain_authority.yaml",
            "severity": "error",
            "message": "Domain authority validator timed out",
        }]

    if result.returncode != 0:
        return [{
            "file": "domain/domain_authority.yaml",
            "severity": "error",
            "message": f"Domain authority drift detected:\n{result.stdout.strip()}",
        }]

    return []
