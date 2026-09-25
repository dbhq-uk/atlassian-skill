#!/bin/bash
# Install the atlassian skill into ~/.claude/skills/ as a live symlink.
#
# A SKILL.md references its scripts via ${CLAUDE_SKILL_DIR}, which Claude Code
# substitutes to the skill's own directory for personal, project, and plugin
# installs alike. So this script symlinks the whole skill directory into
# ~/.claude/skills/ - every edit (scripts AND SKILL.md) is immediately live,
# with no per-file rewrite.
#
# It does not run the setup for you. Launching an interactive credential prompt
# from an installer is surprising - the setup command is printed at the end.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_ROOT="$HOME/.claude/skills"

echo "=== atlassian installer (Claude Code) ==="
echo

# --- Dependencies ---
# A warning, not a failure: a missing tool blocks calling Jira or Confluence
# (jq, curl), or breaks the discovery commands' table output (column), not
# the install itself. column ships as part of bsdextrautils on Debian/Ubuntu
# and util-linux elsewhere - not guaranteed present on every distribution.
MISSING=""
command -v jq >/dev/null 2>&1     || MISSING="$MISSING jq"
command -v curl >/dev/null 2>&1   || MISSING="$MISSING curl"
command -v column >/dev/null 2>&1 || MISSING="$MISSING column"
if [ -n "$MISSING" ]; then
  echo "Missing:$MISSING"
  echo "The skill installs anyway, but a missing tool above will fail the commands that need it."
else
  echo "Dependencies OK."
fi
echo

# --- Retire the old layout ---
# Until 25 Sep 2026 this repository shipped three skills - jira, confluence
# and confluence-publish - and a _shared folder all three reached with ../.
# They are one skill now, atlassian. An earlier run of this installer left a
# symlink for each of the four, pointing at a folder that no longer exists,
# so it would linger as a dangling duplicate an agent may still match.
# Only a symlink to one of those old folders is removed. jira and confluence
# are ordinary names, so a real directory or a link anywhere else is the
# user's own and is left alone.
mkdir -p "$SKILLS_ROOT"
for old in jira confluence confluence-publish _shared; do
  target="$SKILLS_ROOT/$old"
  [ -L "$target" ] || continue
  case "$(readlink "$target")" in
    */skills/"$old")
      echo "Removing '$old' - it is part of the atlassian skill now"
      rm -f "$target"
      ;;
  esac
done

# --- Install the skill as a full-directory symlink ---
for src in "$SCRIPT_DIR"/skills/*/; do
  src="${src%/}"
  name="$(basename "$src")"
  target="$SKILLS_ROOT/$name"
  echo "Installing '$name' -> $target"
  # Only ever remove a symlink here, never a real directory - a target that
  # exists and is NOT a symlink is left alone with an error rather than
  # silently rm -rf'd, in case it is the user's own same-named directory
  # rather than a prior install of this skill.
  if [ -e "$target" ] && [ ! -L "$target" ]; then
    echo "Error: $target already exists and is not a symlink - not touching it." >&2
    echo "Fix: move or remove it yourself, then re-run this installer." >&2
    exit 1
  fi
  rm -f "$target"              # replace any prior symlink install
  ln -sfn "$src" "$target"    # whole-directory symlink; ${CLAUDE_SKILL_DIR} resolves it
  chmod +x "$src"/scripts/*.sh 2>/dev/null || true
done

echo
echo "Installed as directory symlinks - all edits (scripts and SKILL.md) are live."

# --- Setup script ---
SETUPS="$(find "$SCRIPT_DIR"/skills -type f -name '*-setup.sh' | sort)"
if [ -n "$SETUPS" ]; then
  echo
  echo "This skill needs credentials before first use:"
  while IFS= read -r setup; do
    name="$(basename "$(dirname "$(dirname "$setup")")")"
    echo "  $name:  $SKILLS_ROOT/$name/scripts/$(basename "$setup")"
  done <<< "$SETUPS"
fi

echo
echo "Done. Try: 'what Jira projects can I see' or 'what Confluence spaces can I see'"
