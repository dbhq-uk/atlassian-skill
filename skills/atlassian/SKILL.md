---
name: atlassian
description: Jira Cloud and Confluence Cloud over the REST API, with an API token and no MCP server. Creates, reads and comments on Jira issues and moves one through its workflow, searches, reads, creates and updates Confluence pages, lists, adds, edits, resolves and deletes comments on both, and publishes a repository's markdown file to a Confluence page. The only delete is a comment you wrote. Trigger on phrases like "jira", "raise a jira ticket", "create a jira ticket", "what's assigned to me in jira", "comment on the jira ticket", "edit my jira comment", "move the jira ticket to done", "search jira", "JQL", "confluence", "the wiki", "what does the wiki say", "create a confluence page", "update that confluence page", "comment on the confluence page", "reply to the comment", "resolve the comment", "delete my comment", "CQL", "publish this to confluence", "push the docs to the wiki", "sync these docs to confluence".
---

# atlassian

Jira Cloud and Confluence Cloud through their REST APIs, on one API token. No MCP server.

This skill **creates, reads and updates. The one thing it deletes is a comment your own account wrote**, and on Confluence only one with no replies under it. It never deletes an issue, a page, an attachment or a space. It has no bulk transition and no project or space administration. Anything else destructive is a human job in the Atlassian UI.

## Read the reference for the task

Read the matching file before you run anything. Each one holds the commands, the checks and the error messages for its task.

| Task | Read |
|---|---|
| Create, read, search or comment on Jira issues, edit or delete a comment, or move an issue to another status | `${CLAUDE_SKILL_DIR}/references/jira.md` |
| Search, read, create, edit or update a Confluence page | `${CLAUDE_SKILL_DIR}/references/confluence.md` |
| List, add, reply to, update, resolve or delete comments on a Confluence page | `${CLAUDE_SKILL_DIR}/references/confluence-comments.md` |
| Publish a markdown file to a Confluence page | `${CLAUDE_SKILL_DIR}/references/publish.md` |

Every command in those files starts with the `CLAUDE_SKILL_DIR` placeholder. Read it as this skill's own directory: `${CLAUDE_SKILL_DIR}`. Every script the skill runs is inside it.

## Before you write anything

Before you write a Jira description, a Confluence page or a file you will publish, read:

1. `${CLAUDE_SKILL_DIR}/references/house-style.md` - the conventions
2. `${CLAUDE_SKILL_DIR}/references/html-patterns.md` - the HTML+ patterns
3. `~/.dbhq/atlassian/house-style.md`, if it exists - the user's own conventions, which win over the shipped ones

Then work through `${CLAUDE_SKILL_DIR}/references/checklist.md` against the body before you send it. Jira issue text also follows `${CLAUDE_SKILL_DIR}/references/ste.md`.

## Credentials

One credential serves both products: `~/.dbhq/atlassian/config.json`, mode 600, with the site URL, the account email and an API token.

If that file does not exist, setup has not run. **Do not run setup yourself.** It asks for the token with the input hidden, so it needs the user at a terminal, and the token must not pass through this conversation. Give the user this command, with the full path written out, and ask them to run it in their own terminal:

```bash
${CLAUDE_SKILL_DIR}/scripts/atlassian-setup.sh
```

Every script prints the same full path when it finds no credential. Setup asks for the site URL, the email and an API token from <https://id.atlassian.com/manage-profile/security/api-tokens>. It verifies Jira access before it saves anything, then checks Confluence access (a warning, not a blocker). The token never reaches a command line: `curl` reads it from a 0600 config file.

The scripts need `jq`, `curl`, `column` and Python 3 (standard library only).

## The rules

1. **Check before you write.** Never invent a Jira project key, issue type or field name - read them with `jira-meta.sh`. Never invent a Confluence media id or collection.
2. **Confirm with the user before you create, comment or move an issue, and before you add, change or delete any comment.** An issue, a comment or a page is visible to the team the moment it exists. This skill cannot delete an issue or a page, and a deleted comment cannot be restored.
3. **Dry-run first** for `jira-issues.sh bulk`, for `publish.sh`, and for every comment delete.
4. **A Confluence `update` or `edit` needs `--base-version`** - the version your edit started from. It refuses if the page has moved on. To change part of a page, use `edit`, which leaves the rest untouched.
5. **Bodies are HTML+**, converted to ADF and checked locally before anything is sent. Never markdown on the wire, never storage format.
6. **There is no `--force`.** A refusal needs a human decision, not a flag.
7. Never put a credential, a token or personal data into an issue or a page.

When a script fails, read the `Cause:` and `Fix:` lines it prints.

## When not to use it

- GitHub issues or pull requests. Use `gh`.
- Trello boards and cards.
- Jira or Confluence Server or Data Center. This skill speaks the Cloud REST APIs only.
- A session that already has the Atlassian MCP server connected, unless the user asks for this skill by name.
- Deleting anything but your own comment, or moving many issues at once. That stays in the Atlassian UI.
