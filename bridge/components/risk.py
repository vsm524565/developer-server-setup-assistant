"""Which planned steps need the component's name typed (decision 4).

Shared by the command line (main.py) and the web UI, so both ask for the
same confirmations:
- REMOVE: always;
- ADOPT: always (it changes what DSSA may later remove);
- UPDATE: when it restarts a running service or cannot be undone;
- INSTALL of a control panel (4.48): always.
Drafts are confirmed at plan time (4.34) and are not listed here.
"""


def risky_steps(plan):
    """[(component, verb, [warnings])] for the runnable steps that need a typed name."""

    out = []
    for step in plan["steps"]:
        if step["component"] not in plan["runnable"]:
            continue
        action, details = step["action"], step.get("details") or {}
        warnings = []
        if action in ("REMOVE", "UPDATE") and not details.get("reversible", True):
            what = "removal" if action == "REMOVE" else "update"
            warnings.append(f"This {what} is NOT reversible (no package file for "
                            + ", ".join(f"{u['package']} {u['version']}" for u in details["unavailable"]) + ").")
        if action == "UPDATE" and details.get("running_units"):
            warnings.append(f"The update restarts running service(s): {', '.join(details['running_units'])}.")
        if action == "INSTALL" and details.get("panel"):
            # Panels (4.48): not reversible unless the vendor has an uninstaller.
            if not details.get("reversible"):
                warnings.append("Not reversible: the panel takes over the server. Take a VM snapshot first.")
            out.append((step["component"], "install this control panel", warnings))
            continue
        if action == "REMOVE":
            out.append((step["component"], "remove it", warnings))
        elif action == "ADOPT":
            out.append((step["component"], "let DSSA manage (and later remove) it", warnings))
        elif action == "UPDATE" and warnings:
            out.append((step["component"], "update it", warnings))
    return out
