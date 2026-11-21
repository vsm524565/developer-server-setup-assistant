# Component Definitions — Design Draft (Milestone 4.30)

Status: **Implemented in 4.30 (loader, validation, single-component
planner)**. The engine is not yet wired into setup; that happens in
4.31–4.34.

Implementation:
- `bridge/components/contracts.py`, `bridge/components/loader.py`: Python models and validation.
- `backend/linux/engine/definitions.sh`: jq validation, fact collection and the pure decision function.
- `tests/backend/test_component_definitions.py`, `tests/backend/test_definition_planner.py`.

## 1. Problem

Each component's rules are hand-written in up to three places:

| Fact | Python recipe | Bash planner | Bash installer |
|---|---|---|---|
| Docker packages | yes | yes | yes |
| Docker conflicting packages | – | yes | yes (copy) |
| Docker existing-state paths | – | yes | yes (copy) |
| Nginx port 80/443 and panel checks | – | yes | yes (copy) |
| Verification | description only | – | Bash functions |

At about 25 components plus panels, these copies drift apart. Two bugs
already come from per-component code that bypasses shared helpers:

- `verify_docker` prints `docker version`/`docker info` to stdout.
- `install_docker` calls `apt-get` directly, so APT output goes to stdout.

Both break the "exactly one JSON result on stdout" contract that
`operations/setup.sh` enforces.

## 2. Decisions already made

| Decision | Choice |
|---|---|
| Format and location | One JSON file per component, shared by Bash (jq) and Python (dataclasses) |
| Non-data steps | Fixed allowlist of step types plus optional named Bash hooks |
| Rollback journal | On the target in `/var/lib/dssa/transactions/<run-id>/`, copy returned to the laptop |

## 3. Layout

```text
definitions/
├── components/
│   ├── docker.json
│   ├── git.json
│   └── nginx.json
└── README.md                 (later: field reference)
backend/linux/engine/         (4.31+: generic plan/apply/verify/rollback)
backend/linux/hooks/<id>.sh   (only for components that need hooks)
```

`definitions/` sits at the repo root because it belongs to neither layer
alone. Only the definitions and hooks for the selected components are
sent to the target.

## 4. Top-level fields

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `"1.0"` | Definition format version |
| `id` | string | Stable component ID; must match the file name and the catalog |
| `display_name` | string | Human name for reports |
| `category` | string | Same categories as `bridge/applications/definitions/` |
| `depends_on` | list | Other component IDs that must be ready first (see §8) |
| `variants` | list | One entry per supported install method/platform; the first match wins |

## 5. Variant fields

A variant is one installation method for a set of platforms.

| Field | Meaning |
|---|---|
| `variant_id` | Stable ID, e.g. `ubuntu24-amd64-vendor` |
| `support` | `draft` (plan only) or `supported` (apply allowed). Becomes `supported` only after lab-VM validation |
| `platform` | `package_manager` (required) selects the variant. `os` and `architectures` are optional and used only when the component needs them (see §5.1) |
| `detection` | What "already installed" means. `packages_all`: every listed package installed → SKIP, then verify |
| `conflicts` | Evidence that forces REVIEW instead of INSTALL (see §6) |
| `steps` | Ordered install steps from the allowlist (see §7) |
| `verification` | Checks after install and on SKIP (see §9) |
| `rollback` | `created_paths`: filesystem paths the install may create, so the pre-run snapshot knows which paths to record (see §7.1) |
| `hooks` | Optional named Bash functions: `precheck`, `verify` |

### 5.1 Platform matching

Variants are chosen by **package manager**, not a hard-coded OS list.
The OS is considered only when the component actually depends on it.

```json
"platform": { "package_manager": "apt" }
```

A component that needs a specific OS adds explicit pairs:

```json
"platform": {
  "package_manager": "apt",
  "os": [{ "id": "ubuntu", "versions": ["24.04"] }],
  "architectures": ["x86_64"]
}
```

- Git and Nginx use OS repositories, so they match on package manager alone.
- Docker's vendor repository is Ubuntu-release-specific (`noble`), so it
  declares the OS and architecture.
- `os` is a list of ID-and-versions pairs, so a version is only valid
  for its own OS.

## 6. Conflicts

Any match means **REVIEW**, with every matching reason listed.

- `packages` conflicts are **always** checked, even when the component
  is installed (a conflicting package next to a full install still needs
  review, as the legacy Docker planner did).
- All other conflicts are checked only when `detection` says the
  component is not installed, because a working install creates exactly
  that state (executables, paths, units, open ports).

### 6.1 Facts, then decision

Planning is split in two:

1. **Collect facts** (`collect_platform_facts`, `collect_variant_facts`):
   read-only probes of only the items the selected variant names. Files
   are probed under `DSSA_ROOT` (empty in production, a temp dir in
   tests). `os-release` is parsed, never sourced.
2. **Decide** (`decide_component`): a pure jq function. Definition plus
   facts gives the plan entry, with no probing, so every rule can be
   tested without a server.

The same fact format will feed the pre-run snapshot in 4.33.

| Key | Matches when |
|---|---|
| `packages` | Any listed package is installed |
| `partial_packages` | Some but not all `detection.packages_all` are installed (boolean) |
| `unmanaged_executables` | The command exists on PATH but detection says not installed |
| `paths` | The file, directory or symlink exists |
| `repository_patterns` | The text appears in APT sources or DNF repo files |
| `systemd_units` | The unit file is known to systemd |
| `tcp_ports` | The port is listening |
| `panels` | `"any"`: any detected control panel; or a list of panel IDs |

The same checks run at plan time and again on the target right before
apply, so state that changes in between is caught. This replaces the
copied checks in today's planners and installers.

## 7. Step types (allowlist)

| `type` | Parameters | Journal records |
|---|---|---|
| `package_metadata_refresh` | – | nothing (no persistent change) |
| `packages_install` | `packages`, `role`: `prerequisite`/`main` | each package with was-installed state |
| `apt_vendor_repository` | `repository_id`, `key_url`, `key_fingerprint`, `key_path`, `source_path`, `uris`, `suites`, `components`, `architectures` | key and source files with was-present state |
| `dnf_vendor_repository` | `repository_id`, `repo_url`, `key_url`, `key_fingerprint`, `repo_path` | repo file with was-present state |
| `service_enable` | `unit` | was-enabled state |
| `service_start` | `unit` | was-active state |
| `managed_file` | `path`, `template`, `mode`, `owner` | backup of the original file, or was-absent |
| `service_reload` | `unit`, optional `config_test` (`nginx -t`) | reload again after the files are removed (4.45) |
| `hook` | `name` (declared as `hooks.setup`) | what it creates is in `rollback.created_paths` (4.47) |
| `firewall_enable` | `allow` (`ssh` required; `http`, `https`) | firewall off again if it was off; added rules deleted (4.47) |
| `swap_file` | `path` (`/swapfile.dssa`) | swap unit stopped and removed, file removed (4.47) |

Detection (4.47): `packages_all`, or `state` (`swap_active` | `firewall_active`)
with `packages_all: []`.
| `hook` | `name` | whatever the hook journals through the shared journal functions |

Rules for every step type:

- All command output goes to stderr; only the final result goes to stdout.
- No fallback to another package manager or method on failure.
- Before a step changes anything, it writes its pre-state to the journal.
  Unknown pre-state means refuse (the InfraGuard pattern).

### 7.1 Rollback restores the pre-run snapshot

Before installation starts, DSSA records which packages are installed
and which services are active and enabled, plus the state of each
component's `created_paths`. Rollback of a failed component returns
everything it touched to that recorded state:

- Packages absent before the run are removed, including prerequisites
  such as `curl` or `gnupg`. Packages present before are left alone.
- Services are returned to their recorded active/enabled state.
- Files and directories absent before are removed; files that existed
  are restored from backup.
- Anything whose pre-run state is unknown is not touched and is
  reported for review.

`created_paths` exists because the whole filesystem can't be
snapshotted; it tells the snapshot which paths to record for each
component.

## 8. Dependencies

`depends_on: [{"id": "docker", "required": true}]`

- The planner orders components so dependencies come first.
- If a dependency is REVIEW, BLOCKED or fails during apply, its
  dependents are **skipped** with a reason. Independent components
  continue. This is your multi-component rule.
- Optional dependencies (`required: false`) affect ordering only.
- Cycles are rejected when definitions are loaded.

## 9. Verification types

| `type` | Parameters |
|---|---|
| `packages_installed` | `packages` |
| `executable` | `name`, optional `version_args` (output to stderr), optional `version_pattern` (4.39: a POSIX extended regex, at most 200 characters, that the first output line must match; needs `version_args`) |
| `service_active` | `unit` |
| `service_enabled` | `unit` |
| `tcp_listening` | `port` |
| `http_local` | `port`, `expect_status` (e.g. `2xx`) |
| `config_test` | `command` from an allowlist, e.g. `nginx -t` |
| `hook` | `name` |

## 10. Plan actions

| Action | When |
|---|---|
| `SKIP` | Detected as installed and verification passes |
| `INSTALL` | Not detected, no conflicts, variant is `supported` |
| `REVIEW` | Conflicts found, verification of an existing install fails, or the variant is only `draft` |
| `BLOCKED` | No variant matches the package manager (or the OS, where declared), or a required dependency is not plannable |

## 11. Validation on load

Python, at load time (extends `bridge/applications/installation/contracts.py`):
- File name equals `id`, and `id` exists in the application catalog.
- Only known fields and step/verification types are allowed.
- Packages, paths and units match safe patterns (no spaces, shell
  metacharacters or relative paths).
- Fingerprints are 40 hex characters; URLs are `https://` only.
- Dependencies exist and have no cycles.

Bash, before using a definition on the target: a jq structural check of
the same rules. The backend validates independently, as the
architecture requires.

## 12. Review decisions (2026-11-15)

1. **Nginx: deferred.** Nginx install and verification work hasn't
   started, so its SKIP verification behavior is decided when that
   work begins. The draft definition records today's checks only.
2. **Rollback follows the pre-run snapshot** (§7.1), including
   prerequisites and services.
3. **Only Git is `supported`.** Every other definition stays `draft`
   until its install is validated on the lab VMs.
4. **Matching is by package manager**, with the OS considered only
   when the component needs it (§5.1).

## 13. Out of scope for 4.30

Generic planner (4.31), approval records (4.32), journal and rollback
engine (4.33), unified `main.py` (4.34). 4.30 delivers the format,
loaders, validation, and the Git/Nginx/Docker definitions, with tests
showing they reproduce today's planner decisions.
