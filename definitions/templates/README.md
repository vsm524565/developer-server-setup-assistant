# Templates for managed files (Milestone 4.45)

A definition's `managed_file` step names a template here by its path, for
example `"template": "nginx/default.conf"` for `nginx/default.conf`.

- A template is plain text, written as it is (no variables). Trailing
  newlines are ignored and the written file ends with exactly one.
- Its SHA-256 is bound into the plan, like the definitions: a template
  changed after planning refuses approve and apply.
- DSSA writes only new files named `dssa-*`, never over an existing file
  (application flow, decision 3). After install the file is the
  operator's; uninstall removes it only if it is unchanged.
- Same permission rules as the definitions folder (4.35).
- `DSSA_TEMPLATES_DIR` overrides this folder.
