#!/usr/bin/env bash

# Multi-component, dependency-aware planning (Milestone 4.31).
#
# Laptop side:  build_plan_request  selection + definitions -> request
# Target side:  build_definition_plan  request + discovery -> plan
#
# Ordering: dependencies first; otherwise the operator's selection order.
# Required dependencies that were not selected are added and marked.
# A component whose required dependency is not runnable is BLOCKED;
# independent components are unaffected (partial plans are allowed).
#
# Read-only. Requires definitions.sh (4.30) to be loaded first, plus jq
# and sha256sum. Each public function prints one JSON value on stdout.

# Pure jq: resolve the dependency closure and order it.
# Input: {"definitions": {id: definition}, "selection": [id, ...]}
# Output: [{"id": ..., "added_as_dependency": bool}, ...] in plan order.
DSSA_PLAN_ORDER_JQ='
def required_deps($defs; $id): [$defs[$id].depends_on[] | select(.required != false) | .id];
def all_deps($defs; $id): [$defs[$id].depends_on[] | .id];

def check_known($defs; $ids; $what):
  ([$ids[] | select($defs[.] == null)]) as $missing
  | if ($missing | length) > 0
    then error("unknown \($what): \($missing | join(", "))") else . end;

# Required-dependency closure, so optional dependencies are never added.
def closure($defs; $selection):
  def add($id):
    if index($id) then .
    else . + [$id]
      | check_known($defs; required_deps($defs; $id); "dependency of \($id)")
      | reduce required_deps($defs; $id)[] as $dep (.; add($dep))
    end;
  reduce $selection[] as $id ([]; add($id));

# Depth-first post-order over the selection: each dependency is placed
# before its dependents; unrelated components keep selection order.
# Optional dependencies order components only when both are present.
def ordered($defs; $members; $selection):
  def visit($id):
    if (.seen | index($id)) then .
    else .seen += [$id]
      | reduce (all_deps($defs; $id)[] | select(. as $d | $members | index($d))) as $dep
          (.; visit($dep))
      | .out += [$id]
    end;
  reduce $selection[] as $id ({seen: [], out: []}; visit($id)) | .out;

.definitions as $defs
| (.selection | reduce .[] as $s ([]; if index($s) then . else . + [$s] end)) as $selection
| check_known($defs; $selection; "component")
| closure($defs; $selection) as $members
| ordered($defs; $members; $selection)
| map({id: ., added_as_dependency: (. as $id | $selection | index($id) == null)})
'

# Pure jq: apply dependency holds to per-component plan entries.
# Input: {"order": [...from DSSA_PLAN_ORDER_JQ], "entries": {id: entry},
#         "definitions": {id: definition}}
# Output: steps array with depends_on, added_as_dependency and blocked_by.
DSSA_PLAN_PROPAGATE_JQ='
.definitions as $defs | .entries as $entries
| reduce .order[] as $o ({held: [], steps: []};
    . as $acc
    | $entries[$o.id] as $e
    | [$defs[$o.id].depends_on[] | select(.required != false) | .id] as $required
    | [$required[] | . as $dep | select(($acc.held | index($dep)) != null)] as $blockers
    | ($e + {added_as_dependency: $o.added_as_dependency,
             depends_on: [$defs[$o.id].depends_on[] | .id],
             blocked_by: $blockers}) as $step
    | if ($blockers | length) > 0 then
        .held += [$o.id]
        | .steps += [$step
            | .action = "BLOCKED"
            | .reasons += ["Required dependency not runnable: \($blockers | join(", "))."]]
      elif ($e.action == "INSTALL" or $e.action == "SKIP") then
        .steps += [$step]
      else
        .held += [$o.id] | .steps += [$step]
      end)
| .steps
'

DSSA_PLAN_ID_RE='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
DSSA_PLAN_TIME_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'
DSSA_TARGET_ID_RE='^[a-z0-9][a-z0-9_-]{0,63}$'
DSSA_HOST_KEY_RE='^SHA256:[A-Za-z0-9+/]{43}$'

_dssa_sha256() {
    printf '%s' "$1" | sha256sum | awk '{ print "sha256:" $1 }'
}

# Laptop side. Usage:
#   build_plan_request TARGET_ID HOST_KEY_SHA256 DEFINITIONS_DIR COMPONENT...
# Validates every definition, resolves the dependency closure and embeds
# only the definitions the plan needs, each with its SHA-256.
build_plan_request() {
    local target_id="$1"
    local host_key="$2"
    local definitions_dir="$3"
    shift 3

    local file content definitions='{}' contents='{}' order component
    local plan_id created_at

    [[ $# -gt 0 ]] || { echo "ERROR: No components selected." >&2; return 2; }
    [[ "$target_id" =~ $DSSA_TARGET_ID_RE ]] ||
        { echo "ERROR: Invalid target ID." >&2; return 2; }
    [[ "$host_key" =~ $DSSA_HOST_KEY_RE ]] ||
        { echo "ERROR: Invalid host key fingerprint." >&2; return 2; }

    for component in "$@"; do
        [[ "$component" =~ ^[a-z][a-z0-9_-]{0,63}$ ]] || {
            printf 'ERROR: Invalid component ID: %s\n' "$component" >&2
            return 2
        }
    done

    for file in "$definitions_dir"/*.json; do
        [[ -f "$file" && ! -L "$file" ]] || continue
        content="$(cat -- "$file")"
        validate_component_definition "$content" || {
            printf 'ERROR: Invalid definition: %s\n' "${file##*/}" >&2
            return 2
        }
        jq -e --arg id "$(basename -- "$file" .json)" '.id == $id' \
            <<< "$content" >/dev/null || {
            printf 'ERROR: Definition ID does not match file: %s\n' "${file##*/}" >&2
            return 2
        }
        definitions="$(jq -c --argjson d "$content" '. + {($d.id): $d}' <<< "$definitions")"
        contents="$(jq -c --arg id "$(basename -- "$file" .json)" --arg c "$content" \
            '. + {($id): $c}' <<< "$contents")"
    done

    order="$(
        jq -c "$DSSA_PLAN_ORDER_JQ" 2>&1 <<< "$(
            jq -cn --argjson defs "$definitions" \
                '{definitions: $defs, selection: $ARGS.positional}' \
                --args "$@"
        )"
    )" || { printf 'ERROR: Selection could not be resolved: %s\n' "$order" >&2; return 2; }

    plan_id="$(cat /proc/sys/kernel/random/uuid)" || return 1
    created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)" || return 1

    # Embed each needed definition as its exact text plus its digest.
    local -a entries=()
    local id
    while IFS= read -r id; do
        content="$(jq -r --arg id "$id" '.[$id]' <<< "$contents")"
        entries+=("$(jq -cn --arg id "$id" --arg c "$content" \
            --arg h "$(_dssa_sha256 "$content")" \
            '{id: $id, sha256: $h, content: $c}')")
    done < <(jq -r '.[].id' <<< "$order")

    printf '%s\n' "${entries[@]}" | jq -cs \
        --arg plan_id "$plan_id" --arg created_at "$created_at" \
        --arg target_id "$target_id" --arg host_key "$host_key" \
        '{schema_version: "1.0", plan_id: $plan_id, created_at: $created_at,
          target: {target_id: $target_id, host_key_sha256: $host_key},
          selection: ($ARGS.positional
                      | reduce .[] as $s ([]; if index($s) then . else . + [$s] end)),
          definitions: .}' \
        --args "$@"
}

# Target side. Usage:
#   build_definition_plan REQUEST_JSON MANAGER PANEL_FACTS PORT_FACTS
build_definition_plan() {
    local request="$1"
    local manager="$2"
    local panel_facts="$3"
    local port_facts="$4"

    local id content expected actual definitions='{}' order entry entries='{}'

    jq -e \
        --arg plan_re "$DSSA_PLAN_ID_RE" --arg time_re "$DSSA_PLAN_TIME_RE" \
        --arg target_re "$DSSA_TARGET_ID_RE" --arg key_re "$DSSA_HOST_KEY_RE" '
        type == "object"
        and ((keys - ["schema_version","plan_id","created_at","target",
                      "selection","definitions"]) | length == 0)
        and .schema_version == "1.0"
        and (.plan_id | type == "string" and test($plan_re))
        and (.created_at | type == "string" and test($time_re))
        and (.target | type == "object"
             and ((keys - ["target_id","host_key_sha256"]) | length == 0)
             and (.target_id | type == "string" and test($target_re))
             and (.host_key_sha256 | type == "string" and test($key_re)))
        and (.selection | type == "array" and length > 0
             and all(.[]; type == "string" and test("^[a-z][a-z0-9_-]{0,63}$")))
        and (.definitions | type == "array" and length > 0
             and all(.[]; type == "object"
                 and ((keys - ["id","sha256","content"]) | length == 0)
                 and (.id | type == "string") and (.content | type == "string")
                 and (.sha256 | type == "string" and test("^sha256:[0-9a-f]{64}$"))))
        and ([.definitions[].id] | length == (unique | length))
    ' <<< "$request" >/dev/null 2>&1 || {
        echo "ERROR: Invalid plan request." >&2
        return 2
    }

    # Every embedded definition must match its digest and validate again.
    while IFS= read -r id; do
        content="$(jq -r --arg id "$id" '.definitions[] | select(.id == $id) | .content' <<< "$request")"
        expected="$(jq -r --arg id "$id" '.definitions[] | select(.id == $id) | .sha256' <<< "$request")"
        actual="$(_dssa_sha256 "$content")"
        [[ "$actual" == "$expected" ]] || {
            printf 'ERROR: Definition digest mismatch: %s\n' "$id" >&2
            return 2
        }
        validate_component_definition "$content" || return 2
        jq -e --arg id "$id" '.id == $id' <<< "$content" >/dev/null || {
            printf 'ERROR: Definition ID mismatch: %s\n' "$id" >&2
            return 2
        }
        definitions="$(jq -c --argjson d "$content" '. + {($d.id): $d}' <<< "$definitions")"
    done < <(jq -r '.definitions[].id' <<< "$request")

    # Recompute the order here rather than trusting the laptop's.
    order="$(
        jq -c --argjson defs "$definitions" \
            '{definitions: $defs, selection: .selection}' <<< "$request" |
            jq -c "$DSSA_PLAN_ORDER_JQ" 2>/dev/null
    )" || { echo "ERROR: Selection could not be resolved." >&2; return 2; }

    # The request must contain exactly the definitions the plan needs.
    jq -e --argjson order "$order" \
        '([.definitions[].id] | sort) == ([$order[].id] | sort)' \
        <<< "$request" >/dev/null || {
        echo "ERROR: Plan request definitions do not match the dependency closure." >&2
        return 2
    }

    while IFS= read -r id; do
        entry="$(
            plan_component_from_definition \
                "$(jq -c --arg id "$id" '.[$id]' <<< "$definitions")" \
                "$manager" "$panel_facts" "$port_facts"
        )" || return $?
        entries="$(jq -c --arg id "$id" --argjson e "$entry" '. + {($id): $e}' <<< "$entries")"
    done < <(jq -r '.[].id' <<< "$order")

    jq -cn \
        --argjson request "$request" \
        --argjson definitions "$definitions" \
        --argjson order "$order" \
        --argjson entries "$entries" \
        --arg manager "$manager" \
        "{order: \$order, entries: \$entries, definitions: \$definitions}
         | $DSSA_PLAN_PROPAGATE_JQ
         | . as \$steps
         | {schema_version: \"1.0\",
            plan_id: \$request.plan_id,
            created_at: \$request.created_at,
            target: \$request.target,
            package_manager: \$manager,
            selection: \$request.selection,
            definitions: (\$request.definitions | map({(.id): .sha256}) | add),
            steps: \$steps,
            runnable: [\$steps[] | select(.action == \"INSTALL\" or .action == \"SKIP\") | .component],
            held: [\$steps[] | select(.action != \"INSTALL\" and .action != \"SKIP\")
                   | {component, action, reasons, blocked_by}],
            planning_only: true,
            execution_allowed: false}"
}
