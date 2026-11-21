"""Control panels (4.48): fresh-server checks, the vendor installer, the
owner split, and no undo where the vendor has none (docs/design/panels.md).
The vendor installers are mocked scripts served by the mocked curl.

Status: the harness (PanelServer, installer(), serve()) is in place; the
tests are still to be written. TODO, one test each:
  1. every panel plans INSTALL (action REVIEW: draft) on a supported, fresh
     server, with details {"panel", "reversible": false} and the
     "Not reversible" reason; hestia/cloudpanel have no dnf variant (BLOCKED)
  2. fresh-server blocks: a web/db/mail package installed; a non-empty
     install record; another panel's folder present; too little RAM; too
     little swap (directadmin, plesk); no FQDN (hestia, cpanel)
  3. aapanel installs through the mocked installer: panel folder created,
     verify hook passes, record entry written, installer SHA-256 journaled,
     "S3cretFromVendor" masked as "[hidden by DSSA]" in the run output
  4. hestia: {fqdn}/{password} filled in (installer_args.txt), the password
     file is 0600 and the password is in neither the output nor the journal
  5. a failing installer without an uninstaller: rollback_incomplete, exit 4,
     "reinstall the OS or restore the VM snapshot"
  6. a test-only panel definition with uninstall_url: the uninstaller runs
     on failure (failed_rolled_back, exit 3)
  7. owner split: with a panel present, nginx plans REVIEW "Managed by
     <panel>", swap and git still plan INSTALL
  8. plan --remove of an installed panel is BLOCKED
  9. risky_steps(plan) asks for the panel's name with the snapshot warning
 10. a failed installer download (fail.json "installer_download") changes
     nothing
"""

import json
import shlex
import unittest

from tests.backend.test_components import ComponentServer, load
from tests.backend.test_journal_rollback import PLAN_A, ROOT
from bridge.components.risk import risky_steps

PANELS = ("aapanel", "cyberpanel", "hestia", "cloudpanel", "cpanel", "plesk", "directadmin")
FOLDER = {"aapanel": "www/server/panel", "cyberpanel": "usr/local/CyberCP", "hestia": "usr/local/hestia",
          "cloudpanel": "home/clp/htdocs/app", "cpanel": "usr/local/cpanel", "plesk": "usr/local/psa",
          "directadmin": "usr/local/directadmin"}
PORT = {"hestia": 8083, "cloudpanel": 8443, "cpanel": 2087, "directadmin": 2222}


def installer(folder, exit_code=0, port=None):
    """A stand-in vendor installer: records its arguments, makes the
    panel's folder, prints a password like the real ones do."""
    ports = f'echo \'{{"panel.service": [{port}]}}\' > "$MOCK_STATE/unit_ports.json"; ' if port else ""
    return ("#!/bin/bash\n"
            'printf \'%s\\n\' "$@" > "$MOCK_STATE/installer_args.txt"\n'
            'env > "$MOCK_STATE/installer_env.txt"\n'
            'echo "username: admin"\n'
            'echo "password: S3cretFromVendor"\n'
            'echo "Admin password will be set to S3cretFromVendor"\n'
            f'mkdir -p "$DSSA_ROOT/{folder}"\n'
            + (f'python3 - <<PY\nimport json,os\np=os.environ["MOCK_STATE"]+"/units.json"\n'
               f'u=json.load(open(p)) if os.path.exists(p) else {{}}\n'
               f'u["panel.service"]={{"file_state":"enabled","active":"active"}}\njson.dump(u,open(p,"w"))\n'
               f'json.dump({{"panel.service":[{port}]}},open(os.environ["MOCK_STATE"]+"/unit_ports.json","w"))\nPY\n'
               if port else "")
            + f"exit {exit_code}\n")


class PanelServer(ComponentServer):

    def setUp(self):
        super().setUp()
        (self.root / "proc/meminfo").write_text("MemTotal:  8388608 kB\nSwapTotal: 8388608 kB\n")
        self.write("hostname.json", "server1.example.com")

    def bash(self, body, with_root=True):
        # Panel discovery and the install record are needed by the
        # fresh-server facts. discover_panels checks real paths, so tests
        # that need a panel present create its folder under DSSA_ROOT and
        # override discover_panels in the body (see TODO below).
        panel = ROOT / "backend/linux/discovery/panel.sh"
        record = ROOT / "backend/linux/engine/record.sh"
        return super().bash(f"source {shlex.quote(str(panel))}\nsource {shlex.quote(str(record))}\n" + body,
                            with_root)

    def serve(self, panel, exit_code=0):
        url = next(st["url"] for v in load(panel)["variants"] for st in v["steps"] if st["type"] == "vendor_installer")
        self.write("installers.json", {url: installer(FOLDER[panel], exit_code, PORT.get(panel))})


if __name__ == "__main__":
    unittest.main()
