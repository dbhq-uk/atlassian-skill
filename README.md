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

Three agent skills for Atlassian Cloud, on one credential and with no MCP server:

- **`jira`** creates and reads Jira Cloud issues over the REST API v3. Ask your
  agent to raise a ticket and it does, after checking the project key, the
  issue type and the required fields against your Jira first, so the create
  call is right the first time.
- **`confluence`** searches, reads, creates and updates Confluence Cloud pages
  over the v2 REST API - with a stale-write guard on every update.
- **`confluence-publish`** turns a repository's markdown into Confluence pages,
  idempotently, with the page id written back into the file's own frontmatter.

**They create, read and update only.** There is no delete anywhere, no bulk
transition, and no project or space administration. Anything destructive stays
a human job in the Atlassian UI.

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

`jq`, `curl`, Python 3 (standard library only - nothing to install), and an
Atlassian Cloud account you can create an API token on.


## Setup

One credential authenticates all three skills, because Jira Cloud and Confluence
Cloud on the same site take the same site URL, the same account email and the
same API token:

```bash
~/.claude/skills/_shared/scripts/atlassian-setup.sh
```

It asks for three things: your site URL (`https://you.atlassian.net`), the email
on your Atlassian account, and an API token from
<https://id.atlassian.com/manage-profile/security/api-tokens>. The token input is
hidden.

Setup verifies Jira access against `/rest/api/3/myself` **before** writing
anything, so a wrong token costs you nothing, then checks Confluence access too -
a warning, not a blocker, because a token can be valid for Jira with no
Confluence licence. Credentials are saved to `~/.dbhq/atlassian/config.json` at
mode 600, outside any repository.

The token never reaches a command line, including the multipart request the
attachment upload builds by hand. `curl` reads it from a 0600 config file, so it
does not appear in `ps` output or in shell history.

## Why there is no MCP server here

Confluence Cloud v2 accepts exactly two body representations: `storage` XHTML
and `atlas_doc_format`. Markdown was never one, and `wiki` was dropped. So
every published Confluence skill sits on top of the Atlassian MCP server,
which accepts a friendlier HTML dialect and converts it for you.

`htmlplus.py` here does that conversion locally. You author in Confluence
HTML+ - panels, status lozenges, task lists, decision lists, expands, page
properties, column widths - and it emits the ADF the REST API takes. No MCP
server, no vendor CLI, no network round trip to convert anything.

It also validates, rejecting invalid nesting before anything is sent, naming
the element:

    Error: A panel cannot contain a table. Close the panel and put the table
    after it as a sibling.

**That local rejection is not the API's own behaviour handed back early - it
is stricter than the API, deliberately.** Posted against a live site, the
same six malformed documents that trip this local check were accepted by the
REST API five times out of six; the one it did reject came back as a bare
`500` with no usable detail. The API is not the safety net it looks like:
Confluence's own editor silently repairs a structurally invalid document the
next time a person opens it, with nobody told. A clean local error you get
before anything is sent is worth more than a `500` you might get after, or a
silent repair you never see at all.

And because a Jira v3 issue description is ADF too, the same converter
formats a ticket. A ticket with a real warning panel, a syntax-highlighted
code block and real checkboxes, rather than a wall of plain text.

## A known limit: the round-trip gate

`confluence update` and `confluence-publish` both refuse to overwrite a page
whose current content this converter cannot read back unchanged - value
equality on the parsed ADF, not a byte-identical comparison.

`UPDATE REPLACES THE WHOLE BODY`, so writing over content like that would
silently drop whatever does not survive the round trip, not only the part you
meant to change.

**A live-site measurement once found this refusing roughly 60% of a 289-page
sample** - almost always an ordinary
node's own attribute or mark (a local id, a colspanned cell's per-column
widths, a list's start number, the editor's "make this wide" toggle) rather
than an exotic node type. Generalising the carry-through-or-opaque-passthrough
rule that already covered media to every named node type closed that gap,
and the same measurement now passes cleanly.

This is a separate guard from `--base-version`, which `update` also always
requires: `--base-version` refuses a write if the page has moved on since
the version you read, an optimistic-concurrency check unrelated to whether
the content round-trips at all. The two can refuse the same command for two
different reasons - a stale version, or content this converter cannot carry
through unchanged - and both are named here because this is the section a
refusal on either sends you to.

The usual cause is an earlier direct edit in the Confluence web editor, which
writes a node, an attribute or a mark this converter has no HTML+ form for.
This is not a bug and there is no workaround: **there is no `--force`
anywhere in this skill family.** A refusal here means a human resolves it in
the Confluence UI first, not that the skill retries harder.

Two things soften this rather than hide it:

- **Opaque passthrough.** An ADF node type this converter does not recognise
  by name still converts - as an HTML+ element carrying its ADF JSON
  verbatim, which converts straight back unchanged - rather than the whole
  document being refused outright. Before this existed, the converter itself
  refused 35 of 40 real pages sampled from a live site; a *known* type that
  would otherwise drop an attribute it cannot represent degrades the same
  way, to an opaque blob, rather than silently narrowing the page.
- **`confluence-publish --dry-run` previews the gate** against the live page
  before you run for real, so a refusal is not a surprise on the write - the
  page can still change in between, so treat the preview as "likely", not
  certain.

## Use

Talk to your agent: "raise a bug in PAY about the timeout", "what does the
wiki say about egress addresses", "publish this doc to Confluence". The
scripts are also usable directly.

### jira

```bash
cd ~/.claude/skills/jira/scripts

./jira-meta.sh projects              # project keys you can see
./jira-meta.sh types PAY             # issue type names in that project
./jira-meta.sh fields PAY Task       # what a create accepts, required marked

./jira-issues.sh create PAY Task "Summary" "Description" --label infra --priority High
./jira-issues.sh create PAY Task "Summary" --description-file /tmp/desc.html
./jira-issues.sh create PAY Task "Summary" --field customfield_10050=Ops
./jira-issues.sh bulk PAY tickets.json --dry-run
./jira-issues.sh get PAY-12
./jira-issues.sh search "assignee = currentUser() AND statusCategory != Done"
./jira-issues.sh mine
```

Issue type names are per-project. `Task` in one project may be `Story` or
`Work Item` in another, so read `types` rather than assuming.

`create` takes `--field KEY=VALUE` (repeatable) for anything the built-in
options do not cover - a project-specific required field, or a component
with no default. Read the field id with `jira-meta.sh fields` first. A
value that parses as JSON is sent as JSON (`--field components='[{"name":"Backend"}]'`);
anything else goes as plain text. `bulk` has no equivalent - its file format
only covers the seven built-in fields.

`bulk` takes a JSON array. Only `summary` is required; `type` defaults to
`Task`. **Run it with `--dry-run` first** - that prints the exact payload for
every issue and sends nothing. A batch paces itself at one request a second
to stay inside Jira's limit of roughly 60 a minute, and reports `Created:` and
`Failed:` counts at the end. A partial failure leaves the successful issues in
place, because there is no rollback: the skill cannot delete.

```json
[
  {"summary": "Enable the storage provider on the subscription", "type": "Task",
   "description": "Blocks the deployment.\n\nOnly the pipeline identity can do this.",
   "labels": ["infra"], "priority": "High"},
  {"summary": "Add retry handling to the upload step"}
]
```

Blank lines in a plain-text description become separate paragraphs. `bulk`
has no `--description-file` equivalent - every description in a bulk file is
plain text.

### confluence

```bash
cd ~/.claude/skills/confluence/scripts

./confluence-search.sh spaces
./confluence-search.sh text 'egress address'
./confluence-search.sh cql 'space = DOCS and lastmodified >= now("-7d")'

./confluence-pages.sh read 1234567                              # HTML+
./confluence-pages.sh read 1234567 --format markdown             # to understand it, never to edit
./confluence-pages.sh create --space 98765 --title "Payment Correlation" \
    --parent 1234567 --body-file /tmp/body.html
./confluence-pages.sh update 1234567 --body-file /tmp/current.html \
    --base-version 14 --message "Added the egress address"
```

Every `read` prints the page's current version and the exact flag an update
needs: `# page id 1234567, version 14 - pass --base-version 14 to update`.
`update` always needs that `--base-version` - see [§ A known limit](#a-known-limit-the-round-trip-gate)
for what it protects against and what it does not.

### confluence-publish

```bash
cd ~/.claude/skills/confluence-publish/scripts

./publish.sh docs/payment-correlation.md --dry-run
./publish.sh docs/payment-correlation.md
```

Each file carries its own binding in YAML frontmatter:

```yaml
---
confluence:
  space: "98765"
  parent: "1234567"
  page_id: "8901234"
---
```

`page_id` is written back on the first publish - **commit that change**, or
the next run creates a second page instead of updating the first.

Headings, paragraphs, lists, tables, links, inline marks and fenced code
blocks convert cleanly, and GFM task lists (`- [ ]`) become real Confluence
task lists.

A panel, a status lozenge, a decision list, a layout and a column width have
no markdown syntax at all - write them as raw HTML+ inline, which markdown
permits and this skill passes through untouched.

**Nested markdown lists are refused, not mangled.** `- one` with a
`  - nested` line indented under it stops the conversion outright, naming the
line, rather than silently splitting into two lists with the marker left as
stray text in the page. Flatten it to a top-level item, or write the nesting
directly in HTML+.

## Files

| Path | What it is |
|---|---|
| `skills/_shared/scripts/atlassian-setup.sh` | Credential capture and verification, for all three skills |
| `skills/_shared/scripts/_common.sh` | Shared request, error, ADF and credential-migration helpers |
| `skills/_shared/scripts/htmlplus.py` | The HTML+ <-> ADF converter, the round-trip gate, and opaque passthrough |
| `skills/_shared/references/house-style.md` | Conventions for writing a page or a description |
| `skills/_shared/references/html-patterns.md` | Every HTML+ pattern - panels, lozenges, tasks, layouts, tables |
| `skills/_shared/references/checklist.md` | Pre-publish checklist |
| `skills/_shared/tests/` | The converter suite and the repo-wide constraint suite |
| `skills/jira/SKILL.md` | What the agent reads for `jira` |
| `skills/jira/references/ste.md` | Simplified Technical English, for issue text |
| `skills/jira/scripts/jira-meta.sh` | Projects, issue types, fields, priorities, read-only |
| `skills/jira/scripts/jira-issues.sh` | Create, bulk create, get, search |
| `skills/confluence/SKILL.md` | What the agent reads for `confluence` |
| `skills/confluence/scripts/confluence-search.sh` | Spaces, CQL and free-text search |
| `skills/confluence/scripts/confluence-pages.sh` | Read, create, update - the stale-write and round-trip guards |
| `skills/confluence-publish/SKILL.md` | What the agent reads for `confluence-publish` |
| `skills/confluence-publish/scripts/publish.sh` | The dry-run/create/update run, and the write-back of `page_id` |
| `skills/confluence-publish/scripts/md_to_htmlplus.py` | Markdown to HTML+, with raw HTML+ passed through |
| `skills/confluence-publish/scripts/frontmatter.py` | Reads and writes the YAML binding, without `eval` |
| `skills/confluence-publish/scripts/attachments.sh` | Multipart attachment upload - the token stays off the command line here too |

## Also from DBHQ

Every DBHQ agent skill is free, open source and installable from the same
marketplace, and all of them are documented at
**[skills.dbhq.uk](https://skills.dbhq.uk)**. The marketplace itself is
[dbhq-uk/marketplace](https://github.com/dbhq-uk/marketplace) - one
`/plugin marketplace add` and every one of them is available.

| Skill | What it does |
|---|---|
| [outlook](https://skills.dbhq.uk/outlook/) | Microsoft 365 mail and calendar, from the terminal |
| [trello](https://skills.dbhq.uk/trello/) | Your boards, run from your agent |
| [legwork](https://skills.dbhq.uk/legwork/) | Research that settles a decision, and says when it cannot |
| [dovetail](https://skills.dbhq.uk/dovetail/) | Checks whether your repository still agrees with itself |
| [verve](https://skills.dbhq.uk/verve/) | Strips AI tells from prose and puts a voice back |
| [vela](https://skills.dbhq.uk/vela/) | Compiler-exact code search, in any language you index |
| [garmin](https://skills.dbhq.uk/garmin/) | Your Garmin data, answered in the terminal |
| [imager](https://skills.dbhq.uk/imager/) | Images from GPT Image 2, costed before it spends |
| [gitview](https://skills.dbhq.uk/gitview/) | Which branches are finished, and safe to delete |
| [heliograph](https://skills.dbhq.uk/heliograph/) | Change a machine you cannot log into, through an operator |
| [pennyblack](https://skills.dbhq.uk/pennyblack/) | A physical letter, posted from the terminal |
| [buildwork](https://skills.dbhq.uk/buildwork/) | Your open issues, run as parallel agents |
| [deskwork](https://skills.dbhq.uk/deskwork/) | What an agent noticed, tracked as real work |
| [groupwork](https://skills.dbhq.uk/groupwork/) | A second agent on the work, adversary or partner |
| [headwork](https://skills.dbhq.uk/headwork/) | One decision at a time, with a recommendation |

Plus [heliograph](https://skills.dbhq.uk/heliograph/), for a machine you cannot log into.

## Licence

MIT. See [LICENSE](LICENSE).
