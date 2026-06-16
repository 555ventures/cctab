#!/usr/bin/env bash
# Mechanical shortcut-pattern sweep — deterministic input to /spec:review.
# Usage: [DIFF_BASE=<ref>] scripts/spec-patterns.sh [dir ...]    (defaults to src/cctop tests)
# Pure report: always exits 0. Sanctioned exceptions exist — the reviewer judges; this only counts.
set -u
DIRS=("$@")
[ ${#DIRS[@]} -eq 0 ] && DIRS=(src/cctop tests)
echo "## Mechanical pattern sweep"; echo "Scope: ${DIRS[*]}"; echo
sweep() {
  local name="$1"; shift
  local out; out=$(rg -n "$@" "${DIRS[@]}" 2>/dev/null || true)
  local count=0
  [ -n "$out" ] && count=$(printf '%s\n' "$out" | wc -l | tr -d ' ')
  echo "### ${name}: ${count}"
  if [ -n "$out" ]; then
    printf '%s\n' "$out" | head -15 | sed 's/^/    /'
    [ "$count" -gt 15 ] && echo "    ... (${count} total)"
  fi
  echo
}

# --- discipline bypasses / deferred work -----------------------------------
sweep "Deferred-work markers (TODO/FIXME/XXX/HACK)" -e 'TODO|FIXME|XXX|HACK'
sweep "Lint/type suppressions (noqa / type: ignore)" -e '# *noqa|# *type: *ignore|# *ruff: *noqa'
sweep "Bare or blanket except (prefer specific OSError/JSONDecodeError)" \
  -e 'except *:' -e 'except Exception'

# --- layer boundary: data.py must not import the TUI -----------------------
sweep "data.py importing the TUI layer (one-way dependency violation)" \
  -e 'import +cctop\.app|from +cctop\.app' -g 'data.py'

# --- number / cost discipline: rates live only in data.py ------------------
# $/MTok rate literals or per-MTok division outside the data.py rate block.
sweep "Rate literals / per-MTok math outside data.py" \
  -e 'RATE_|/ *1e6|/ *1_?000_?000' -g '!data.py'

# --- TUI stdout discipline: no print() from app/data -----------------------
sweep "print() in app.py or data.py (corrupts the TUI)" \
  -e '\bprint\(' -g 'app.py' -g 'data.py'

# --- generated / managed surfaces ------------------------------------------
echo "### Managed-surface edits vs ${DIFF_BASE:-main} (uv.lock — regenerate via 'uv sync', never hand-edit)"
locks=$(git diff --name-only "${DIFF_BASE:-main}" -- uv.lock 2>/dev/null || true)
if [ -n "$locks" ]; then
  printf '%s\n' "$locks" | sed 's/^/    /'
  echo "    NOTE: a uv.lock diff is sanctioned ONLY alongside a pyproject.toml dependency change."
else
  echo "    (none)"
fi
echo

echo "Sweep complete. Counts are leads, not verdicts — sanctioned exceptions exist."
exit 0
