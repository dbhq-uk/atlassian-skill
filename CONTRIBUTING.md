# Contributing

Thanks for your interest - contributions are welcome.

## Ways to help

- Report a bug or request a feature via [issues](https://github.com/dbhq-uk/atlassian-skill/issues)
- Sharpen the skill's instructions, improve an error message, or widen what a
  create or update call can set, via a pull request

## Local development

```bash
git clone https://github.com/dbhq-uk/atlassian-skill.git
cd atlassian-skill
./install.sh          # symlinks the skill into ~/.claude/skills (edits are live)
./install-codex.sh    # the same for ~/.codex/skills
```

`install.sh` symlinks the whole skill directory, so edits - including to
`SKILL.md` and `references/` - are live immediately. For Codex, re-run
`./install-codex.sh` after editing a `SKILL.md`, since that file is rewritten
at install time rather than symlinked.

## Before opening a PR

```bash
for s in skills/*/scripts/*.sh install.sh install-codex.sh; do bash -n "$s"; shellcheck -S warning -x "$s"; done
python3 -m unittest discover -s skills/atlassian/tests -v
for p in skills/*/scripts/*.py; do python3 -m py_compile "$p"; done
jq empty .claude-plugin/plugin.json
```

CI runs the Python test suite above, plus a repository-wide shellcheck and
`ruff --select E9,F` pass over every script, a check that every `SKILL.md`
carries `name` and `description` frontmatter, and a check that nothing under
`skills/` reaches outside its own folder with `../`. One of the Python
suites (`test_constraints.py`) is the prose gate: no em dashes, British
English, no trailing full stops on headings, and nothing that looks
client-specific - a real client name, a security-proxy host, a ticket id
matching `[A-Z]{3,}-[0-9]{3,}`, or an IP address, scanned across every file
`git ls-files` tracks. See [`AGENTS.md`](AGENTS.md) for the full validation
list and what each check is for.

## What we will not accept

**A delete, a bulk transition, or project or space administration.** The
skill creates, reads and updates - never deletes. That boundary is the reason
it is safe to let an agent drive them, and `jira bulk` has no rollback
precisely because it cannot delete what it made. A pull request that adds a
destructive call, or a `--force` on `confluence-pages.sh update`'s
round-trip gate or on `publish.sh`'s check for an edit made in Confluence,
will be declined.

**A token on a command line, or in another process's argv.** `curl` reads
the credentials from a 0600 config file, and `jq` reads a token being saved
through its environment rather than `--arg`, so the token stays out of `ps`,
out of shell history, and out of `/proc/<pid>/cmdline`. Passing it as an
argument, interpolating it into a URL, or echoing it undoes that.

**A create or update call built without checking first.** Jira issue type
names are per-project and required fields vary - read `types` and `fields`
from the live Jira rather than assuming. Confluence `update` always needs
`--base-version`, with no default and no inference, and runs a round-trip
gate before sending - see [`AGENTS.md`](AGENTS.md) for what each protects
against.

**A real ticket id, hostname, IP address or organisation.** CI greps for
them. Every example in the docs is generic (`PAY-12`, `mycompany.atlassian.net`)
and should stay that way. Note that the check matches `[A-Z]{3,}-[0-9]{3,}`,
so keep example issue keys short.

**Anything that needs a package.** This is bash calling `curl` and `jq`, plus
Python 3's standard library. There is no `requirements.txt` and no venv,
which is why there is nothing to keep patched.

## Code of conduct

By taking part you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

By contributing you agree your work is licensed under the [MIT licence](LICENSE).
