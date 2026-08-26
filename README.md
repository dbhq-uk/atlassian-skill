# Jira skill

A Claude Code skill that creates and reads Jira Cloud issues over the REST API v3, authenticated with an API token.

Ask Claude to raise a ticket and it does — after checking the project key, the issue type and the required fields against your Jira, so the create call is right the first time. It creates one issue or a batch, and reads issues back with JQL.

**It creates and reads only.** There is no delete, no bulk transition, and no project administration. Anything destructive stays a human job in the Jira UI.

## Install

```bash
git clone https://github.com/dbhq-uk/jira-skill.git
cd jira-skill
./install.sh
```

`install.sh` symlinks `skills/jira` into `~/.claude/skills/`, so edits to the scripts or to `SKILL.md` are live with no reinstall. It then runs setup if you have no credentials yet.

Requires `jq` and `curl`.

## Setup

```bash
~/.claude/skills/jira/scripts/jira-setup.sh
```

It asks for three things: your site URL (`https://you.atlassian.net`), the email on your Atlassian account, and an API token from <https://id.atlassian.com/manage-profile/security/api-tokens>. The token input is hidden.

Setup verifies against `/rest/api/3/myself` **before** writing anything, so a wrong token costs you nothing. Credentials are saved to `~/.jira/config.json` at mode 600, outside any repository.

The token never reaches a command line. `curl` reads it from a 0600 config file, so it does not appear in `ps` output or in shell history.

## Use

Talk to Claude — "raise a bug in PAY about the timeout", "what's assigned to me", "create these five tickets". The scripts are also usable directly:

```bash
cd ~/.claude/skills/jira/scripts

./jira-meta.sh projects              # project keys you can see
./jira-meta.sh types PAY             # issue type names in that project
./jira-meta.sh fields PAY Task       # what a create accepts, required marked

./jira-issues.sh create PAY Task "Summary" "Description" --label infra --priority High
./jira-issues.sh bulk PAY tickets.json --dry-run
./jira-issues.sh get PAY-101
./jira-issues.sh search "assignee = currentUser() AND statusCategory != Done"
./jira-issues.sh mine
```

Issue type names are per-project. `Task` in one project may be `Story` or `Work Item` in another, so read `types` rather than assuming.

### Bulk creation

`bulk` takes a JSON array. Only `summary` is required; `type` defaults to `Task`.

```json
[
  {"summary": "Enable the storage provider on the subscription", "type": "Task",
   "description": "Blocks the deployment.\n\nOnly the pipeline identity can do this.",
   "labels": ["infra"], "priority": "High"},
  {"summary": "Add retry handling to the upload step"}
]
```

Blank lines in a description become separate paragraphs. REST v3 needs Atlassian Document Format rather than a plain string, and the script builds it, so you pass plain text.

**Run it with `--dry-run` first.** That prints the exact payload for every issue and sends nothing.

A batch paces itself at five requests a second to stay inside Jira's limit of roughly 60 a minute, and reports `Created:` and `Failed:` counts at the end. A partial failure leaves the successful issues in place — there is no rollback, because the skill cannot delete.

## Files

| Path | What it is |
|---|---|
| `skills/jira/SKILL.md` | What Claude reads |
| `skills/jira/scripts/jira-setup.sh` | Credential capture and verification |
| `skills/jira/scripts/jira-meta.sh` | Projects, issue types, fields, priorities — read-only |
| `skills/jira/scripts/jira-issues.sh` | Create, bulk create, get, search |
| `skills/jira/scripts/_common.sh` | Shared request, error and ADF helpers |

## Licence

MIT. See [LICENSE](LICENSE).
