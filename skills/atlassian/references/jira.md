# Jira issue creation and lookup

Create, read and comment on Jira Cloud issues, and move one issue through its workflow, through the REST v3 API. Credentials, setup and the rules are in `SKILL.md`.

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

`projects`, `types` and `fields` each follow every page the API offers rather than stopping at the first, so a project, issue type or required field that lives past the first page is not silently invisible to this check.

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

An entry can carry a `fields` object for anything else, the same as `create`'s `--field`: `"fields": {"customfield_10050": "Ops", "components": [{"name": "Backend"}]}`. Values go as written, since they are already JSON. It cannot set the seven fields that have their own key.

**A project that requires a field this payload does not name - a custom field, or a component with no default - is not a dead end.** `create` takes `--field KEY=VALUE` (and a `bulk` entry takes a `fields` object), repeatable, merged into `fields` last: read the field id with `jira-meta.sh fields <PROJECT> <TYPE>` first, then `--field customfield_10050=Ops` or `--field components='[{"name":"Backend"}]'` for anything that needs a shape rather than a string - a value that parses as JSON is sent as JSON, anything else as plain text. It refuses to set the seven fields the dedicated options already cover (`project`, `issuetype`, `summary`, `description`, `labels`, `priority`, `parent`) - use those instead.

**Use `--dry-run` first on anything bulk.** It prints the exact payload and sends nothing.

Every successful create prints the issue key and its browse URL.

## Formatted descriptions

`--description-file` takes an HTML+ fragment in place of the plain-text `[description]`, so a description can carry a real panel, a syntax-highlighted code block, a table and a status lozenge instead of a wall of text:

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh create PAY Task "Confirm the egress address" \
    --description-file /tmp/desc.html
```

`[description]` and `--description-file` are mutually exclusive - pass one or the other, never both. `bulk` has no `--description-file` equivalent; every description in a bulk file is plain text.

Before you write the body, read `${CLAUDE_SKILL_DIR}/references/house-style.md` and `${CLAUDE_SKILL_DIR}/references/html-patterns.md`, then `~/.dbhq/atlassian/house-style.md` if it exists. The user's file wins. Both references are written for a Confluence page, so skip what has no Jira equivalent, such as page titles and cross-page smart links. Work through `${CLAUDE_SKILL_DIR}/references/checklist.md` against the body before you send it.

**Jira's ADF profile is narrower than Confluence's, and `--description-file` refuses what is not on it.** The profile is [Atlassian's list of the nodes and marks Jira supports](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/). A decision list, a multi-column layout, a block or embed card and a Confluence macro are not on it. Jira may accept a description containing one and then show nothing where it should be, which looks like it worked. `--description-file` refuses these before anything is sent, naming the node:

```
Error: A decisionList is not on Atlassian's list of the nodes Jira supports, so the API may accept the description and show nothing where it should be. Use a panel, a table, a code block, a heading or a list instead. The list: https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
```

Panels, code blocks with language highlighting, tables, headings, lists, status lozenges and expands are all on Atlassian's list. Reach for those. A centred or indented paragraph is refused too, because Jira does not list those marks.

A task list passes, but it is an inference rather than a listed node: Atlassian lists a block task item, which only exists inside a task list. Until a live check confirms it renders, use a bullet list or a table for anything the reader must not miss.

## Writing the issue text

Every summary and every description is written in **Simplified Technical English**. The rules, the word choices and the pre-create checklist are in [ste.md](ste.md). Read that file before you write an issue.

The shape of an issue:

- **Summary** - one line, in the imperative or as a noun phrase. It names the thing and the scope: `Open TCP port 1433 from the DevOps agent pool subnet to the private endpoint subnet`. A change reference in parentheses at the end is permitted.
- **First paragraph** - what this issue is, in one or two sentences. A reader who stops here knows why the ticket exists.
- **The request, or what happened** - the specific action, with the values a person needs to do it.
- **Why** - the reason and the evidence, with the date you measured it.
- **Scope and limits** - what the issue does not cover.
- **The trigger to close it** - for anything temporary, what event ends it, named as a ticket or a date rather than as an intention.
- **References** - the documents and the related issue keys.

Drop a heading that has nothing to say. Do not write a heading and then repeat the summary under it.

## Reading

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh get <ISSUE-KEY> [--comments N]
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh search "<JQL>" [max]
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh mine [max]
```

`get` prints the description as markdown, through the same converter `confluence-pages.sh read --format markdown` uses, so tables, nested lists and mentions read as they look in Jira. It is for reading only: never edit that markdown and send it back. It then shows the last 5 comments, oldest first, with author and date. `--comments N` shows more, and `--comments 0` none.

`search` posts to `/rest/api/3/search/jql`. The old `GET /rest/api/3/search` is deprecated and is not used here.

`search` fetches one page only, up to `max` (default 25). It is not silent about that: if the response carries a `nextPageToken`, or the page is exactly full, it says more results may exist and to raise `max` or narrow the JQL, rather than let a truncated result set look complete.

## Commenting

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh comment <ISSUE-KEY> "<text>" [--dry-run]
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh comment <ISSUE-KEY> --body-file /tmp/comment.html [--dry-run]
```

Plain text works as it does for a description: a blank line starts a new paragraph. `--body-file` takes an HTML+ fragment and is held to Jira's node list, the same as `--description-file`. Write the comment in Simplified Technical English, and confirm it with the user first: the skill cannot delete a comment once it is posted. It prints a link to the new comment.

## Moving an issue

```bash
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh transitions <ISSUE-KEY>                 # the moves on offer now
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh transition <ISSUE-KEY> "<target>" --dry-run
${CLAUDE_SKILL_DIR}/scripts/jira-issues.sh transition <ISSUE-KEY> "<target>"
```

A workflow decides where an issue can go from where it is, so never assume a status name. `transition` reads the issue's current status and the transitions its workflow offers before it sends anything. `<target>` is the transition's name, the status it leads to, or its id, in any case. It refuses:

- a target the workflow does not offer from here, naming the ones it does
- a target that matches two transitions, until you pass the id
- a transition whose screen needs a field, such as a resolution, which this command does not set. Make that move in the Jira UI.

On success it prints the status the issue came from, and the command that moves it back if the workflow allows that. One issue per call: there is no bulk transition.

## Limits

Jira Cloud limits requests per second per endpoint, and a points quota per hour, rather than by a fixed figure a minute. On a 429 it sends `Retry-After`, in seconds. See [Rate limiting](https://developer.atlassian.com/cloud/jira/platform/rate-limiting/).

`bulk` pauses a second between creates. On a 429 it waits for `Retry-After` (2 seconds if there is none) and retries that issue once; if the limit has not cleared, or asks for more than a minute, it stops sending. It reports `Created:` and `Failed:` counts at the end. A partial failure leaves the created issues in place, and writes every entry that was not created to a remaining file, exactly as it was in the file: `tickets.json` gives `tickets.remaining.json`. Fix what failed, then run `bulk` on that file: it sends only those, and never repeats an issue that was created. A run of a remaining file writes back to the same file, and empties it once everything in it is created.

