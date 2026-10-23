#!/usr/bin/env python3
"""Check publishable Git files for local state and common identifier leaks.

This is a focused release check, not a general secret scanner. Runtime logs and
databases must stay private even if they happen to pass these pattern checks.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".pcap", ".pcapng", ".iq",
                      ".log", ".dylib", ".a", ".o", ".zip", ".iso"}
PATTERNS = [
    ("machine-specific home path", re.compile(r"/" + "Users" + r"/[A-Za-z0-9_.-]+/")),
    ("possible subscriber/device identifier", re.compile(r"(?<![A-Za-z0-9])[0-9]{14,16}(?![A-Za-z0-9])")),
    ("literal SDR serial", re.compile(r"serial\s*=\s*[0-9A-Fa-f]{12,32}\b", re.I)),
    ("GitHub credential", re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def issues(path, data):
    p = Path(path)
    found = []
    if (p.parts[0] in (".lab", "work", "runtime", "data") or p.name == "lab.json"
            or p.name.startswith(".env") or ".local.json" in p.name
            or p.suffix in FORBIDDEN_SUFFIXES or ".db-" in p.name):
        found.append("private/generated artifact")
    if len(data) > 2 * 1024 * 1024:
        found.append("unexpected file larger than 2 MiB")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return found + ["unexpected binary file"]
    if "\x00" in text:
        found.append("unexpected NUL byte")
    for label, pattern in PATTERNS:
        if pattern.search(text):
            found.append(label)
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="inspect the staged bytes of added/changed files")
    args = parser.parse_args()
    listing = (["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"] if args.staged
               else ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    names = subprocess.check_output(["git", *listing], cwd=ROOT).decode().split("\0")
    failures, count = [], 0
    for name in sorted(set(filter(None, names))):
        path = ROOT / name
        if not args.staged and not path.exists():
            continue
        if path.is_symlink():
            failures.append((name, ["unexpected symlink"]))
            continue
        data = (subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
                if args.staged else path.read_bytes())
        count += 1
        labels = issues(name, data)
        if labels:
            failures.append((name, labels))
    for name, labels in failures:
        # Print locations/reasons only; never echo a suspected credential.
        print(name + ": " + ", ".join(labels), file=sys.stderr)
    if failures:
        return 1
    print(f"Publication check passed for {count} files (no local state or matched identifiers).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
