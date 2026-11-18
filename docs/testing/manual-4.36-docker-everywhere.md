# Manual Test — Milestone 4.36 Docker Everywhere + Compose

**This test changes lab VMs on purpose:** Docker and Compose stay
installed on all six VMs afterwards (decision D). The alma10 failure
test cleans up after itself.

Shorthand: `H` = `bash docs/testing/manual-4.36.sh`,
`M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`,
`D` = `DSSA_DEFINITIONS_DIR=$HOME/dssa-manual-4.36/definitions`.
Each plan, approve and apply is run **once**, one step at a time.

| # | Command | Checks | Expected |
|---|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_definition_planner tests.backend.test_component_definitions tests.backend.test_journal_rollback tests.backend.test_unified_apply` | Automated tests | `Ran 140 tests … OK (skipped=1)` |
| 2 | You add `"lab": true` to ubuntu22, ubuntu26, debian13, alma10 in `lab-six.json`; then `H flags` | Lab targets | all six `lab=true` |
| 3 | `M plan ubuntu24 docker_compose`, then `M apply ID` | Verification-only on the 4.34 install | Docker `SKIP`, Compose `SKIP`; no approval; `docker verified`, `docker_compose verified`, `docker compose: plugin answering` |
| 4 | `H defs`; `H state alma10` | dnf failure test setup | no Docker, `rpm key (Docker): none` |
| 5 | `D M plan alma10 dssa-dnftest` → approve → apply | New dnf vendor step and its rollback | key fingerprint checked; Compose plugin installed from Docker's repo; verification fails; undo `remove_package docker-compose-plugin…`, `remove_file …dssa-dnftest.repo`, `remove_file …dssa-dnftest.asc`, `remove_rpm_key gpg-pubkey-621e9f35-…: ok` |
| 6 | `H state alma10` | Clean again | no Docker files, `rpm key (Docker): none` |
| 7 | for each of ubuntu22, ubuntu26, debian13, alma10, rocky10: `M --allow-draft plan VM docker_compose` (type `docker`, then `docker_compose`) → approve → apply | Live install per platform | Docker `INSTALL` then Compose `INSTALL`; `docker: daemon answering`, `docker compose: plugin answering`; exit 0 |
| 8 | `H state VM` after each | Outside view | `docker-ce` and `docker-compose-plugin` installed, `docker.service: active`; EL: `docker-ce.repo` present and `rpm key (Docker): gpg-pubkey-621e9f35-…` |
| 9 | `M --allow-draft plan alma10 docker_compose` (before any update; rocky10 if its kernel is behind) | EL kernel hold, added after the alma10 failure | if rocky10's kernel is behind its repositories: Docker `BLOCKED` ("Running kernel … is older than the newest kernel-modules-extra … reboot first"), Compose held because Docker is blocked; nothing runnable |
| 10 | You, on the held EL VM: `sudo dnf -y update`, then `sudo reboot` | Bring EL to the newest kernel | after the reboot `uname -r` equals the newest `kernel-core` |
| 11 | Steps 7–8 again for that VM | Live EL install | as in steps 7–8 |
| 12 | `ssh -t dssa-test@192.168.56.114 'sudo journalctl -u docker.service --since "-30 min" --no-pager \| tail -40'` | Why dockerd exited on alma10 | found: `INVALID_IPV: 'ipv4' is not a valid backend` from firewalld |
| 13 | firewalld state on alma10 and rocky10 | Why rocky10 passed | both `active`; a `docker` zone on alma10 left over from the failed run |
| 14 | You, on alma10: delete the leftover `docker` zone and `docker-forwarding` policy, then reload firewalld | Clean start | `firewall-cmd --permanent --get-zones` has no `docker` |
| 15 | Steps 7–8 again for alma10 | Live EL install with `firewalld_reload` | `firewalld: reloading so it sees the newly installed iptables-nft` in the run log; Docker `installed`, Compose verified, exit 0 |

After every VM passes, one commit marks those variants `supported`
(decision D).

## Result

Run by the project owner on 2026-11-17/18: **all steps passed** after the
fixes below.

| # | Outcome |
|---|---|
| 1 | `Ran 132 tests … OK (skipped=1)` (140 after the firewalld fix) |
| 2 | all six `lab=true` |
| 3 | Plan `dd1edee0…`: `docker verified`, `docker_compose verified`, no approval asked (the first attempt was stale and showed the misleading refusal fixed below) |
| 4 | alma10 clean, `rpm key (Docker): none` |
| 5 | Plan `1ff3b531…`: key fingerprint checked, Compose plugin installed from Docker's repo, verification failed as planned; `failed_rolled_back` with `remove_file` repo and key and `remove_rpm_key gpg-pubkey-621e9f35-58ade481: ok`; exit 3 |
| 6 | alma10 clean again |
| 7 | ubuntu22 (`1c29c20f…`), ubuntu26 (`2b325743…`), debian13 (`f552f72c…`), rocky10 (`4098cfd5…`): Docker `installed`, Compose `verified` (pulled in with Docker); exit 0. ubuntu22's first run (`3da97ca8…`) gave Compose `drift_skipped`, fixed below. alma10 (`e0d56412…`) failed, see below |
| 8 | Docker 29.10.0 and Compose 5.8.0 installed, `docker.service: active` on each; EL: repo file and `gpg-pubkey-621e9f35-58ade481` |
| 9 | alma10, plan `2913de85…`: Docker `BLOCKED` ("Running kernel 6.12.0-211.7.3… is older than the newest kernel-modules-extra (6.12.0-264.16.1…)"), Compose held, `Runnable: none`. rocky10 already had `kernel-modules-extra` for its running kernel, so it planned `INSTALL` correctly and added no kernel |
| 10 | alma10 after update and reboot: `uname -r` `6.12.0-264.16.1.el10_3.x86_64`, same as the newest `kernel-modules-extra` |
| 11 | alma10, plan `83f87505…`: no kernel added, but `service_start` failed; clean rollback of 10 packages, repo, key and paths; exit 3 |
| 12 | dockerd: `failed to create NAT chain DOCKER: COMMAND_FAILED: INVALID_IPV: 'ipv4' is not a valid backend or is unavailable` |
| 13 | firewalld `active` on both; both list a `docker` zone (rocky10's is in use; alma10's was left from plan `e0d56412…`) |
| 14 | alma10: `docker` zone and `docker-forwarding` policy deleted (both existed), firewalld reloaded |
| 15 | alma10, plan `51656b17…`: `firewalld: reloading so it sees the newly installed iptables-nft`, Docker `installed`, Compose `verified`, exit 0; journal `upgraded: []`, `added_versions: []`; `H state alma10` as in step 8 |

Found and fixed during the run (details in the design doc, section 11):
- A stale verification-only plan said "No approval exists"; `apply` now
  shows the real preflight reason.
- A planned INSTALL that is already installed at re-check (same variant)
  is verified only, not `drift_skipped` (apt Recommends and dnf weak
  dependencies bring the Compose plugin in with Docker).
- EL: plan-time hold when `kernel-modules-extra` is not available for the
  running kernel (installing it would add a new kernel).
- dnf rollback removes versions installed side by side (a new kernel),
  never the running kernel. Covered by mock tests; not repeated live.
- EL: new step `firewalld_reload` after `iptables-nft` is newly installed;
  rollback removes Docker's saved firewalld zone and policy and reloads
  firewalld.
- Test helper: the rpm key line printed nothing when no key was present.
