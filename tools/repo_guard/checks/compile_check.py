"""Check that staged Python files compile without syntax errors."""

import os
import py_compile
import subprocess
import tempfile


def _get_staged_content(file_path: str) -> str | None:
    """Read file content from git staging area (index)."""
    result = subprocess.run(
        ["git", "show", f":{file_path}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout.lstrip("\ufeff")


def check_compile(staged_files: list[str]) -> list[dict]:
    """Run py_compile on each staged .py file.

    Returns list of {file, ok, error} dicts.
    """
    results = []
    for fpath in staged_files:
        if not fpath.endswith(".py"):
            continue

        content = _get_staged_content(fpath)
        if content is None:
            results.append({"file": fpath, "ok": False, "error": "Could not read staged content"})
            continue

        # Write to temp file for py_compile
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".py")
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                tmp.write(content)

            py_compile.compile(tmp_path, doraise=True)
            results.append({"file": fpath, "ok": True, "error": None})
        except py_compile.PyCompileError as e:
            # Replace temp path with real path in error message
            error_msg = str(e).replace(tmp_path, fpath)
            results.append({"file": fpath, "ok": False, "error": error_msg})
        finally:
            os.unlink(tmp_path)

    return results
