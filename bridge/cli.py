"""DSSA bridge CLI: read-only assessment and capability reports.

Installation moved to ``main.py`` (plan -> approve -> apply, 4.34). The
former ``--component`` setup path through setup-components.sh is retired.
"""

import sys


USAGE = """Usage:
  python -m bridge.cli assess ...        read-only application assessment
  python -m bridge.cli capabilities ...  published capability report

To install components use: python main.py
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "assess":
        from bridge.assessment_cli import run_assessment_cli

        return run_assessment_cli(argv[1:])
    if argv and argv[0] == "capabilities":
        from bridge.capability_cli import run_capability_cli

        return run_capability_cli(argv[1:])
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
