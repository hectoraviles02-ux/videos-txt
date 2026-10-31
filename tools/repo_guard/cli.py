"""CLI entry point for repo_guard."""

import argparse
import sys

from repo_guard import __version__
from repo_guard.ai_review.diff_reader import get_ref_diff, get_staged_diff
from repo_guard.ai_review.prompt import build_prompt
from repo_guard.ai_review.runner import run_ai_review
from repo_guard.checks.compile_check import check_compile
from repo_guard.checks.domain_authority import check_domain_authority
from repo_guard.checks.forbidden import check_forbidden
from repo_guard.checks.protected_files import check_protected
from repo_guard.config import load_config
from repo_guard.staged import get_staged_files

# ANSI colors (only if terminal supports it)
_COLOR = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
RED = "\033[91m" if _COLOR else ""
YELLOW = "\033[93m" if _COLOR else ""
GREEN = "\033[92m" if _COLOR else ""
BOLD = "\033[1m" if _COLOR else ""
RESET = "\033[0m" if _COLOR else ""


def _print_header(text: str) -> None:
    print(f"\n{BOLD}=== {text} ==={RESET}")


def _severity_color(severity: str) -> str:
    if severity in ("error", "block"):
        return RED
    if severity == "warn":
        return YELLOW
    return ""


def cmd_check(args: argparse.Namespace) -> int:
    """Run all deterministic checks. Returns 0 if pass, 1 if fail."""
    config = load_config()
    checks_cfg = config.get("checks", {})

    if args.staged:
        staged = get_staged_files()
    else:
        print(f"{YELLOW}Warning: --staged not set. No files to check.{RESET}")
        return 0

    if not staged:
        print(f"{GREEN}No staged files to check.{RESET}")
        return 0

    print(f"{BOLD}repo_guard v{__version__} — checking {len(staged)} staged file(s){RESET}")

    has_errors = False
    has_blocks = False

    # 1. Compile check
    if checks_cfg.get("compile", {}).get("enabled", True):
        py_files = [f for f in staged if f.endswith(".py")]
        if py_files:
            _print_header("Compile Check")
            results = check_compile(py_files)
            for r in results:
                if r["ok"]:
                    print(f"  {GREEN}OK{RESET}  {r['file']}")
                else:
                    has_errors = True
                    print(f"  {RED}FAIL{RESET} {r['file']}: {r['error']}")

    # 2. Forbidden patterns
    forbidden_cfg = checks_cfg.get("forbidden_patterns", {})
    if forbidden_cfg.get("enabled", True):
        patterns = forbidden_cfg.get("patterns", [])
        if patterns:
            _print_header("Forbidden Patterns")
            findings = check_forbidden(staged, patterns)
            if not findings:
                print(f"  {GREEN}No forbidden patterns found.{RESET}")
            for f in findings:
                color = _severity_color(f["severity"])
                print(f"  {color}[{f['severity'].upper()}]{RESET} {f['file']}:{f['line']} — {f['message']}")
                if f["severity"] == "block":
                    has_blocks = True
                elif f["severity"] == "error":
                    has_errors = True

    # 3. Protected files
    protected_cfg = checks_cfg.get("protected_files", {})
    if protected_cfg.get("enabled", True):
        protected_paths = protected_cfg.get("paths", [])
        if protected_paths:
            _print_header("Protected Files")
            findings = check_protected(staged, protected_paths)
            if not findings:
                print(f"  {GREEN}No protected files in staging.{RESET}")
            for f in findings:
                color = _severity_color(f["severity"])
                print(f"  {color}[{f['severity'].upper()}]{RESET} {f['file']} — {f['message']}")
                if f["severity"] == "block":
                    has_blocks = True

    # 4. Domain authority
    da_cfg = checks_cfg.get("domain_authority", {})
    _print_header("Domain Authority")
    findings = check_domain_authority(da_cfg.get("enabled", True))
    if not findings:
        print(f"  {GREEN}OK{RESET}  domain/domain_authority.yaml")
    for f in findings:
        color = _severity_color(f["severity"])
        print(f"  {color}[{f['severity'].upper()}]{RESET} {f['file']} — {f['message']}")
        if f["severity"] == "error":
            has_errors = True

    # 5. Ruff check (import inline to survive formatter)
    from repo_guard.checks.ruff_check import check_ruff as _check_ruff

    ruff_cfg = checks_cfg.get("ruff", {})
    if ruff_cfg.get("enabled", True):
        py_files = [f for f in staged if f.endswith(".py")]
        if py_files:
            _print_header("Ruff Check")
            findings = _check_ruff(py_files)
            if not findings:
                print(f"  {GREEN}No ruff issues found.{RESET}")
            for f in findings:
                if f["code"] == "SKIP":
                    print(f"  {YELLOW}SKIP{RESET} {f['message']}")
                else:
                    print(f"  {YELLOW}[{f['code']}]{RESET} {f['file']}:{f['line']} {f['message']}")

    # Summary
    print()
    if has_blocks:
        print(f"{RED}{BOLD}BLOCKED: commit contains blocked files or patterns.{RESET}")
        return 1
    if has_errors:
        print(f"{RED}{BOLD}FAILED: fix errors above before committing.{RESET}")
        return 1

    print(f"{GREEN}{BOLD}All checks passed.{RESET}")
    return 0


def cmd_ai_review(args: argparse.Namespace) -> int:
    """Run AI review. Always returns 0 (advisory only)."""
    config = load_config()
    ai_cfg = config.get("ai_review", {})
    timeout = args.timeout or ai_cfg.get("timeout", 120)
    rules_file = ai_cfg.get("rules_file")

    # Get the diff
    if args.staged:
        diff = get_staged_diff()
    elif args.ref:
        diff = get_ref_diff(args.ref)
    else:
        print(f"{YELLOW}Specify --staged or --ref BASE..HEAD{RESET}")
        return 0

    if not diff.strip():
        print(f"{GREEN}No diff to review.{RESET}")
        return 0

    print(f"{BOLD}repo_guard AI review (timeout: {timeout}s){RESET}")
    print("Sending diff to claude CLI...")

    prompt = build_prompt(diff, rules_file)
    result = run_ai_review(prompt, timeout=timeout)

    if result["ok"]:
        print(f"\n{BOLD}=== AI Review ==={RESET}\n")
        print(result["review"])
    else:
        print(f"\n{YELLOW}AI review unavailable: {result['error']}{RESET}")

    return 0


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="repo_guard",
        description="Deterministic pre-commit checks + optional AI review",
    )
    parser.add_argument("--version", action="version", version=f"repo_guard {__version__}")

    subparsers = parser.add_subparsers(dest="command")

    # check subcommand
    check_parser = subparsers.add_parser("check", help="Run deterministic checks")
    check_parser.add_argument("--staged", action="store_true", help="Check staged files only")

    # ai-review subcommand
    ai_parser = subparsers.add_parser("ai-review", help="Run AI code review (advisory)")
    ai_parser.add_argument("--staged", action="store_true", help="Review staged changes")
    ai_parser.add_argument("--ref", type=str, help="Review diff between refs (e.g. main..HEAD)")
    ai_parser.add_argument("--timeout", type=int, help="Timeout in seconds (default: from config)")

    args = parser.parse_args()

    if args.command == "check":
        sys.exit(cmd_check(args))
    elif args.command == "ai-review":
        sys.exit(cmd_ai_review(args))
    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
