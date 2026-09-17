# Contributing

Thanks for your interest - contributions are welcome.

## Ways to help

- Report a bug or request a feature via [issues](https://github.com/dbhq-uk/jira-skill/issues)
- Sharpen the skill's instructions, improve an error message, or widen what a
  create call can set, via a pull request

## Local development

```bash
git clone https://github.com/dbhq-uk/jira-skill.git
cd jira-skill
./install.sh          # symlinks the skill into ~/.claude/skills (edits are live)
./install-codex.sh    # the same for ~/.codex/skills
```

`install.sh` symlinks the whole skill directory, so edits - including to
`SKILL.md` and `references/` - are live immediately. For Codex, re-run
`./install-codex.sh` after editing `SKILL.md`, since that file is rewritten at
install time rather than symlinked.

## Before opening a PR

```bash
bash -n install.sh install-codex.sh skills/jira/scripts/*.sh   # everything parses
shellcheck -S warning skills/jira/scripts/*.sh                 # and is clean
jq empty .claude-plugin/plugin.json                            # the manifest is valid JSON
```

CI runs those, plus two checks on the prose: no em dashes, and nothing that looks
client-specific. British English, plain hyphens, no trailing full stops on
headings.

## What we will not accept

**A delete, a bulk transition, or project administration.** The skill creates and
reads. That boundary is the reason it is safe to let an agent drive it, and
`bulk` has no rollback precisely because it cannot delete what it made. A pull
request that adds a destructive call will be declined.

**A token on a command line.** `curl` reads the credentials from a 0600 config
file so the token stays out of `ps` and out of shell history. Passing it as an
argument, interpolating it into a URL, or echoing it undoes that.

**A create call built without checking.** Issue type names are per-project and
required fields vary. Read `types` and `fields` from the live Jira rather than
assuming, or the failure lands on the user as a 400 they cannot read.

**A real ticket id, hostname, IP address or organisation.** CI greps for them.
Every example in the docs is generic (`PAY-12`, `mycompany.atlassian.net`) and
should stay that way. Note that the check matches `[A-Z]{3,}-[0-9]{3,}`, so keep
example issue keys short.

**Anything that needs a package.** This is bash calling `curl` and `jq`. There is
no `requirements.txt` and no venv, which is why there is nothing to keep patched.

## Code of conduct

By taking part you agree to the [code of conduct](CODE_OF_CONDUCT.md).

## Licence

By contributing you agree your work is licensed under the [MIT licence](LICENSE).
