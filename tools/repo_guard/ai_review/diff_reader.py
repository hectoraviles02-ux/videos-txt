"""Read git diffs for AI review."""

import subprocess


def get_staged_diff() -> str:
    """Return the full staged diff."""
    result = subprocess.run(
        ["git", "diff", "--cached"],
        capture_output=True,
        text=True,
    )
    return result.stdout


def get_ref_diff(ref_range: str) -> str:
    """Return diff between two refs (e.g. 'main..HEAD').

    Args:
        ref_range: git ref range like 'main..HEAD' or 'abc123..def456'
    """
    result = subprocess.run(
        ["git", "diff", ref_range],
        capture_output=True,
        text=True,
    )
    return result.stdout
