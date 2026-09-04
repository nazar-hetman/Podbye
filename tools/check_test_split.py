"""Prove the CI test split covers the whole suite, exactly once.

CI runs the suite as two jobs, ``-m qt`` and ``-m "not qt"``. That is only
safe while the two selections are exact complements. If they ever stop adding
up, the missing tests are not reported as skipped or failed - they simply
never run, and nothing says so. This collects all three sets and compares
them, so a gap fails the build at the point it appears.

Collection only: no test is executed here.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _collect(*extra: str) -> set[str]:
    """Node ids pytest would run for *extra* selection arguments."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", *extra],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise SystemExit("collection failed for: %s" % (extra or ("<all>",),))
    ids = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        # Collected node ids are the only lines carrying "::"; the trailing
        # summary ("123 tests collected in 0.5s") does not.
        if "::" in line and not line.startswith(("<", "=")):
            ids.add(line)
    return ids


def main() -> int:
    everything = _collect()
    qt = _collect("-m", "qt")
    logic = _collect("-m", "not qt")

    print("all           : %5d" % len(everything))
    print("qt job        : %5d" % len(qt))
    print("logic job     : %5d" % len(logic))
    print("qt + logic    : %5d" % (len(qt) + len(logic)))

    problems = []
    both = qt & logic
    if both:
        problems.append("%d test(s) selected by BOTH jobs, e.g. %s"
                        % (len(both), sorted(both)[:3]))
    missed = everything - qt - logic
    if missed:
        problems.append("%d test(s) selected by NEITHER job, e.g. %s"
                        % (len(missed), sorted(missed)[:3]))
    extra = (qt | logic) - everything
    if extra:
        problems.append("%d test(s) selected that full collection does not "
                        "list, e.g. %s" % (len(extra), sorted(extra)[:3]))
    if len(qt) + len(logic) != len(everything):
        problems.append("counts do not add up: %d + %d != %d"
                        % (len(qt), len(logic), len(everything)))

    if problems:
        print()
        for p in problems:
            print("FAIL: " + p)
        return 1
    print("\nOK: every test is in exactly one job.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
