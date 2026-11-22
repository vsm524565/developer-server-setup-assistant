# Manual Test — Milestone 4.47 Components (when the VMs are free)

Every new component is a draft: plan with `--allow-draft` on lab targets,
type its name, approve, apply. One component per run keeps failures
readable. A passed component becomes supported afterwards.

Shorthand: `M` = `.venv/bin/python main.py --inventory config/inventory/lab-six.json`

Suggested order and VMs (apt: ubuntu24; dnf: alma10), one loop per check
where it repeats:

| # | Do | Checks |
|---|---|---|
| 1 | `.venv/bin/python -m unittest tests.backend.test_components tests.backend.test_component_definitions` | `OK` |
| 2 | `for t in ubuntu24 alma10; do M plan --allow-draft $t python nodejs certbot; done` | INSTALL or SKIP; certbot on alma10 via EPEL |
| 3 | apply those plans (approve each) | installed/verified, exit 0 |
| 4 | `M plan --allow-draft ubuntu24 nginx` → approve → apply; `curl -sI http://192.168.56.110/ \| grep -i ^server` | `Server: nginx` with no version (`server_tokens off` from the DSSA file) |
| 5 | `M plan --remove ubuntu24 nginx` → approve → apply | removed; unchanged `dssa-hardening.conf` removed |
| 6 | mariadb on ubuntu24 and alma10 | `bind-address = 127.0.0.1` in dssa-bind.cnf; `ss -ltn` shows 127.0.0.1:3306 only |
| 7 | postgresql on alma10 | `postgresql_initdb` ran; port 5432 on localhost |
| 8 | redis on ubuntu24, Valkey on alma10 | port 6379 on localhost |
| 9 | php on ubuntu24 and alma10 | `verify_php_fpm` / `php-fpm.service` active |
| 10 | fail2ban on ubuntu24 | `fail2ban-client status sshd` lists the jail |
| 11 | firewall on ubuntu24 (have a second session open first) | ufw active; `ufw status` shows 22/tcp, 80/tcp, 443/tcp; SSH still works |
| 12 | swap on ubuntu24 | `swapon --show` lists `/swapfile.dssa`; `/etc/fstab` unchanged |
| 13 | chrony on alma10 (SKIP expected); on ubuntu24 REVIEW (systemd-timesyncd) | as stated |
| 14 | mysql on ubuntu24 (after removing mariadb) | installed; on debian13 BLOCKED |
| 15 | apache on ubuntu24 (Nginx removed first) | port 80 served by apache2 |
| 16 | memcached, java, varnish, haproxy, postfix, clamav, composer on ubuntu24 and alma10 | each installed and verified; postfix on 127.0.0.1:25 only |
| 17 | caddy, mongodb on ubuntu24 and alma10 (Nginx/Apache removed first for Caddy) | vendor repository added, key fingerprint accepted, verified |
| 18 | php84 on ubuntu24 (Ondřej's PPA) and alma10 (Remi) | `php8.4-fpm` / `php84-php-fpm` active beside the OS PHP |
| 19 | opensearch on ubuntu24 (needs ≥ 4 GB RAM), then elasticsearch after removing it | OpenSearch: password file root-only, never in the run log; Elasticsearch: reset its password afterwards |
| 20 | openlitespeed on alma10 | `lshttpd` active, port 8088; 7080 closed from outside if the firewall is on |
| 21 | remove everything installed above with `plan --remove` | lab back to its start |

## Result

In progress (owner's lab run, 2026-11-21/22).

| Component | apt (ubuntu24) | dnf (alma10) | Notes |
|---|---|---|---|
| *web UI run* | | | From here on, through the web UI (`DSSA_WEBUI_HOME=~/dssa-lab-4.47`), one plan per VM, all six VMs added as lab servers with confirmed fingerprints |
| certbot, clamav, php, php82, php84, fail2ban, firewall, mariadb, nginx, postfix, redis, swap | installed (plan f34ad4d9, one approval for 15 components) | | swap: 3.8 GiB `/swapfile.dssa`; php83 verified (Ubuntu 24's own PHP 8.3); chrony held (systemd-timesyncd), as designed |
| composer | failed_rolled_back in f34ad4d9, then installed (plan 6b5d4185) | | **Finding:** as root, Composer prints two warnings before `Composer version 2.7.1`, and the version check read only the first line. Fixed: any output line may match |
| memcached, nodejs | installed (plan 6b5d4185), exit 0 | | |
| certbot, clamav, memcached, php82, php83, php84, postfix, redis (Valkey) | | installed (plan efc12d86) | Remi and EPEL repositories added; Valkey 8.1.10 |
| chrony, firewall, mariadb, nginx, nodejs, postgresql, python, swap | | verified (already present) | |
| php (OS), composer | | failed_rolled_back / not attempted | **Finding:** alma10 already had EL 10's parallel `php8.4-*` AppStream packages (installed 2026-11-19, not by these runs), which conflict with the default `php-*` 8.3. Fixed: the dnf `php` variant now lists `php8.4-common`/`php8.5-common` as conflicts, so it plans REVIEW. php, composer and fail2ban on dnf move to rocky10 |
| apache, haproxy, java, mysql, php83, postgresql, swap, varnish | installed on **ubuntu22** (plan 538f048a), exit 0 | | MySQL 8.0.46, OpenJDK 21.0.13, HAProxy 2.4.30; php83 from Ondřej's PPA |
| caddy, java, swap | installed on **ubuntu26** (plan 63283d07) | | Caddy from the cloudsmith repository, key accepted |
| mongodb, php84 | failed_rolled_back on ubuntu26 (plan 63283d07) | | **Finding:** both repositories have a `resolute` release file but publish no server packages for 26.04 (Ondřej's PPA: 0 packages; MongoDB: Atlas tools only), and MongoDB has none for Debian 13 either. Fixed: those variants removed (MongoDB gains Debian 12), so the plan says BLOCKED instead of failing mid-install |
| *debian13 plan* | | | Refused by the 4.39 clock check: debian13 was 456 s behind (limit 300 s). **Finding:** the web UI showed only "Planning failed (exit 3)". Fixed: the plan step keeps the backend's error lines and the page shows them |
| chrony, memcached, php82 | installed on **debian13** (plan 3fb4484c) | | chrony gave debian13 a time service (it had none; that is why its clock drifted); php82 from sury |
| mongodb | BLOCKED on debian13, as intended | | "MongoDB 8.0 requires: ubuntu 22.04; ubuntu 24.04; debian 12; … detected debian 13" |
| openlitespeed | failed_rolled_back on debian13 (plan 3fb4484c) | | **Finding:** on Debian, the package's `rc-inst.sh` installs `/etc/init.d/lsws` and never creates `lshttpd.service`. Fixed: the unit is `lsws.service` everywhere (Ubuntu and EL have it as a link) |
| python | installed (plan 4f148d98), exit 0, Python 3.12.3 | installed, exit 0, Python 3.12.16 (python3 already there, pip added) | The first ubuntu24 apply was refused: an interrupted nodejs run (187d4615) left an unfinished transaction. It was rolled back; `report` then showed `DSSA_STATUS none` |

