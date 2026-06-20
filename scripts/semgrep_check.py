#!/usr/bin/env python3
"""
semgrep-compatible CLI wrapper — cross-platform, no native binary required.

Accepts a subset of semgrep's interface:
  semgrep --config <rules.yaml> --error [files...]

Implements pattern-regex rules from the YAML config using Python's `re`
module, producing semgrep-style output. Works on Windows where the semgrep
pip package does not bundle the native semgrep-core binary.

Exit codes (matches semgrep):
  0 — no findings
  1 — findings detected (--error flag enforced by default)
"""
import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


# ── CLI ────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="semgrep",
        description="Local semgrep-compatible scanner (Python, cross-platform).",
    )
    parser.add_argument(
        "--config", "-c",
        default=".semgrep/rules.yaml",
        metavar="PATH",
        help="Path to Semgrep rules YAML file (default: .semgrep/rules.yaml).",
    )
    parser.add_argument(
        "--error",
        action="store_true",
        default=True,
        help="Exit with code 1 if any findings are found (default: on).",
    )
    parser.add_argument(
        "--no-error",
        dest="error",
        action="store_false",
        help="Exit with code 0 even if findings are found.",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="Python files to scan. Defaults to scanning all *.py files recursively.",
    )
    return parser


# ── Rule loading ───────────────────────────────────────────────────────────────

def load_rules(config_path: str) -> list[dict]:
    """Load pattern-regex rules from a Semgrep YAML config."""
    path = Path(config_path)
    if not path.exists():
        print(f"semgrep: error: config file not found: {config_path}", file=sys.stderr)
        sys.exit(2)

    if yaml is None:
        # Fallback: simple regex extraction without PyYAML
        text = path.read_text(encoding="utf-8")
        rules = []
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("- pattern-regex:"):
                rules.append({
                    "id": "unknown",
                    "pattern": line.split(":", 1)[1].strip(),
                    "severity": "ERROR",
                    "message": "Hardcoded secret detected.",
                })
        return rules

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    rules = []
    for rule in data.get("rules", []):
        patterns = rule.get("patterns", [])
        for p in patterns:
            regex = p.get("pattern-regex")
            if regex:
                rules.append({
                    "id": rule.get("id", "unknown"),
                    "pattern": regex,
                    "severity": rule.get("severity", "ERROR"),
                    "message": rule.get("message", "Security issue detected."),
                })
    return rules


# ── File collection ────────────────────────────────────────────────────────────

def collect_files(targets: list[str]) -> list[Path]:
    """Resolve file arguments to a list of .py Paths."""
    if not targets:
        raw_paths = list(Path(".").rglob("*.py"))
    else:
        raw_paths = []
        for t in targets:
            p = Path(t)
            if p.is_dir():
                raw_paths.extend(p.rglob("*.py"))
            elif p.suffix == ".py":
                raw_paths.append(p)

    ignored_dirs = {".venv", ".pytest_cache", ".ruff_cache", "build", "dist"}
    filtered_paths = []
    for path in raw_paths:
        if not any(part in ignored_dirs for part in path.parts):
            filtered_paths.append(path)
    return filtered_paths


# ── Scanner ────────────────────────────────────────────────────────────────────

def scan(files: list[Path], rules: list[dict]) -> list[dict]:
    """Run all rules against all files, return list of finding dicts."""
    findings = []
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            print(f"semgrep: warning: cannot read {path}: {exc}", file=sys.stderr)
            continue

        for rule in rules:
            try:
                pattern = re.compile(rule["pattern"])
            except re.error as exc:
                print(f"semgrep: bad regex in rule {rule['id']!r}: {exc}", file=sys.stderr)
                continue

            for lineno, line in enumerate(lines, 1):
                m = pattern.search(line)
                if m:
                    findings.append({
                        "rule_id": rule["id"],
                        "severity": rule["severity"],
                        "message": rule["message"],
                        "path": str(path),
                        "line": lineno,
                        "matched": m.group(),
                        "source_line": line.rstrip(),
                    })
    return findings


# ── Reporter ───────────────────────────────────────────────────────────────────

def _safe(text: str) -> str:
    """Encode to ASCII replacing unencodable chars — safe on any Windows terminal."""
    return text.encode("ascii", errors="replace").decode("ascii")


def report(findings: list[dict]) -> None:
    """Print semgrep-style finding output."""
    if not findings:
        print("Ran 1 rule. No findings.")
        return

    print()
    for f in findings:
        severity = f["severity"]
        rule_id = f["rule_id"]
        path = f["path"]
        lineno = f["line"]
        source = _safe(f["source_line"].strip())
        message = _safe(f["message"].strip().replace("\n", " "))
        matched = _safe(f["matched"])

        print(f"  {severity} [{rule_id}]")
        print(f"  {path}:{lineno}")
        print(f"  > {source}")
        print(f"  Matched token : {matched!r}")
        print(f"  {message}")
        print()

    n = len(findings)
    print(f"Ran 1 rule. {n} finding{'s' if n != 1 else ''}.")
    print()
    print("  Remediation: remove the hardcoded credential and load it from")
    print("  os.environ[\"GOOGLE_API_KEY\"] or Google Cloud Secret Manager.")
    print("  Rotate the exposed key immediately.")
    print()



# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    rules = load_rules(args.config)
    if not rules:
        print(f"semgrep: warning: no pattern-regex rules found in {args.config}")
        sys.exit(0)

    files = collect_files(args.files)
    findings = scan(files, rules)
    report(findings)

    if findings and args.error:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
