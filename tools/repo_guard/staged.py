"""Detect staged files from git index."""

import subprocess


def get_staged_files(filter_ext: str | None = None) -> list[str]:
    """Return list of staged file paths (added, copied, modified).

    Args:
        filter_ext: optional extension filter, e.g. ".py"
    """
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
    if filter_ext:
        files = [f for f in files if f.endswith(filter_ext)]
    return files


def get_staged_diff(file_path: str | None = None) -> str:
    """Return unified diff of staged changes."""
    cmd = ["git", "diff", "--cached"]
    if file_path:
        cmd += ["--", file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.stdout
