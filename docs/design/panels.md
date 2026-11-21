# Control Panels — Decisions (draft)

Status: all six decided with the project owner, 2026-11-21. Built (engine,
contracts, seven draft definitions); tests and the lab run still open, see
"Build status" at the end.

How panels differ from other components:
- they install through the vendor's own installer, not a package;
- they take over the whole server (web, PHP, database, mail, FTP);
- they expect a fresh OS;
- they have little or no real undo;
- each supports its own list of OS versions.

Today DSSA only detects panels (`discovery/panel.sh`), and most components
plan as REVIEW on a panel server (`"panels": "any"`).

## 1. Which panels — decided 2026-11-21: all seven

| Panel | Licence |
|---|---|
| aaPanel | free |
| CyberPanel | free (OpenLiteSpeed edition) |
| HestiaCP | free |
| CloudPanel | free |
| cPanel/WHM | licensed; installs without one, licence added afterwards |
| Plesk | licensed; installs without one, licence added afterwards |
| DirectAdmin | licensed; recent installers may ask for a key at install (to check when writing its definition; see decision 6) |

The owner's point: installing a licensed panel does not need a valid
licence, so installation is not affected.

## 2. How a panel installs — decided 2026-11-21: the vendor's latest installer over HTTPS

- DSSA downloads the installer from the vendor's own HTTPS URL, fixed in
  the definition (`--proto =https`, no redirects to other hosts), and runs
  it as root with the vendor's non-interactive options.
- No SHA-256 pin, so vendor changes need no re-pinning. The run log records
  the SHA-256 and size of the script that actually ran, for tracing.
- Accepted consequence: a changed or compromised installer is not caught
  before it runs. The vendor's own HTTPS and package signatures are the
  protection, as for the panel's later downloads.

## 3. Preconditions — decided 2026-11-21: strict fresh-server check, no override

A panel plan is BLOCKED when any of these holds:
- another control panel is detected;
- a web server, database or mail server package is installed (each panel
  definition lists them);
- DSSA's install record is not empty;
- the OS version is not on the panel's supported list;
- RAM or free disk is below the vendor's minimum.

There is no override: start from a fresh server. DSSA cannot take
snapshots (that happens at the hypervisor or provider), so every panel plan
carries the warning "Not reversible: take a VM snapshot first".

## 4. Undo — decided 2026-11-21: the vendor's uninstaller where one exists

- **With an official uninstaller** (expected: aaPanel, Plesk; each checked
  when its definition is written):
  - A failed install runs it, then stops with the result.
  - `plan --remove` uses it, with the typed name plus `APPROVE`.
  - It is downloaded the same way as the installer (decision 2).
- **Without one** (expected: CyberPanel, HestiaCP, CloudPanel, cPanel,
  DirectAdmin):
  - A failed install stops with exit 4 ("needs review") and the full log.
  - `plan --remove` is BLOCKED: "reinstall the OS or restore the snapshot".
- Either way, every panel plan says "Not reversible: take a VM snapshot
  first". A successful install is written into DSSA's install record.

## 5. DSSA's role after a panel is installed — decided 2026-11-21: split by owner

- The panel owns the web server, PHP, databases, mail, DNS, FTP and usually
  the firewall. DSSA components for those stay REVIEW on a panel server,
  with the reason "managed by <panel>; use its own tools".
- DSSA may install what the panel does not own: swap, chrony, git, Python,
  Node.js, Java and Docker. Each panel definition carries this allowlist,
  so a panel that does own one of these (for example a firewall) can take
  it out.
- Installing components through a panel's own CLI or API is a later option,
  one panel at a time, and would need panel credentials.

## 6. Licence keys — decided 2026-11-21: DSSA never holds them

- cPanel and Plesk install without a licence (trial or none). The licence
  is activated in the panel afterwards.
- If an installer asks for a key (possibly DirectAdmin), the prompt appears
  in the run's terminal and the operator types the key there, as with
  sudo. DSSA passes it through to the server and never stores or logs it.
  This works the same in the web UI's relayed terminal.

## Build outline (proposed, for the owner's go-ahead)

1. **Step type `vendor_installer`:** an HTTPS URL on the vendor's host
   (fixed in the definition), fixed arguments, and an optional
   `uninstall_url` and `uninstall_args`. It runs in the run's terminal, so
   prompts reach the operator, and journals the script's SHA-256 and size.
2. **Detection:** by the panel's own folder (the existing
   `discover_panels` list, plus CloudPanel), as a detection state.
3. **Preconditions** (decision 3): the definition lists the panel's
   supported OS versions, conflicting packages, and minimum RAM and disk.
   The plan also requires an empty install record and no other panel.
4. **Owner split** (decision 5): a panel-owned component's REVIEW reason
   names the panel. The allowlisted components (swap, chrony, git, Python,
   Node.js, Java, Docker) have no panel conflict.
5. **Definitions** for the seven panels, each with its installer URL,
   non-interactive options, OS list and minimums taken from the vendor's
   current documentation, and an uninstaller where one exists.
6. **Tests** with a mocked installer and uninstaller, then the lab. The lab
   needs **fresh VMs** (clones or snapshots), because the current lab VMs
   have Docker and git and would be BLOCKED by decision 3.

## Build status (2026-11-21)

Done (commit 55821ca and the one after it):
- **Engine:** the `vendor_installer` step, `panel_present` detection, the
  fresh-server requirements, the owner split, password masking in the run
  log, and BLOCKED panel removal.
- **Definitions (draft)** from the vendors' docs and installer source:

| Panel | Installer | Unattended | OS (DSSA lab) | Min RAM / swap / disk |
|---|---|---|---|---|
| aaPanel | www.aapanel.com/script/install_panel_en.sh | `-y` | Ubuntu 22/24/26, Debian 12/13, Alma/Rocky 9/10 | 512 MiB / – / 1 GiB |
| CyberPanel | cyberpanel.net/install.sh | `-v ols -p r` (OpenLiteSpeed, random password; stored in /etc/cyberpanel/adminPass) | Ubuntu 22/24/26, Alma 9/10 | 1 GiB / – / 10 GiB |
| HestiaCP | raw.githubusercontent.com/hestiacp/…/hst-install.sh | `--interactive no --force` plus hostname, email, user `hestiaadmin` and a password DSSA generates (root-only `/root/.dssa/hestia-admin-password`) | Ubuntu 22/24/26, Debian 12/13 | 1 GiB / – / 10 GiB; FQDN |
| CloudPanel | installer.cloudpanel.io/ce/v2/install.sh | `DB_ENGINE=MARIADB_11.8` | Ubuntu 22/24/26, Debian 12/13 | 2 GiB / – / 10 GiB |
| cPanel & WHM | securedownloads.cpanel.net/latest (run from /home) | none needed | Ubuntu 24.04, Alma 9/10 | 2 GiB / – / 25 GiB; FQDN |
| Plesk | autoinstall.plesk.com/one-click-installer | none needed | Ubuntu 22/24/26, Debian 12/13, Alma 9/10 | 1 GiB / 1 GiB / 20 GiB |
| DirectAdmin | download.directadmin.com/setup.sh | `DA_WEB_INSTALL=yes`: no key on the command line; the licence key is entered in DirectAdmin's own web page | Ubuntu 22/24/26, Debian 12/13, Alma/Rocky 9/10 | 4 GiB / 4 GiB / 2 GiB |

Notes from building:
- **Prompts:** the apply run is detached, so it survives a dropped
  connection but its stdin is /dev/null. Installers therefore run
  unattended. Decision 6 still holds: no key or password is typed into
  DSSA, and DirectAdmin's key goes into its own web page.
- **Uninstallers:** none of the seven has an official uninstaller (cPanel
  says to reformat). Removal is BLOCKED, and a failed install needs review
  (exit 4). The `uninstall_url` support is in place for a panel that gains
  one.
- **HestiaCP:** on Ubuntu its installer treats the preinstalled `ufw` as a
  conflict and would prompt; `--force` skips that one check, since DSSA's
  fresh-server check already guarantees no web, database or mail software.
  Its admin password is passed on the installer's command line, where other
  local processes can see it while the installer runs.
- **CloudPanel:** create its admin user in the browser right after install
  (the vendor warns that bots look for fresh installs).
- **Plesk on Debian:** needs AppArmor disabled first (vendor note; checked in
  the lab).
- **Flags to confirm in the lab:** aaPanel's `-y` and the DirectAdmin
  web-install flag were read from secondary sources.

Open:
- the tests: `tests/backend/test_panels.py` has the harness and a TODO list
  of ten tests;
- a lab guide and the lab run on **fresh VMs** (clones or snapshots of clean
  installs, one per panel or panel family).
