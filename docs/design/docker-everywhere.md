# Docker on Every Lab Platform, plus Compose (Milestone 4.36)

Status: **Implemented 2026-11-17** (decisions in §8; build notes in §10), waiting for the project owner's lab test.
Builds on 4.30 (definitions), 4.33 (journal and rollback), 4.34
(unified apply, `--allow-draft` on lab targets) and 4.35 (hardening).

## 1. Goal

- Docker Engine installs, verifies and rolls back on all six lab
  platforms through `main.py`, from Docker's own repositories.
- A `docker_compose` component (catalog ID `docker_compose`, category
  `container`) that depends on Docker.
- A new allowlisted step type for **dnf vendor repositories**, needed
  for Alma and Rocky now, and later for other vendor-repo apps (the
  official PostgreSQL, MariaDB and Node repositories).

## 2. Facts checked on 2026-11-17

| VM | OS | Codename | Docker repository | Status |
|---|---|---|---|---|
| ubuntu22 | Ubuntu 22.04 | jammy | `linux/ubuntu` `jammy` | stable amd64 published |
| ubuntu24 | Ubuntu 24.04 | noble | `linux/ubuntu` `noble` | **installed** (4.34): docker-ce, containerd.io, compose plugin |
| ubuntu26 | Ubuntu 26.04 | resolute | `linux/ubuntu` `resolute` | stable amd64 published |
| debian13 | Debian 13 | trixie | `linux/debian` `trixie` | stable amd64 published |
| alma10 | AlmaLinux 10.2 | (none) | `linux/centos` or `linux/rhel` `10` | both publish `10/x86_64/stable` |
| rocky10 | Rocky 10.2 | (none) | same | same |

- Signing keys: Ubuntu and Debian share `9DC8 5822 9FC7 DD38 854A E2D8
  8D81 803C 0EBF CD88`. CentOS and RHEL share `060A 61C5 1B55 8A7F 742B
  77AA C52F EB6B 621E 9F35`.
- No VM has a conflicting package (podman, buildah, runc, docker.io,
  containerd) or an existing Docker repository.
- The packages have the same names on every platform: `docker-ce`,
  `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`,
  `docker-compose-plugin`.

## 3. Variants in `definitions/components/docker.json`

Following the 4.30 rule (match by package manager; name an OS only when
the component needs it), Docker needs an OS. Each variant names its
platform, its repository and its key fingerprint explicitly. Their
layout is decision §8 A.

Every variant has the same **conflicts** as today, plus for the RHEL
family: `podman`, `buildah`, `podman-docker`, `docker-client`,
`docker-common`, `docker-engine`, `docker-latest`. Any conflict means
REVIEW. DSSA never removes software it did not install (the
refuse-to-own rule). The repository-pattern conflict check must also
search `/etc/yum.repos.d` (today it searches apt locations only).

**Services:** the RHEL packages do not enable or start `docker.service`;
the Debian/Ubuntu packages do. Proposal: every variant has explicit
`service_enable` and `service_start` steps for `docker.service`, so
the journal records the pre-state everywhere and the rollback is the
same on every OS.

## 4. New step type: `dnf_vendor_repository`

The same idea as `apt_vendor_repository`:

| Field | Example (Docker, Alma/Rocky 10) |
|---|---|
| `repository_id` | `docker-ce-stable` |
| `key_url` | `https://download.docker.com/linux/centos/gpg` |
| `key_fingerprint` | `060A61C51B558A7F742B77AAC52FEB6B621E9F35` |
| `key_path` | `/etc/pki/rpm-gpg/docker-ce.asc` |
| `repo_path` | `/etc/yum.repos.d/docker-ce.repo` |
| `baseurl` | `https://download.docker.com/linux/centos/10/x86_64/stable` |

The executor:
- downloads the key over HTTPS only and refuses it unless its
  fingerprint matches
- writes the key file
- **writes its own `.repo` file** (one stanza, `enabled=1`,
  `gpgcheck=1`, `gpgkey=file://<key_path>`), never Docker's downloaded
  `docker-ce.repo`, so exactly what is trusted is in the definition and
  the journal

It has the same journaled pre-state and backup rules as the apt step.

**rpm key database:** with `gpgcheck=1`, dnf imports the key into rpm's
database at the first install (a `gpg-pubkey-…` entry). The 4.33
package snapshot excludes `gpg-pubkey`. How DSSA handles this key is
decision §8 B.

**Rollback:** remove or restore the `.repo` file and the key file. dnf
ignores cached metadata of a repository that is no longer configured,
so no list refresh is needed (unlike apt in 4.35).

## 5. `docker_compose` component

The Compose plugin already arrived with Docker on ubuntu24 (apt pulled
it in as a recommended package). What the component should be is
decision §8 C.

## 6. Moving from draft to supported

Each variant stays `draft` until the project owner's lab run passes on
its VM. Only ubuntu24 and rocky10 are marked `"lab": true` today, so
`--allow-draft` would be refused on the other four. Decision §8 D.

## 7. Tests (planned)

- **Hermetic:**
  - definition validation for every variant (Bash and Python)
  - plan decisions per OS
  - the dnf vendor executor against mocks: fingerprint mismatch, the
    repo file content, and rollback of the repo file, key file and rpm key
  - the repository-pattern conflict in `/etc/yum.repos.d`
  - Compose's dependency on Docker (plan order; held when Docker is held)
- **Lab, with you running each step:**
  - Docker plus Compose on ubuntu22, ubuntu26, debian13, alma10 and rocky10
  - ubuntu24 as a verification-only run (Docker SKIP, Compose SKIP or a
    quick install)
  - one forced failure on alma10 with a test-only dnf vendor-repo
    definition, to prove the new rollback (repo file, key file and rpm
    key gone)

## 8. Decisions (2026-11-17)

- **A. Variant layout.**
  1. **Chosen:** one variant per OS release (`ubuntu22.04-…`,
     `ubuntu24.04-…`, `ubuntu26.04-…`, `debian13-…`, `el10-…`), each
     with its literal suite or baseurl. The data is longer, but every
     trusted URL is visible and reviewable, and there is no templating.
  2. One variant per family with a `{codename}` / `{major}`
     placeholder filled from `/etc/os-release`. The data is shorter,
     but the trusted URL is computed on the server.
- **B. The rpm signing key on Alma/Rocky.**
  1. **Chosen:** DSSA records rpm's `gpg-pubkey` entries before the
     install. If one is new after the run, rollback removes it
     (`rpm -e gpg-pubkey-…`). The key file is fingerprint-checked first,
     so dnf only ever imports the checked key.
  2. Leave imported keys in place and report them as "kept" (like
     upgraded packages in 4.33).
- **C. What `docker_compose` is.**
  1. **Chosen:** its own component that `depends_on` Docker
     (required), installs `docker-compose-plugin` (skipped if already
     present, 4.34.1) and verifies `docker compose version`. Selecting
     Compose alone auto-adds Docker (4.31).
  2. Verification-only: no install step; it reports whether the plugin
     already came with Docker.
  3. Merge it into the Docker component (always install the plugin).
- **D. Lab flags and lab state after the test.**
  1. **Chosen:** you add `"lab": true` to ubuntu22, ubuntu26,
     debian13 and alma10 (all six are lab VMs), we test with
     `--allow-draft`, and Docker plus Compose stay installed on all six
     as the base for later container milestones. Each variant is marked
     `supported` in one commit after its VM passes.
  2. The same flags, but each VM is reset after its test.
  3. Mark the variants `supported` first and test without
     `--allow-draft`.

Proposal, not a decision: Alma and Rocky use Docker's `linux/centos`
repository, which is what Docker's own instructions name for
RHEL-compatible systems. The `linux/rhel` path has the same key and
packages. It is one URL in each variant, so it can change without
redesign.

## 9. Not in 4.36

ARM or other architectures. Docker rootless mode. Docker daemon
configuration (`daemon.json`, log drivers). Adding users to the
`docker` group. Nginx and the other catalog components.

## 10. Build notes

- **ubuntu24 variant unchanged.** It was verified live in 4.34 and is
  `supported`, so it keeps its exact steps (no added `service_enable`).
  The four new variants have explicit `service_enable` + `service_start`.
- **OS version rule:** a version without a dot also matches its point
  releases (`10` matches `10.2`, never `100.1`). Versions with a dot
  (`24.04`) still match exactly. This is needed because Alma and Rocky
  report `VERSION_ID="10.2"`.
- **dnf schema replaced:** the 4.30 placeholder (`repo_url`: download the
  vendor's `.repo`) became `baseurl` + `key_path` + `repo_path`. The key
  must live in `/etc/pki/rpm-gpg/`, the repo file in `/etc/yum.repos.d/`,
  and `baseurl` must be a literal `https://` URL (no `$releasever`). The
  executor refuses if `/etc/yum.repos.d` is missing (it never creates it).
- **rpm keys** are recorded at the dnf vendor step (`pre.rpm_keys`).
  Rollback removes only `gpg-pubkey-<hex>-<hex>` entries that are new
  since then. A failed removal makes the rollback incomplete.
- **EL prerequisites** are `ca-certificates gnupg2`, not `curl`: asking dnf
  for `curl` where `curl-minimal` is installed would swap packages.
- **Compose verification** is the read-only hook `verify_docker_compose`
  (`docker compose version --short`), because `executable` checks allow
  only dashed arguments.
- The repository-pattern conflict check already searched
  `/etc/yum.repos.d` for dnf, so no change was needed there.


## 11. Found during the manual test (2026-11-18)

- **INSTALL → SKIP at re-check is not drift.** On ubuntu22, apt installed
  `docker-compose-plugin` together with Docker (a `Recommends` of
  `docker-ce-cli`). Compose's re-check then decided SKIP instead of the
  planned INSTALL, and the 4.31 drift rule skipped it. Decision (project
  owner): a planned INSTALL that is now SKIP with the **same variant** is
  verified only. This does less than was approved, so it stays inside the
  approval. The result carries the note "Planned as INSTALL, already
  installed at re-check; verified only." Every other change (another
  variant, SKIP → INSTALL, a new conflict, a failed re-check) is still
  `drift_skipped`. This applies whatever installed it (an earlier
  component or a person), because verifying changes nothing.
- **Stale verification-only plan:** `apply` now shows the preflight
  reason (e.g. "older than 30 minutes") instead of "No approval exists".
- **dnf weak dependencies:** on alma10, `docker-compose-plugin` pulled in
  `docker-buildx-plugin`. Snapshot-based rollback removed both.
- **EL: modules for the running kernel (alma10 failure).** On EL,
  `docker-ce` needs `kernel-modules-extra` (it carries `xt_addrtype`).
  alma10 ran kernel `6.12.0-211`, but the repositories only offered
  `kernel-modules-extra` for `6.12.0-264`, so dnf installed the whole
  new kernel beside the old one. `dockerd` then failed on
  `iptables -m addrtype` ("missing kernel module?"), and Docker was
  rolled back. Decision (project owner): **block at plan time.** A
  variant may list `requirements.running_kernel_packages` (dnf only; the
  EL Docker variant lists `kernel-modules-extra`). For an install
  candidate, the planner checks without root that each package is
  installed for `uname -r` or that the newest build in the repositories
  is for `uname -r` (`dnf repoquery --latest-limit 1`). Otherwise the
  component is `BLOCKED`: "Running kernel … is older than the newest …:
  update the server and reboot first". A check that cannot run also
  blocks. DSSA never reboots a server. An installed component (SKIP) is
  not checked. In the lab, rocky10 already had
  `kernel-modules-extra` for its running kernel and planned `INSTALL`
  (no kernel added); alma10 was `BLOCKED` until `dnf update` and a reboot.
- **Rollback of versions installed side by side.** The snapshot keyed
  packages by name.arch, so the new kernel looked like an upgrade and
  was left installed ("upgraded, not reverted"). Decision (project
  owner): **remove added versions.** On dnf each install step now also
  records every installed version (`*.versions` files). A version that
  is new while every older version of the same name.arch is still
  installed counts as added side by side: it is journaled as
  `added_versions`, it is no longer reported as an upgrade, and rollback
  removes that exact version (`dnf remove name-epoch:version-release.arch`)
  in the same transaction as the new packages. The running kernel is
  never removed; that case makes the rollback incomplete. apt has one
  version per package name, so nothing changes there.
- **EL: firewalld did not see iptables (alma10, second failure).** With
  the kernel current, `dockerd` still failed: "failed to create NAT chain
  DOCKER: COMMAND_FAILED: INVALID_IPV: 'ipv4' is not a valid backend or is
  unavailable". When firewalld runs, dockerd adds its rules through
  firewalld's direct interface, which needs the `iptables` commands.
  `iptables-nft` arrived in the same dnf transaction, and firewalld notices
  a new backend only when it starts or reloads. On rocky10 it worked by
  luck: dockerd created its saved `docker` zone and reloaded firewalld
  itself. On alma10 that zone was left over from the morning's failed run
  (it is saved config, so it survived the rollback and a reboot), so no
  reload happened. Decision (project owner): **both fixes.**
  - New step `firewalld_reload` with `when_packages_added` (the EL Docker
    variant lists `iptables-nft`, placed after the install and before
    `service_enable`). It runs `firewall-cmd --reload` only when firewalld
    is active and a listed package was absent from the run's snapshot and
    is installed now; otherwise it records why no reload was needed. The
    intent journals `{firewalld, packages_added, reload}`. A failed reload
    fails the step and rolls the component back. A reload is not undone:
    it only loads firewalld's saved configuration (runtime-only rules are
    dropped, as with any reload).
  - Every Docker variant lists `/etc/firewalld/zones/docker.xml` and
    `/etc/firewalld/policies/docker-forwarding.xml` in
    `rollback.created_paths`. When they were absent from the snapshot,
    rollback removes them; a file that existed before is left in place.
    After removing a firewalld file, or after a completed
    `firewalld_reload`, rollback runs `firewall-cmd --reload` when
    firewalld is active (action `reload firewalld`); a failed reload makes
    the rollback incomplete.
