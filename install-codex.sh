#!/bin/bash
# Install every skill in this pack into ~/.codex/skills/ for Codex.
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
# A warning, not a failure: a missing tool blocks a call to Jira, not the install.
MISSING=""
command -v jq >/dev/null 2>&1   || MISSING="$MISSING jq"
command -v curl >/dev/null 2>&1 || MISSING="$MISSING curl"
if [ -n "$MISSING" ]; then
  echo "Missing:$MISSING"
  echo "The skill installs anyway, but it cannot call Jira until they are there."
else
  echo "Dependencies OK."
fi
echo

mkdir -p "$SKILLS_ROOT"
for src in "$SCRIPT_DIR"/skills/*/; do
  src="${src%/}"
  name="$(basename "$src")"
  target="$SKILLS_ROOT/$name"
  echo "Installing '$name' -> $target"

  # _shared is not a skill - it has no SKILL.md, because nothing installs it
  # on its own; jira, confluence and confluence-publish all reach it as
  # ${CLAUDE_SKILL_DIR}/../_shared/. There is no ${CLAUDE_SKILL_DIR} inside it
  # to rewrite, so it is symlinked whole, the same way install.sh does it for
  # Claude Code - not stepped into the SKILL.md-rewrite path below, which
  # would fail on the file every other directory here has and this one does
  # not.
  if [ ! -f "$src/SKILL.md" ]; then
    # Same whole-directory symlink install.sh does for every directory
    # (see its own comment) - so it needs the same guard: only ever remove
    # a symlink here, never a real directory, in case $target is the
    # user's own same-named directory rather than a prior install.
    if [ -e "$target" ] && [ ! -L "$target" ]; then
      echo "Error: $target already exists and is not a symlink - not touching it." >&2
      echo "Fix: move or remove it yourself, then re-run this installer." >&2
      exit 1
    fi
    rm -f "$target"
    ln -sfn "$src" "$target"
    continue
  fi

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
