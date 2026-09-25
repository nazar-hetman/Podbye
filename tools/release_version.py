"""Read the application version, and refuse a release tag that disagrees.

app/version.py is the only place the version is written. Everything that
needs it at release time - the installer's AppVersion, the artifact names, the
tag check - asks this script rather than keeping a copy, because a copy is a
second number that someone has to remember to bump.

    python tools/release_version.py                  print the version
    python tools/release_version.py --tag v1.2.3     exit 1 unless the tag is
                                                     exactly "v" + the version

The version is read as text rather than imported, so this runs on a bare
Python with none of the app's dependencies installed.
"""
from __future__ import annotations

import argparse
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "app" / "version.py"

# MAJOR.MINOR.PATCH with an optional pre-release suffix such as "-beta.5" or
# "-rc.1". It ends up in file names and in Inno Setup's AppVersion, so nothing
# looser than this is accepted.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$")


def read_version(path: pathlib.Path = VERSION_FILE) -> str:
    """The string assigned to ``__version__`` in *path*."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "__version__"
                        for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return node.value.value
    raise ValueError(f"no __version__ = \"...\" assignment in {path}")


def tag_problem(version: str, tag: str) -> str | None:
    """Why *tag* cannot release *version*, or None when it can."""
    expected = f"v{version}"
    if tag != expected:
        return (f"release tag {tag!r} does not match the application version: "
                f"app/version.py says {version!r}, so the tag must be "
                f"{expected!r}. Bump __version__ or re-tag.")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="release tag to validate, e.g. v1.0.0-rc.1")
    args = parser.parse_args(argv)

    try:
        version = read_version()
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not VERSION_RE.match(version):
        print(f"error: app/version.py has {version!r}, which is not "
              f"MAJOR.MINOR.PATCH[-prerelease]", file=sys.stderr)
        return 1
    if args.tag is not None:
        problem = tag_problem(version, args.tag)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 1
    print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
