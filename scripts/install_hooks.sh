#!/usr/bin/env bash
# Git hook installer — sets up post-commit, post-merge, post-rewrite hooks
# that trigger auto_deploy.sh in the background after any commit/pull/rebase.
#
# Usage: bash scripts/install_hooks.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS_DIR="$ROOT/.git/hooks"
DEPLOY_SCRIPT="$ROOT/scripts/auto_deploy.sh"

chmod +x "$DEPLOY_SCRIPT"

HOOK_BODY='#!/usr/bin/env bash
# Auto-installed by scripts/install_hooks.sh
ROOT="$(git rev-parse --show-toplevel)"
nohup bash "$ROOT/scripts/auto_deploy.sh" >> "$ROOT/.runtime/stack/auto_deploy.log" 2>&1 &
disown
'

install_hook() {
  local name="$1"
  local file="$HOOKS_DIR/$name"

  if [[ -f "$file" ]] && ! grep -q "auto_deploy" "$file"; then
    # Append to existing hook instead of overwriting
    echo "" >> "$file"
    echo "# Sterling auto-deploy" >> "$file"
    echo 'ROOT="$(git rev-parse --show-toplevel)"' >> "$file"
    echo 'nohup bash "$ROOT/scripts/auto_deploy.sh" >> "$ROOT/.runtime/stack/auto_deploy.log" 2>&1 &' >> "$file"
    echo 'disown' >> "$file"
    echo "  → Appended to existing $name"
  else
    printf '%s' "$HOOK_BODY" > "$file"
    chmod +x "$file"
    echo "  → Installed $name"
  fi
}

echo "Installing Sterling git hooks…"
install_hook "post-commit"    # local: git commit
install_hook "post-merge"     # local: git pull (merge)
install_hook "post-rewrite"   # local: git rebase / git commit --amend
echo ""
echo "Done. Auto-deploy will trigger on: commit · pull · merge · rebase"
echo "Logs: .runtime/stack/auto_deploy.log"
