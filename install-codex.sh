#!/bin/bash
# Install the atlassian skill into ~/.codex/skills/ for Codex.
#
# Codex does not substitute ${CLAUDE_SKILL_DIR}, so this script rewrites that
# variable to each skill's installed Codex path and symlinks the supporting
# directories (edits to those stay live). Re-run after editing a SKILL.md.
#
# This skill needs no venv: it is bash calling curl and jq. So there is no build
# step here, unlike the org's Python skills that carry one.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_ROOT="$HOME/.codex/skills"

echo "=== atlassian installer (Codex) ==="
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

mkdir -p "$SKILLS_ROOT"

# --- Retire the old layout ---
# Until 25 Sep 2026 this repository shipped three skills - jira, confluence
# and confluence-publish - and a _shared folder all three reached with ../.
# They are one skill now, atlassian. An earlier run of this installer left
# _shared as a symlink, and each of the three as a folder holding a
# rewritten SKILL.md beside symlinks into this repository. Those links now
# point at folders that no longer exist, and the SKILL.md would still be
# matched, so the old install is taken out.
#
# jira and confluence are ordinary names, so only what this installer made
# is removed: a link to the old folder, or a folder whose scripts link
# points at it and which holds nothing but links and a SKILL.md. Anything
# else is the user's own and is left alone. Nothing is removed with rm -r.
retire_old_install() {
  local old="$1" target="$SKILLS_ROOT/$1" entry
  if [ -L "$target" ]; then
    case "$(readlink "$target")" in
      */skills/"$old") rm -f "$target" ;;
      *) return 0 ;;
    esac
  elif [ -d "$target" ]; then
    case "$(readlink "$target/scripts" 2>/dev/null)" in
      */skills/"$old"/scripts) ;;
      *) return 0 ;;
    esac
    for entry in "$target"/* "$target"/.[!.]*; do
      [ -e "$entry" ] || [ -L "$entry" ] || continue
      [ -L "$entry" ] || [ "$(basename "$entry")" = "SKILL.md" ] || return 0
    done
    find "$target" -mindepth 1 -maxdepth 1 \( -type l -o -name SKILL.md \) -exec rm -f {} +
    rmdir "$target"
  else
    return 0
  fi
  echo "Removing '$old' - it is part of the atlassian skill now"
}
for old in jira confluence confluence-publish _shared; do
  retire_old_install "$old"
done

for src in "$SCRIPT_DIR"/skills/*/; do
  src="${src%/}"
  name="$(basename "$src")"
  target="$SKILLS_ROOT/$name"
  [ -f "$src/SKILL.md" ] || continue
  echo "Installing '$name' -> $target"

  mkdir -p "$target"
  # Clear what a previous install left before linking what this one needs.
  # Without this, an entry since renamed or deleted upstream survives as a
  # symlink to a path that no longer exists - and a dangling link fails more
  # confusingly than a missing file, because it looks installed. Only symlinks
  # are removed, so a real SKILL.md is never at risk.
  find "$target" -mindepth 1 -maxdepth 1 -type l -exec rm -f {} +
  for sub in references scripts; do
    [ -d "$src/$sub" ] && ln -sfn "$src/$sub" "$target/$sub"
  done
  chmod +x "$src"/scripts/*.sh 2>/dev/null || true
  sed "s#\${CLAUDE_SKILL_DIR}#$target#g; s#\$CLAUDE_SKILL_DIR#$target#g" "$src/SKILL.md" > "$target/SKILL.md"
done

echo
echo "Installed for Codex. Re-run after editing a SKILL.md - that file is
rewritten at install time rather than symlinked, so its edits are not live."

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
