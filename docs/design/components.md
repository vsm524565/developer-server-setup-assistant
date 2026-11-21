# Components — Milestone 4.47

The project owner chose every component except web-hosting panels, with
MariaDB as the default database (2026-11-21). All 13 new definitions start
as **draft**: they are applied only on lab targets with `--allow-draft` and
the typed name, and each becomes **supported** after its lab run, the same
rule as Docker (4.34). Choices marked *(changeable)* were taken as the safer
option without a separate decision.

All packages come from the OS repositories, except where EPEL is noted.
Nothing listens on a public interface unless noted.

| Component | apt (Ubuntu 22/24/26, Debian 13) | dnf (Alma/Rocky 10) | Notes |
|---|---|---|---|
| `nginx` | nginx | nginx | Writes `conf.d/dssa-hardening.conf` (`server_tokens off`), then `nginx -t` and reload. Ports 80/443 must be free |
| `apache` | apache2 | httpd | Ports 80/443 must be free, so Nginx and Apache together need a choice of ports first *(changeable)* |
| `certbot` | certbot | certbot from **EPEL** (`epel-release` installed first) | Installs only. Issuing a certificate is a separate, explicit step later |
| `mariadb` (**default**) | mariadb-server | mariadb-server | Writes `dssa-bind.cnf` with `bind-address = 127.0.0.1` (EL listens on all interfaces otherwise). Conflicts with MySQL |
| `mysql` | mysql-server, **Ubuntu only** (Debian has none) | mysql8.4-server | Conflicts with MariaDB. The EL package name is checked in the lab |
| `postgresql` | postgresql | postgresql-server, then setup hook `postgresql_initdb` | localhost only (the default) |
| `redis` | redis-server | **valkey** (EL 10 ships Valkey instead of Redis) | localhost only (the default) |
| `php` | php-fpm, -cli, -mysql, -curl, -mbstring, -xml, -zip | php-fpm, -cli, -mysqlnd, -mbstring, -xml | apt: the package starts `php<ver>-fpm`, checked by hook `verify_php_fpm` |
| `nodejs` | nodejs, npm | nodejs, npm | OS repository version, not NodeSource *(changeable)* |
| `python` | python3, python3-venv, python3-pip | python3, python3-pip | Usually SKIP: python3 is there already |
| `firewall` | ufw | firewalld | Step `firewall_enable`: allows the ports sshd really listens on, plus 80/443, **before** turning the firewall on. Refuses if the SSH port can't be found *(the web ports are changeable)* |
| `fail2ban` | fail2ban, python3-systemd | the same, from **EPEL** | Writes `jail.d/dssa-sshd.local` (sshd jail, systemd backend), then reloads |
| `swap` | — | — | Step `swap_file`: `/swapfile.dssa`, 2× RAM up to 2 GiB, then = RAM, at most 8 GiB. Needs free disk of size + 20% + 1 GiB. Its own unit `swapfile.dssa.swap`; `/etc/fstab` is never edited |
| `chrony` | chrony | chrony (`chronyd.service`) | REVIEW where `systemd-timesyncd` (Ubuntu/Debian default), ntp or ntpsec is installed: one time service is enough |

## Engine additions

- **Detection by state:** `detection.state` is `swap_active` (`/proc/swaps`)
  or `firewall_active` (ufw's `ufw.conf` on apt, firewalld's unit on dnf),
  read without sudo. With a state, `packages_all` is `[]`. A package-based
  check would call ufw "installed" on Ubuntu, where it is present but off.
- **New step types:**
  - `firewall_enable` (`allow` must include `ssh`)
  - `swap_file` (path fixed to `/swapfile.dssa`)
  - `hook` steps, for hooks declared as `setup`. What a setup hook creates
    must be in `rollback.created_paths`.
- **Undo:** the firewall is turned off again if it was off, and every rule
  DSSA added is deleted. The swap unit is stopped and removed, then the
  file. Both happen before packages are removed.
- **New hooks** (`engine/hooks/components.sh`): `postgresql_initdb`,
  `verify_firewall_active`, `verify_swap_active`, `verify_php_fpm`. Hooks
  read the transaction's package manager (`DSSA_MANAGER`).
- **Templates:** `nginx/hardening.conf`, `mariadb/bind.cnf`,
  `fail2ban/sshd.local`.

## Checked in the lab, not here

The mocked tests install and verify every component on apt and dnf. The
following depend on the real distributions, and the lab run settles them:

- the package names on EL 10 (`mysql8.4-server`, `valkey`), and EPEL being
  reachable;
- the version-output patterns;
- services that the packages already start;
- `python3-systemd` being available for Fail2ban.
