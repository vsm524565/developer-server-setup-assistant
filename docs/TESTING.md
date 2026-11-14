# Test strategy and historical evidence

**Published source baseline:** Milestone 4.28 Git setup `46f8637339255b8b2fac1e3a6a2e1f72071197cd`.  
**Latest reported operator workstation state:** uncommitted Milestone 4.29 Docker Engine development. Test outcomes below are **historical user terminal evidence**, not tests executed by the documentation author.

## Run local regression

**Workstation, ordinary user, local-only:**

```bash
cd ~/projects/developer-server-setup-assistant
source .venv/bin/activate
env -u DSSA_INVENTORY_FILE -u DSSA_SSH_KEY \
  python -m unittest discover -s tests/backend -p 'test_*.py' -q
python -m py_compile tests/backend/test_git_installer.py
git diff --check
```

Test availability and count depend on current branch and uncommitted files. Do not run remote package installations as part of documentation validation.

## Observed milestone results

| Category | Location / test | Test environment | Expected | Observed | Status |
|---|---|---|---|---|---|
| Read-only architecture through 4.27 | `tests/backend/test_*.py`; Docker and Compose text/JSON CLI | Workstation + six Linux lab VMs | No execution; trustworthy evidence | 523/523 backend; 24/24 assessment runs | Passed historically |
| Git installer | `tests/backend/test_git_installer.py` | Workstation, mocked Bash | Correct skip/install/fail cases | 6/6 | Passed |
| Git structured failure | `tests/backend/test_git_coordinator.py` | Workstation, mocked error emitter | Preserve completed and mark failed/not attempted | 4/4 | Passed |
| Existing setup failure | `tests/backend/test_setup_failures.py` | Workstation | Valid partial failure frame | 1/1 | Passed |
| Published 4.28 regression | `python -m unittest discover -s tests/backend -p 'test_*.py' -q` | Workstation | No regression | 533/533, exit 0 | Passed |
| Git APT installation | `python -m bridge.cli --target debian13 --component git` | Debian 13 lab | Installed, verified, return 0 | Git 2.47.3, `installed`/`passed` | Passed |
| Git APT idempotency | Same command repeated; `dpkg -S` | Debian 13 lab | SKIP, verified | `skipped`/`passed`, package owned | Passed |
| Git DNF installation | `python -m bridge.cli --target alma10 --component git` | AlmaLinux 10 lab | Installed, verified | Git 2.52.0, `installed`/`passed` | Passed |
| Git DNF idempotency | Same command repeated; `rpm -qf` | AlmaLinux 10 lab | SKIP, verified | `skipped`/`passed`, executable owned by git-core | Passed |
| Six-VM Git planning | `--component git --plan` on six lab targets | Ubuntu 22/24/26, Debian 13, Alma/Rocky 10 | Distinguish installed and missing | 6/6 correctly classified at time of test | Passed |

## Uncommitted Docker Milestone 4.29 tests

These files were present **on the operator workstation, not in the verified GitHub commit**. Do not assume they exist on a fresh clone.

| Category | Local test file | Environment | Expected | Latest observed | Status |
|---|---|---|---|---|---|
| Docker planner / OS guards | `test_docker_planner.py` | Mocked shell | Unsupported/partial/conflict `REVIEW` | 7/7 | Passed locally |
| Docker dispatch | `test_docker_planner_dispatch.py` | Mocked shell | Dedicated planner is called | 1/1 | Passed locally |
| Docker verifier | `test_docker_verification.py` | Mocked package/service/daemon | Require complete packages and responsive daemon | 5/5 | Passed locally |
| Docker installer early guards | `test_docker_installer.py` | Mocked shell | Conflict/partial/unmanaged and supported-platform enforcement | 8/8 | Passed locally |
| Full uncommitted backend regression | `test_*.py` | Workstation | Preserve published tests | 554/554 on Nov 13 before new failure fixture | Passed at checkpoint only |
| Initial APT metadata failure | `test_docker_install_failures.py` | Modified shell fixture | Fail on first metadata refresh | Test failed because first/second error messages were reversed; fix instructed, **no follow-up result** | Pending |
| Prerequisite, signing key download, key fingerprint | `test_docker_install_failures.py` | Modified shell fixture | Fail without installing Docker | 3/4 test suite cases last observed passing | Partially passed |
| Post-repository update, package failure, daemon startup, coordinator propagation | Not yet documented as implemented | N/A | Fail with structured partial-state evidence | No result | Not verified |
| Live Docker Engine install | `--component docker` without `--plan` | Ubuntu 24 lab | Install, daemon API healthy, repeat safe | No live test performed | Not verified |
| Docker Compose installation | Future | Lab | Plugin package + CLI + workload verification | No installation evidence | Not verified |
| Fresh Nginx installer | Existing Bash path | Ubuntu 24 lab | Install Nginx and verify | Historical existing-package and HTTP check only | Not fully verified |
| Swap activation | Swap planning tests | Workstation/lab | Safely create and persist swap | No complete live creation/activation result established | Not verified |

### Six-VM Docker guard

Initial integration produced an unsafe eligible `INSTALL` for all six VMs due to missing dispatcher call. That bug was corrected locally and a permanent dispatch regression was added. Later test results:

- Ubuntu 24.04: `INSTALL`, `eligible:true` for a clean lab candidate.
- Ubuntu 22.04, Ubuntu 26.04, Debian 13, AlmaLinux 10 and Rocky 10: `REVIEW`, `eligible:false`.
- The corrected behavior is **not yet published as source code** on `architecture/layered-platform`.

## Negative and historic discovery test evidence

From prior lab conversations: unsupported Ubuntu 26 test, invalid sudo user, missing `/etc/os-release`, strict known-host mismatch, aaPanel installed/inactive state and component discovery. These are historically reported exercises; precise per-test fixture names, request IDs and complete logs were **not all independently available** for this documentation pass. Do not invent their counts.

## Safe test commands for active local Milestone 4.29

**Workstation, ordinary user, only if the uncommitted Docker files are present:**

```bash
python -m unittest discover -s tests/backend -p 'test_docker_install_failures.py' -v
python -m unittest discover -s tests/backend -p 'test_docker_planner*.py' -v
python -m unittest discover -s tests/backend -p 'test_docker_verification.py' -v
python -m unittest discover -s tests/backend -p 'test_docker_installer.py' -v
bash -n backend/linux/modules/install/docker.sh
git diff --check
```

**Critical test isolation warning:** `test_docker_install_failures.py` sources a modified production Bash installer in a subprocess. Redirecting strings and mocking selected shell functions does **not** guarantee all effects are contained. Do not extend this fixture into a successful package installation path without a truly isolated environment or a strict command/filesystem whitelist. Never run it as root.

## Verification gaps and sign-off policy

A green syntax check proves syntax only. A mocked installer test proves a controlled branch only. `--plan` output is not permission to execute. A passive Docker service/socket observation does not prove Docker API health. Only promote an installation milestone after supported-platform, fresh install, repeat install, failure propagation, daemon health, security review and actual lab evidence are complete.

For current next step and exact state, use [PROJECT_STATE](PROJECT_STATE.md).
