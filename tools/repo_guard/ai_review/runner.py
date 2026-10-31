"""Run AI review via claude CLI subprocess."""

import subprocess


def run_ai_review(prompt: str, timeout: int = 120) -> dict:
    """Send prompt to claude CLI and return the review.

    Args:
        prompt: the review prompt text
        timeout: max seconds to wait (default 120)

    Returns:
        dict with keys: ok (bool), review (str), error (str|None)
    """
    try:
        result = subprocess.run(
            ["claude", "--print", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        if result.returncode != 0:
            return {
                "ok": False,
                "review": "",
                "error": f"claude CLI exited with code {result.returncode}: {result.stderr.strip()}",
            }

        return {
            "ok": True,
            "review": result.stdout.strip(),
            "error": None,
        }

    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "review": "",
            "error": f"AI review timed out after {timeout}s",
        }
    except FileNotFoundError:
        return {
            "ok": False,
            "review": "",
            "error": "claude CLI not found. Install it or ensure it's in PATH.",
        }
