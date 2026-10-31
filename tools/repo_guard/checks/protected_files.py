"""Check if staged files include protected paths."""


def check_protected(staged_files: list[str], protected_paths: list[dict]) -> list[dict]:
    """Check staged files against protected path list.

    Args:
        staged_files: list of staged file paths
        protected_paths: list of dicts with keys: path, severity, message

    Returns:
        list of {file, severity, message} dicts.
    """
    findings = []
    protected_map = {p["path"].replace("\\", "/"): p for p in protected_paths}

    for fpath in staged_files:
        normalized = fpath.replace("\\", "/")
        if normalized in protected_map:
            rule = protected_map[normalized]
            findings.append({
                "file": fpath,
                "severity": rule.get("severity", "warn"),
                "message": rule.get("message", f"File {fpath} is protected"),
            })

    return findings
