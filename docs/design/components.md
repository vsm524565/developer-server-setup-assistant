# Components — Milestone 4.47 (three batches, 32 definitions)

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

## Second batch: OS repositories (2026-11-21)

| Component | apt | dnf | Notes |
|---|---|---|---|
| `memcached` | memcached | memcached | localhost only (the default), port 11211 |
| `java` | openjdk-21-jdk-headless | java-21-openjdk-headless | OpenJDK 21 LTS |
| `varnish` | varnish | varnish | port 6081; put in front of a web server by hand |
| `haproxy` | haproxy | haproxy | packaged default config only |
| `postfix` | postfix: the setup hook `postfix_local_only` answers the installer with "Local only" first | postfix (EL's default config is localhost only) | outgoing mail only; no config file is edited. REVIEW if exim4 or sendmail is installed |
| `clamav` | clamav, clamav-freshclam | the same, from EPEL | signature updates through `clamav-freshclam.service`; the scanning daemon is not installed |
| `composer` | composer | composer, from EPEL | needs `php` (a required dependency, so it's added to the plan) |

maldet (Linux Malware Detect) is not included: the owner chose ClamAV
instead (maldet has no package, only a vendor `install.sh` run as root).

## Third batch: vendor repositories (2026-11-21)

Every key was downloaded over HTTPS and its fingerprint read with
`gpg --show-keys`. Then the repository's own signature (`InRelease`,
`Release.gpg` or `repomd.xml.asc`) was verified against that key alone.
LiteSpeed's and Remi's rpm metadata is unsigned, so there the signature of
a real package was checked instead.

| Component | Repository | Key fingerprint | Checked against |
|---|---|---|---|
| `caddy` | dl.cloudsmith.io caddy/stable (deb `any-version`; rpm el/10) | `65760C51EDEA2017CEA2CA15155B6D79CA56EA34` | InRelease and repomd.xml.asc |
| `openlitespeed` | rpms.litespeedtech.com (debian per release; centos/10) | `3E892522DB44E1B063D366C5011AA62DEDA1F085` (`lst_repo.gpg`) | noble Release.gpg; signature of openlitespeed-1.8.5 el10 |
| `mongodb` | repo.mongodb.org 8.0 (ubuntu jammy/noble/resolute, debian trixie, redhat 10) | `4B0752C1BCA238C0B4EE14DC41DE058A4E7DCA05` | InRelease and repomd.xml.asc |
| `elasticsearch` | artifacts.elastic.co packages/9.x | `46095ACC8548582C1A2699A9D27D666CD88E42B4` | InRelease and repomd.xml.asc |
| `opensearch` | artifacts.opensearch.org opensearch/3.x | `A8B2D9E04CD51FEF6AA2DB53BA81D99981191457` | InRelease and repomd.xml.asc |
| `php82`/`php83`/`php84` on Ubuntu | Ondřej Surý's PPA | `B8DC7E53946656EFBCE4C1DD71DAEAAB4AD4CAB6` (from keyserver.ubuntu.com) | noble InRelease |
| … on Debian 13 | packages.sury.org/php | `15058500A0235D97F5D10063B188E2B695BD4743` | trixie InRelease |
| … on EL 10 | rpms.remirepo.net enterprise/10/safe (plus EPEL) | `CF1DF0057CE85DFF5B2F2A37C2FD3B2C2A0948E4` (remi2024) | signature of remi-release-10 |

Notes:
- **Binary keys:** LiteSpeed's and sury's keys are binary. apt reads them
  as `.gpg`. For rpm, the dnf repository step now re-exports a binary key
  in ASCII armour, exporting only the verified fingerprint.
- **Formats:** an apt suite may now contain path parts (MongoDB's
  `noble/mongodb-org/8.0`). A `key_url` may carry a simple query string
  (the keyserver lookup). Repository IDs have no dots.
- **OpenSearch 3** needs an initial admin password to install. The setup
  hook `opensearch_admin_password` generates a random one, stores it
  root-only in `/root/.dssa/opensearch-initial-admin-password`, and passes
  it to the install through the environment. It is never printed or
  logged.
- **Elasticsearch 9** generates its `elastic` password during install and
  prints it in the package output, so the password ends up in the run log
  (root-only on the server, and in the private run copy on the laptop).
  Reset it after install with `elasticsearch-reset-password -u elastic`.
  Elasticsearch and OpenSearch conflict on port 9200 and with each other's
  packages.
- **OpenLiteSpeed's** admin console listens on 7080 on all interfaces. The
  `firewall` component allows only SSH, 80 and 443, so it stays closed
  from outside until you open it.
- **`php82`/`php83`/`php84`** install side by side with the OS PHP:
  - apt: `php8.x-fpm.service`;
  - EL: Remi's `php8x-php-fpm.service` and binary `php8x`.

  The PHP versions share one repository. Remove the component that added
  the repository last, because removing it also removes the repository
  file.

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
