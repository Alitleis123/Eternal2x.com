"""Report what this Resolve build's scripting API actually offers.

Run it from a terminal in the plugin folder, with Resolve open and its external
scripting turned on, when something behaves unexpectedly or on a Resolve
version this plugin has not seen. The output says which methods exist,
which is usually enough to explain any failure without guesswork.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys

from Pipeline import resolve_bridge as bridge


def main() -> int:
    parser = argparse.ArgumentParser(description="Eternal2x Resolve API probe.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args()

    try:
        resolve = bridge.connect()
    except bridge.ResolveError as exc:
        print(f"Error: {exc}")
        return 2

    version = ""
    for attr in ("GetVersionString", "GetProductName"):
        getter = getattr(resolve, attr, None)
        if callable(getter):
            try:
                version = f"{version} {getter()}".strip()
            except Exception:
                pass

    report = {
        "resolve": version or "unknown",
        "studio": bridge.is_studio(resolve),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "api": bridge.probe(resolve),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"Resolve : {report['resolve']}")
    print(f"Studio  : {report['studio']}")
    print(f"Python  : {report['python']}")
    print(f"Platform: {report['platform']}")
    for obj, methods in report["api"].items():
        available = [m for m, ok in methods.items() if ok]
        missing = [m for m, ok in methods.items() if not ok]
        print(f"\n{obj}")
        print("  present: " + (", ".join(available) if available else "(none)"))
        print("  absent : " + (", ".join(missing) if missing else "(none)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
