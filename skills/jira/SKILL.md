---
name: jira
description: Create and read Jira Cloud issues via the REST API. Trigger on phrases like "jira", "create a ticket", "raise a ticket", "jira ticket", "what's assigned to me", "search jira", "JQL".
---

# Jira issue creation and lookup

Create and read Jira Cloud issues through the REST v3 API, authenticated with an API token.

This skill **creates and reads only**. It has no delete, no bulk transition, and no project administration. Anything destructive is done by a human in the Jira UI.

## Prerequisites

- Credentials in `~/.jira/config.json` - run setup if absent
- `jq` and `curl`

## Setup

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-setup.sh
```

It asks for the site URL, the account email, and an API token from <https://id.atlassian.com/manage-profile/security/api-tokens>. The token input is hidden. It verifies against `/rest/api/3/myself` **before** writing anything, then saves to `~/.jira/config.json` at mode 600. Credentials live outside the repository and are never committed.

## Look before you create

A create call fails when the project key, the issue type name, or a required field is wrong. Check first - it costs one call and saves a confusing 400.

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-meta.sh projects [search]      # project keys
${CLAUDE_SKILL_DIR}/scripts/jira-meta.sh types <PROJECT>        # issue type names for that project
${CLAUDE_SKILL_DIR}/scripts/jira-meta.sh fields <PROJECT> <TYPE> # what a create accepts, required marked
${CLAUDE_SKILL_DIR}/scripts/jira-meta.sh priorities             # valid priority names
${CLAUDE_SKILL_DIR}/scripts/jira-meta.sh whoami                 # who the token authenticates as
```

Issue type names are per-project and case-sensitive on the wire. `Task` in one project may be `Story` or `Work Item` in another - read `types` rather than assuming.

## Creating issues

One issue:

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh create <PROJECT> <TYPE> "<summary>" "<description>" \
    [--label backend] [--priority High] [--parent ABC-12] [--dry-run]
```

Several at once, from a JSON array:

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh bulk <PROJECT> tickets.json [--dry-run]
```

```json
[
  {"summary": "Enable the storage provider on the subscription", "type": "Task",
   "description": "Blocks the deployment.\n\nOnly the pipeline identity can do this.",
   "labels": ["infra"], "priority": "High"},
  {"summary": "Add retry handling to the upload step", "type": "Task"}
]
```

Only `summary` is required; `type` defaults to `Task`. Blank lines in a description become separate paragraphs - REST v3 needs Atlassian Document Format, and the script builds it, so pass plain text.

**Use `--dry-run` first on anything bulk.** It prints the exact payload and sends nothing.

Every successful create prints the issue key and its browse URL.

## Writing the issue text

Every summary and every description is written in **Simplified Technical English**. The rules, the word choices and the pre-create checklist are in [references/ste.md](references/ste.md). Read that file before you write an issue.

The shape of an issue:

- **Summary** - one line, in the imperative or as a noun phrase. It names the thing and the scope: `Open TCP port 1433 from the DevOps agent pool subnet to the private endpoint subnet`. A change reference in parentheses at the end is permitted.
- **First paragraph** - what this issue is, in one or two sentences. A reader who stops here knows why the ticket exists.
- **The request, or what happened** - the specific action, with the values a person needs to do it.
- **Why** - the reason and the evidence, with the date you measured it.
- **Scope and limits** - what the issue does not cover.
- **The trigger to close it** - for anything temporary, what event ends it, named as a ticket or a date rather than as an intention.
- **References** - the documents and the related issue keys.

Drop a heading that has nothing to say. Do not write a heading and then repeat the summary under it.

Four rules carry most of the value: a sentence has a maximum of 25 words, the active voice names the actor, one term means one thing through the whole issue, and a technical name never changes to obey a rule.

## Reading

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh get <ISSUE-KEY>
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh search "<JQL>" [max]
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh mine [max]
```

`search` posts to `/rest/api/3/search/jql`. The old `GET /rest/api/3/search` is deprecated and is not used here.

## Rules

1. **Never invent a project key, an issue type, or a field name.** Read it with `jira-meta.sh` first.
2. **Confirm the summary and description with the user before creating.** A Jira issue is visible to the whole team the moment it exists, and this skill cannot delete one.
3. **Two or more issues means `bulk` with `--dry-run` first**, reviewed, then the real run.
4. Do not put credentials, tokens, account numbers, or personal data into an issue description. A Jira issue is not a secret store, and in a regulated environment it is disclosable.
5. On failure, read the `Cause:` and `Fix:` lines the scripts print. They carry Jira's own error text.
6. **Write the summary and the description in Simplified Technical English** - see [§ Writing the issue text](#writing-the-issue-text) and [references/ste.md](references/ste.md).

## Limits

Jira Cloud allows roughly 60 authenticated requests a minute. `bulk` paces itself at five a second and reports `Created:` and `Failed:` counts at the end; a partial failure leaves the successful issues in place.

## Credentials

`~/.jira/config.json`, mode 600, holding `site`, `email` and `token`. The token never reaches a command line - `curl` reads it from a 0600 config file, so it does not appear in `ps` or in shell history.