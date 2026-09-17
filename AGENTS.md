# AGENTS.md

Guidance for AI agents (and people) working in this repository.

## What this is

**jira** - an agent skill that creates and reads Jira Cloud issues over the REST
API v3. It follows the [Agent Skills](https://agentskills.io) layout
(`skills/<name>/SKILL.md`) and ships as a
[Claude Code plugin](https://code.claude.com/docs/en/plugins).

## Layout

```
.claude-plugin/plugin.json        # plugin manifest
skills/jira/SKILL.md              # the skill (agent-facing instructions)
skills/jira/references/           # Simplified Technical English, for issue text
skills/jira/scripts/              # bash, curl and jq
install.sh / install-codex.sh     # local symlink installers (Claude / Codex)
```

## The constraints that must not be broken

Everything else here is a preference. These are not.

**1. It creates and reads. It does not delete.** No delete, no bulk transition,
no project administration. `bulk` keeps `--dry-run`. A partial failure leaves the
successful issues in place, and that is stated rather than hidden, because the
skill has no way to roll back. Anything destructive stays a human job in the Jira
UI.

**2. The token never reaches a command line.** `curl` reads the URL, the
credentials and the method from a 0600 config file, so the token stays out of
`ps` output and out of shell history. Never pass it as an argument, never
interpolate it into a URL, and never echo it.

**3. Check before you create.** The project key, the issue type and the required
fields are read from the live Jira before a create call is built. Issue type
names are per-project - `Task` in one project may be `Story` or `Work Item` in
another - so read `types` rather than assuming.

**4. No packages, no venv, no credential in the repo.** Bash plus `curl` and
`jq`. Nothing is installed at runtime.

## Conventions

- Any path `SKILL.md` names goes through `${CLAUDE_SKILL_DIR}`, which Claude Code
  substitutes for personal, project and plugin installs alike. **Never hardcode
  `~/.claude/skills/jira` or any absolute path** - it is wrong under a Codex
  install and wrong under a plugin install. `install-codex.sh` rewrites the
  variable at install time because Codex does not substitute it.
- `SKILL.md` is the short half on purpose. The workflow, the constraints and the
  checks live there; the reasoning and the reference material live in
  `references/` and are read on demand.
- Shell scripts use `set -e`; errors go to stderr, output to stdout.
- Every example is generic: `PAY-12`, `mycompany.atlassian.net`. CI greps for
  anything that looks like a real ticket id, hostname, IP address or
  organisation, and the ticket pattern it matches is `[A-Z]{3,}-[0-9]{3,}`.
- House style: British English, plain hyphens, **no em dashes** - CI fails on
  them. No trailing full stops on headings.

## Validating a change

```bash
bash -n install.sh install-codex.sh skills/jira/scripts/*.sh
shellcheck -S warning skills/jira/scripts/*.sh install.sh install-codex.sh
jq empty .claude-plugin/plugin.json
```

CI runs those plus the two prose checks.

## History

This skill lived here, moved into the `devskills` pack on 13 September 2026, and
came back to its own repository on 17 September 2026. The pack was split because
its two skills shared no API, no credential and no subject - `devskills` itself
recorded that they "have nothing in common beyond being things a developer needs
mid-task". The other half is [gitview](https://github.com/dbhq-uk/gitview-skill).
