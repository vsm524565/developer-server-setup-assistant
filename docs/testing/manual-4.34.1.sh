#!/usr/bin/env bash
# Manual test helper for 4.34.1 (already-installed packages are left untouched).
#
# Usage: bash docs/testing/manual-4.34.1.sh <step>
#   marks   read-only (no sudo): apt auto/manual marks of ca-certificates,
#           curl and gnupg on ubuntu24, and whether tree is installed
#   defs    write a test-only definitions folder (never in definitions/):
#           dssa-testpkg installs curl (already present) + tree, and its
#           verification must fail, so the run is rolled back
#
# Then run main.py with DSSA_DEFINITIONS_DIR pointing at that folder.

set -euo pipefail

OUT="$HOME/dssa-manual-4.34.1"
HOST=192.168.56.110

case "${1:-}" in
marks)
    ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "dssa-test@$HOST" 'set -u
        auto="$(apt-mark showauto ca-certificates curl gnupg 2>/dev/null)"
        for p in ca-certificates curl gnupg; do
            if grep -qx "$p" <<< "$auto"; then echo "$p: auto"; else echo "$p: manual"; fi
        done
        dpkg-query -W -f="tree: \${Status}\n" tree 2>/dev/null || echo "tree: not installed"'
    ;;
defs)
    mkdir -p "$OUT/definitions"
    jq -n '{schema_version: "1.0", id: "dssa-testpkg", display_name: "DSSA test package",
            category: "tool", depends_on: [],
            variants: [{variant_id: "apt-test", support: "supported",
                platform: {package_manager: "apt"},
                detection: {packages_all: ["tree"]}, conflicts: {},
                steps: [{type: "package_metadata_refresh"},
                        {type: "packages_install", role: "main", packages: ["curl", "tree"]}],
                verification: [{type: "packages_installed", packages: ["tree"]},
                               {type: "executable", name: "dssa-missing-check", version_args: ["--version"]}],
                rollback: {created_paths: []}}]}' > "$OUT/definitions/dssa-testpkg.json"
    echo "Test definitions: $OUT/definitions"
    ls -l "$OUT/definitions"
    ;;
*)
    sed -n '2,12p' "${BASH_SOURCE[0]}"
    exit 2
    ;;
esac
