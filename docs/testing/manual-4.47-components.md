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
| 16 | remove everything installed above with `plan --remove` | lab back to its start |

## Result

Not run yet.
