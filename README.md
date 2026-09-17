<div align="center">

<img src="assets/logo.svg" alt="atlassian - Jira issues and Confluence pages from your agent, by DBHQ" width="560">

# atlassian

**Raise the ticket and write the page, without leaving the conversation**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet)](https://code.claude.com/docs/en/plugins)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey)]()

A free, open-source tool by [DBHQ](https://dbhq.uk) - documented at [skills.dbhq.uk](https://skills.dbhq.uk/atlassian/)

</div>

---

Creates and reads Jira Cloud issues over the REST API v3, authenticated with an
API token.

Ask your agent to raise a ticket and it does, after checking the project key, the
issue type and the required fields against your Jira, so the create call is right
the first time. It creates one issue or a batch, and reads issues back with JQL.

**It creates and reads only.** There is no delete, no bulk transition, and no
project administration. Anything destructive stays a human job in the Jira UI.

## Install

### As a Claude Code plugin (recommended)

```
/plugin marketplace add dbhq-uk/marketplace
/plugin install atlassian@dbhq
```

### Any agent (Cursor, Copilot, Windsurf, Gemini, Cline and more)

```bash
npx skills add dbhq-uk/atlassian-skill
```

The [skills.sh](https://skills.sh) CLI installs into whichever agent directories
it finds, so this works outside Claude Code and Codex too.

### Local install (Claude Code or Codex)

```bash
git clone https://github.com/dbhq-uk/atlassian-skill.git
cd atlassian-skill
./install.sh          # Claude Code: symlinks into ~/.claude/skills (edits are live)
./install-codex.sh    # Codex: installs into ~/.codex/skills
```

[`install.sh`](install.sh) and [`install-codex.sh`](install-codex.sh) are the
same install two ways: Claude Code substitutes `${CLAUDE_SKILL_DIR}`, so the
whole skill directory is symlinked untouched, while Codex does not, so its
`SKILL.md` is rewritten at install time. Re-run the Codex one after editing
`SKILL.md`.

### Requirements

`jq`, `curl`, and a Jira Cloud account you can create an API token on.


## Setup

```bash
~/.claude/skills/jira/scripts/jira-setup.sh
```

It asks for three things: your site URL (`https://you.atlassian.net`), the email
on your Atlassian account, and an API token from
<https://id.atlassian.com/manage-profile/security/api-tokens>. The token input is
hidden.

Setup verifies against `/rest/api/3/myself` **before** writing anything, so a
wrong token costs you nothing. Credentials are saved to `~/.dbhq/jira/config.json` at
mode 600, outside any repository.

The token never reaches a command line. `curl` reads it from a 0600 config file,
so it does not appear in `ps` output or in shell history.

## Use

Talk to your agent: "raise a bug in PAY about the timeout", "what's assigned to
me", "create these five tickets". The scripts are also usable directly:

```bash
cd ~/.claude/skills/jira/scripts

./jira-meta.sh projects              # project keys you can see
./jira-meta.sh types PAY             # issue type names in that project
./jira-meta.sh fields PAY Task       # what a create accepts, required marked

./jira-issues.sh create PAY Task "Summary" "Description" --label infra --priority High
./jira-issues.sh bulk PAY tickets.json --dry-run
./jira-issues.sh get PAY-12
./jira-issues.sh search "assignee = currentUser() AND statusCategory != Done"
./jira-issues.sh mine
```

Issue type names are per-project. `Task` in one project may be `Story` or
`Work Item` in another, so read `types` rather than assuming.

## Bulk creation

`bulk` takes a JSON array. Only `summary` is required; `type` defaults to `Task`.

```json
[
  {"summary": "Enable the storage provider on the subscription", "type": "Task",
   "description": "Blocks the deployment.\n\nOnly the pipeline identity can do this.",
   "labels": ["infra"], "priority": "High"},
  {"summary": "Add retry handling to the upload step"}
]
```

Blank lines in a description become separate paragraphs. REST v3 needs Atlassian
Document Format rather than a plain string, and the script builds it, so you pass
plain text.

**Run it with `--dry-run` first.** That prints the exact payload for every issue
and sends nothing.

A batch paces itself at five requests a second to stay inside Jira's limit of
roughly 60 a minute, and reports `Created:` and `Failed:` counts at the end. A
partial failure leaves the successful issues in place, because there is no
rollback: the skill cannot delete.

## Files

| Path | What it is |
|---|---|
| `skills/jira/SKILL.md` | What the agent reads |
| `skills/jira/references/ste.md` | Simplified Technical English, for issue text |
| `skills/jira/scripts/jira-setup.sh` | Credential capture and verification |
| `skills/jira/scripts/jira-meta.sh` | Projects, issue types, fields, priorities, read-only |
| `skills/jira/scripts/jira-issues.sh` | Create, bulk create, get, search |
| `skills/jira/scripts/_common.sh` | Shared request, error and ADF helpers |

## Also from DBHQ

Fifteen free agent skills, all of them installable from the same marketplace and
all documented at **[skills.dbhq.uk](https://skills.dbhq.uk)**.

| Skill | What it does |
|---|---|
| [outlook](https://skills.dbhq.uk/outlook/) | Microsoft 365 mail and calendar, from the terminal |
| [trello](https://skills.dbhq.uk/trello/) | Your boards, run from your agent |
| [legwork](https://skills.dbhq.uk/legwork/) | Research that settles a decision, and says when it cannot |
| [dovetail](https://skills.dbhq.uk/dovetail/) | Checks whether your repository still agrees with itself |
| [verve](https://skills.dbhq.uk/verve/) | Strips AI tells from prose and puts a voice back |
| [vela](https://skills.dbhq.uk/vela/) | Compiler-exact code search for .NET |
| [garmin](https://skills.dbhq.uk/garmin/) | Your Garmin data, answered in the terminal |
| [imager](https://skills.dbhq.uk/imager/) | Images from GPT Image 2, costed before it spends |
| [gitview](https://skills.dbhq.uk/gitview/) | Which branches are finished, and safe to delete |
| [pennyblack](https://skills.dbhq.uk/pennyblack/) | A physical letter, posted from the terminal |
| [buildwork](https://skills.dbhq.uk/buildwork/) | Your open issues, run as parallel agents |
| [deskwork](https://skills.dbhq.uk/deskwork/) | What an agent noticed, tracked as real work |
| [groupwork](https://skills.dbhq.uk/groupwork/) | A second agent on the work, adversary or partner |

Plus [heliograph](https://skills.dbhq.uk/heliograph/), for a machine you cannot log into.

## Licence

MIT. See [LICENSE](LICENSE).
