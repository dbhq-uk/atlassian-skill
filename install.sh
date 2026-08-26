#!/bin/bash
# Install the Jira skill into ~/.claude/skills/ as a live symlink install.
#
# SKILL.md references scripts via ${CLAUDE_SKILL_DIR}, which Claude Code
# substitutes to the skill's own directory for personal, project, and plugin
# installs alike. So this script symlinks the whole skill directory into
# ~/.claude/skills/ - every edit (scripts AND SKILL.md) is immediately live,
# with no per-file rewrite. Re-run only when you add a new skill directory.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_ROOT="$HOME/.claude/skills"

echo "=== Jira skill installer (Claude Code) ==="
echo

# --- Dependencies ---
MISSING=""
command -v jq >/dev/null 2>&1   || MISSING="$MISSING jq"
command -v curl >/dev/null 2>&1 || MISSING="$MISSING curl"
if [ -n "$MISSING" ]; then
  echo "Missing required dependencies:$MISSING"
  echo "  macOS:  brew install$MISSING"
  echo "  Ubuntu: sudo apt install$MISSING"
  exit 1
fi
echo "Dependencies OK."
echo

# --- Install each skill in this pack as a full-directory symlink ---
mkdir -p "$SKILLS_ROOT"
for src in "$SCRIPT_DIR"/skills/*/; do
  src="${src%/}"
  name="$(basename "$src")"
  target="$SKILLS_ROOT/$name"
  echo "Installing '$name' -> $target"
  rm -rf "$target"            # replace any prior copy or partial-symlink install
  ln -sfn "$src" "$target"    # whole-directory symlink; ${CLAUDE_SKILL_DIR} resolves it
  chmod +x "$src"/scripts/*.sh 2>/dev/null || true
done

echo
echo "Installed as a directory symlink - all edits (scripts and SKILL.md) are live."
echo

# --- Setup / credentials ---
if [ -f "$HOME/.jira/config.json" ]; then
  echo "Existing Jira credentials found. Re-run setup any time with:"
  echo "  $SKILLS_ROOT/jira/scripts/jira-setup.sh"
elif [ -t 0 ]; then
  echo "No credentials found. Launching setup..."
  echo
  "$SKILLS_ROOT/jira/scripts/jira-setup.sh" || echo "Setup skipped; run jira-setup.sh when ready."
else
  # Not a terminal - jira-setup.sh prompts, so never launch it here.
  echo "No credentials found. Run setup when you are at a terminal:"
  echo "  $SKILLS_ROOT/jira/scripts/jira-setup.sh"
fi

echo
echo "Done. Try: 'what Jira projects can I see' or 'raise a ticket in PAY'"
