# Confluence comments

List, read, add, reply to, update, resolve and delete the comments on a Confluence Cloud page, through the v2 REST API. Credentials, setup and the rules are in `SKILL.md`.

## Before you write anything

A comment is visible to everyone who can see the page the moment it exists, and the page's watchers are notified. Confirm the text with the user before you add, reply to or update one.

Read `${CLAUDE_SKILL_DIR}/references/house-style.md` and `${CLAUDE_SKILL_DIR}/references/html-patterns.md`, then `~/.dbhq/atlassian/house-style.md` if it exists. The user's file wins. A comment is short, so most of the checklist does not apply, but the tone and the "no em dashes" rules do.

## The two kinds

| Kind | Where it sits | How to add one |
|---|---|---|
| Footer | At the foot of the page | `create <page-id> <text>` |
| Inline | Anchored to a piece of text on the page, shown in the margin | `create <page-id> <text> --inline "<text on the page>"` |

Every other command takes a comment id and works on both kinds. The script finds which kind the id is.

## Reading

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh list 1234567             # every thread on the page
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh read 7654321             # one comment, as HTML+
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh read 7654321 --format markdown
```

`list` prints the footer threads, then the inline threads, oldest first. Replies are indented under the comment they answer. Each comment shows its id, its version, its author's account id, marked `(you)` for your own, and the date. An inline comment also shows the text it highlights and whether it is `open` or `resolved`. `--limit` (default 50, most 250) caps each list; the command says when there are more.

`read` prints a header on stderr naming the version, as a page read does: `# version 2 - pass --base-version 2 to update`.

## Adding

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh create 1234567 "Looks good. Two points below." --dry-run
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh create 1234567 --body-file /tmp/comment.html
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh create 1234567 "Which address?" --inline "egress address"
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh reply 7654321 "Fixed in version 15."
```

The body is plain text, where a blank line starts a new paragraph, or an HTML+ fragment with `--body-file`. Use `--body-file` for a numbered list, bold text or a link. It goes through the same converter as a page. Each command prints the new comment's id and a link to it.

**`--inline` anchors the comment to text inside one paragraph, heading or table cell.** Confluence needs to know how many times that text is on the page and which one to highlight. The script counts it on the live page, never trusting a number from you. If the text is there more than once, it refuses until you pass `--match <n>`, counting from 1 in page order, or quote more of the text. Text broken by a mention, a status or a date does not match; pick text on one side of it.

`reply` answers a comment of either kind. A reply to an inline comment stays in that comment's thread.

## Updating

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh read 7654321
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh update 7654321 "Corrected text." --base-version 1 --dry-run
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh update 7654321 "Corrected text." --base-version 1
```

`update` replaces the whole comment body. It needs `--base-version`, for the same reason a page update does: it refuses if the comment has moved on since you read it. It updates **only a comment your own account wrote**, judged by the author of the comment's first version. It cannot move an inline comment's highlight.

## Resolving

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh resolve 7654321
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh reopen 7654321
```

Only an inline comment can be resolved. Anyone's inline comment can be, as in the Confluence UI. The body is sent back exactly as it was read, so only the resolution changes. `resolve` prints the `reopen` command that undoes it.

## Deleting

```bash
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh delete 7654321 --dry-run
${CLAUDE_SKILL_DIR}/scripts/confluence-comments.sh delete 7654321
```

**Confluence's delete is permanent. It cannot be reverted, from the API or the UI.** Confirm with the user every time, and dry-run first: the dry run prints the comment that would go.

`delete` refuses:

- a comment another account wrote. Reply to it instead, or ask its author.
- a comment with replies. Deleting it would take other people's replies with it. Use `update` to correct it instead.

This is the only delete in the skill's Confluence scripts. A page, an attachment or a space is never deleted.

A scoped API token needs `delete:comment:confluence` for `delete`. Without it, the delete fails and nothing else changes. See `SECURITY.md`.
