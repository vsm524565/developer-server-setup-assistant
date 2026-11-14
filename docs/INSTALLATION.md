# Installation and safe developer setup

This document is for recreating the **development environment and read-only workflows**, not production deployment. It reflects published commit `46f8637`. The operator's newer Docker installer is uncommitted; do not use the published checkout to claim Docker installation support.

## Prerequisites

**Developer workstation, ordinary user:** Linux with Bash, Git, Python 3 and venv, OpenSSH client, `jq` and standard GNU tools (`base64`, `sha256sum`, `stat`). An SSH identity and known-hosts entry are required for the inventory-backed lab backend. The tested Python dependencies are pinned in root `requirements.txt` (including Paramiko). Avoid substituting undocumented versions.

**Target server:** an authorized disposable Linux lab VM reachable via SSH, with an appropriately privileged sudo account and the tools needed by the selected operation. Nothing in this document grants permission to manage client or production servers.

## Checkout and Python dependencies

**Workstation, ordinary user:**

```bash
git clone git@github.com:vsm524565/developer-server-setup-assistant.git
cd developer-server-setup-assistant
git switch architecture/layered-platform
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Review the selected branch and version before running tests. The active workstation development tree may contain additional uncommitted Docker files not present in a fresh clone.

```bash
git branch --show-current
git rev-parse HEAD
git status --short
```

## Inventory and SSH identity

Published `backend/linux/connection/inventory.sh` reads `DSSA_INVENTORY_FILE`, defaulting to `config/inventory/servers.json`. It requires a regular non-symlink file, not group- or world-writable. The selected server must have one unique enabled Linux target with valid host, SSH user and port.

**Workstation, ordinary user:** example *sanitized* inventory, only if the file does not already exist; never overwrite a real inventory:

```json
{
  "schema_version": "1.0",
  "servers": [
    {
      "target_id": "lab-ubuntu24",
      "enabled": true,
      "platform": "linux",
      "host": "lab-host.example.invalid",
      "ssh_port": 22,
      "ssh_user": "lab-sudo-user"
    }
  ]
}
```

Store the JSON at a private file path **outside public repository content**, set restrictive permissions (for example `chmod 600 /path/to/lab-inventory.json`) and configure:

```bash
export DSSA_INVENTORY_FILE="/path/to/lab-inventory.json"
export DSSA_SSH_KEY="$HOME/.ssh/your-lab-key"
test -r "$DSSA_INVENTORY_FILE"
test -r "$DSSA_SSH_KEY"
```

The required SSH host key must already be vetted in `$HOME/.ssh/known_hosts`; never bypass `StrictHostKeyChecking` or accept unknown keys blindly. The target sudo prompt is interactive. Do not store passwords in inventory or terminal history.

## Read-only routes

**Workstation, ordinary user; network used for target assessments:**

```bash
python -m bridge.cli capabilities
python -m bridge.cli assess docker --target lab-ubuntu24 --format text
python -m bridge.cli assess docker_compose --target lab-ubuntu24 --format json
python -m bridge.cli --target lab-ubuntu24 --component git --plan
python main.py
```

The last command starts an interactive discovery/plan demonstration, not component execution. A generated report can indicate `REVIEW_REQUIRED` even with exit status zero. The `capabilities` route is offline.

## Existing published Git installation path

**Workstation, ordinary user launching a privileged target-side Bash workflow:** only on a disposable, authorized lab VM after a backup/restore decision:

```bash
python -m bridge.cli --target lab-ubuntu24 --component git --plan
```

Review the OS/package manager/target. If the plan is eligible and matches the approved operation, a separate apply-mode command (without `--plan`) displays the same kind of plan and requests explicit `APPLY` via the interactive target terminal. **Do not type APPLY blindly**. The legacy path allows Git and Nginx in the verified published source. It is not the executable equivalent of the Python draft recipe framework.

## Evidence and error handling

Plan results contain `schema_version`, `eligible`, `steps` and per-step `action` (`INSTALL`, `SKIP` or `REVIEW`). Successful setup uses `DSSA_RESPONSE` with a `results` array of package/service/verification statuses. On failure, preserve stdout/stderr and the nonzero exit code; the remote host may have partially changed. Never immediately repeat a failed package install without inspecting its state.

## Validation

**Workstation, ordinary user; local-only non-destructive:**

```bash
env -u DSSA_INVENTORY_FILE -u DSSA_SSH_KEY \
  python -m unittest discover -s tests/backend -p 'test_*.py' -q
git diff --check
```

Published 4.28 baseline: 533 passed (historical operator output). Running this on a fresh clone produces its **own** count and result; don't promise the same count if the branch differs.

See [TESTING](TESTING.md) and [PROJECT_STATE](PROJECT_STATE.md). No root command is required on the workstation for the read-only tests above.
