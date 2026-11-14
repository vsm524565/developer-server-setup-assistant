# Troubleshooting and safe recovery

DSSA distinguishes **read-only discovery**, **legacy component setup**, and **uncommitted Docker installer development**. Never treat a plan or a green syntax check as proof of installation.

## Fail-safe rules

1. Preserve the exact failed command, sanitized stderr, exit status and framed `DSSA_RESPONSE`.
2. Confirm target identity, OS/version, package manager and the **current** installed-package/service state.
3. Do not automatically rerun a failed installer; repositories, packages and services may be partially modified.
4. Never disable SSH host-key validation, remove unknown data directories, delete client files, reset Git history, or change production servers to make a test pass.
5. Avoid printing credentials, private keys, live customer IPs or full sensitive inventory contents.

## Common diagnostic matrix

| Symptom | Likely cause / investigation | Safe next action |
|---|---|---|
| `Invalid inventory or target` | Missing/duplicate/disabled target, schema mismatch, unsafe inventory file | Validate schema and permissions in a **private** copy; don't log credentials |
| SSH `known_hosts` or host key failure | Unknown/changed server identity | Verify expected fingerprint with infrastructure owner; do not bypass strict checking |
| Sudo denied | Account lacks permission or sudo password invalid | Confirm privileges on disposable lab target; don't broaden permissions blindly |
| OS unsupported | Platform policy explicitly excludes target | Run discovery/read-only report; do not force installation |
| Setup plan returns `REVIEW` | Conflict, partial installation, panel/port evidence, unsupported platform | Inspect reported reason; do not type `APPLY` |
| `DSSA_RESPONSE` missing/invalid | Remote runner failed, interrupted or malformed output | Preserve remote exit; treat change state as unknown |
| Git exists but verification fails | Broken binary or package/executable mismatch | Inspect package ownership and `git --version`; don't reinstall automatically |
| Docker CLI absent / unit not found | Docker not detected through conventional rootful systemd path | Read-only assess package state, do not infer total absence of alternative/rootless installs |
| Docker daemon `REVIEW_REQUIRED` | API not actively probed | Check passive service/socket findings; treat API responsiveness as unverified |
| Docker installer refuses unmanaged executable | Docker, dockerd or containerd exists without recognized vendor package state | Identify owner/installation method before any modification |

## Milestone 4.29 Docker failure-injection history

The Docker installation test suite `tests/backend/test_docker_install_failures.py` was built in the operator's **uncommitted working tree**.

### Fixture failures (not production defects)

1. Initial four failures: mock detected workstation Docker executable. The production guard was behaving conservatively; test isolation needed correction.
2. Next four errors: `source.replace()` referenced `source` before `INSTALLER.read_text()`. Correct the initialization order.
3. Next four failures: Bash `set -u` failed with `$2: unbound variable` inside mocked `apt-get update`. Change mock arguments to `action="${1:-}"`, `option="${2:-}"`.
4. Subsequent state: 3/4 tests passed. Metadata failure was injected during first update but received the **second-update** error message from production `docker.sh`.

### Current exact required fix

The installer contains two distinct `apt-get update` commands:

```bash
# Before writing Docker repository configuration:
apt-get update || fail "APT metadata refresh failed."

# After creating docker.sources:
apt-get update ||
    fail "Docker repository metadata refresh failed; partial repository configuration remains."
```

The operator was instructed to correct the messages and remove temporary `MOCK_APT` diagnostics. **No subsequent passing output is in available evidence.** Do not report 4/4 success until observed.

**Workstation, ordinary user; safe local diagnostics:**

```bash
cd ~/projects/developer-server-setup-assistant
grep -n -A 1 'apt-get update' backend/linux/modules/install/docker.sh
bash -n backend/linux/modules/install/docker.sh
python -m unittest discover -s tests/backend -p 'test_docker_install_failures.py' -v
git diff --check
```

Do not use `python -m bridge.cli --target ubuntu24 --component docker` (without `--plan`) while these safety tests remain unresolved.

### Partial installation recovery principles

- Initial APT metadata failure: before vendor repository changes; investigate system repository reachability and locks.
- Prerequisite installation failure: package state may already have changed; inspect `dpkg` status before retry.
- Signing-key download/fingerprint failure: stop; never accept an unknown key or turn off signature validation.
- Post-repository metadata refresh failure: `docker.sources` / signing key may remain. Review exactly what was written; never silently overwrite/delete.
- Engine package install failure: `docker-ce`, `containerd.io` or related packages may be partially installed; report `COMPONENT_FAILED`.
- Service/daemon failure: CLI installation does not mean Docker API is responsive; independently inspect systemd service and daemon response under an approved workflow.
- Automatic rollback is **not** implemented. Existing data and ownership always take precedence over convenience.

## Security and publication

Before staging docs or tests, inspect for private keys, passwords, IP addresses, inventory files and customer data. Do not stage unrelated backups or local evidence. A docs-only commit should not change any `backend/`, `bridge/`, `main.py`, `connection/` or `tests/` files. Keep the active worktree intact.

See [TESTING](TESTING.md) and [PROJECT_STATE](PROJECT_STATE.md).
