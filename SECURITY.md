# Security

## Reporting a vulnerability

Email <dan@dbhq.uk> rather than opening a public issue. Include what you found,
how to reproduce it, and what an attacker could do with it. You will get a first
response within 48 hours.

## What this skill does

### Network

Jira Cloud REST API v3 and Confluence Cloud REST API v2, plus two Confluence
v1 endpoints that v2 has no equivalent for: the attachment upload and the CQL
search. Every call goes over HTTPS, to the site URL you configure, or to
`api.atlassian.com` for a scoped token (see below). Nothing else, and no
telemetry.

### Credentials

`~/.dbhq/atlassian/config.json`, mode 600, holding `site`, `email`, an API
token, whether the account has Confluence access, and where requests go. It is written outside
any repository. Setup verifies the credential against `/rest/api/3/myself`
before writing anything, so a wrong token costs you nothing, then checks
Confluence access too - a warning, not a blocker, since a token can be valid
for Jira with no Confluence licence.

**The token never reaches a command line.** `curl` reads the URL, the
credentials and the method from the 0600 config file for every request,
including the multipart attachment upload, which builds its own config file
for the same reason rather than going through the shared JSON-body helper -
so the token does not appear in `ps` output or in your shell history. The
same discipline applies to writing the credential in the first place: setup
passes the token to `jq` through its environment, not as a command-line
`--arg`, since a process's own argv is as readable as any other command line.

The file path is documented here deliberately. A credential store you cannot
find is harder to audit, not safer, and owner-only permissions are what `gh`,
`aws`, `docker` and `kubectl` all do.

### What it can do to your Jira and Confluence

**It creates, reads and updates. It never deletes.** There is no delete
anywhere - not an issue, not a page, not an attachment, not a space - no bulk
transition, and no project or space administration. Anything destructive
stays a human job in the Atlassian UI.

`jira bulk` supports `--dry-run`, which prints the exact payload for every
issue and sends nothing. A partial bulk failure leaves the created issues in
place, because there is no rollback - a consequence of the same constraint:
the skill cannot delete what it made.

**A Confluence `update` replaces the whole page body**, not a diff or a
patch - there is no partial edit on the API this skill sits on. Two guards
sit in front of every write: `--base-version` refuses to write if the page
has moved on since the version the caller read, and a round-trip gate
refuses to overwrite a page whose current content this repository's
converter cannot read back unchanged - a live-site measurement once put
this at roughly 60% of a 289-page sample, before this converter generalised
its attribute and mark carry-through to every named node type; the same
measurement now passes cleanly. Neither guard has a bypass - there is no
`--force` anywhere in this skill. A refusal needs a human decision in
the Confluence UI, not a flag.

`publish.sh` also replaces the whole body, with the file. It refuses when the
page's latest version was not written by a publish - somebody edited the page
in Confluence - until that exact version is confirmed with `--base-version`.
It uploads the local images a file shows as page attachments, and nothing
else.

### Scope of the token

Setup takes either kind of Atlassian API token, and works out which it was
given.

**A classic API token** (Create API token) can do anything your account can
do, on Jira and Confluence alike, since one token authenticates both. It
calls your site URL. The skill limits itself to create, read and update; the
credential does not.

**A scoped API token** (Create API token with scopes) can do only what its
scopes allow. It must call Atlassian's gateway,
`https://api.atlassian.com/ex/jira/<cloud id>` and
`https://api.atlassian.com/ex/confluence/<cloud id>`, not the site. Setup
reads the cloud id from `<site>/_edge/tenant_info`, which needs no
credential. It tries the token on the site, and on a 401 tries the gateway.
It records a base URL per product, and every request goes there. See
[Manage API tokens](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/).

**A scoped token with no delete scope is the only way to make "never
deletes" hold at the credential**, not only in the scripts. Use granular
scopes. The classic `write:jira-work` scope includes deleting issues, and
Atlassian's API reference gates Confluence v1 deletes, including deleting a
page tree, behind the classic `write:confluence-content`. So neither
belongs on a token for this skill.

The granular scopes the skill needs, taken from the "OAuth 2.0 scopes
required" of every endpoint it calls (read 25 September 2026):

- **Jira**: `read:application-role:jira`, `read:avatar:jira`,
  `read:comment:jira`, `read:comment.property:jira`, `read:field:jira`,
  `read:field-configuration:jira`, `read:field.default-value:jira`,
  `read:field.option:jira`, `read:group:jira`, `read:issue:jira`,
  `read:issue-details:jira`, `read:issue-meta:jira`,
  `read:issue-security-level:jira`, `read:issue-type:jira`,
  `read:issue-type-hierarchy:jira`, `read:issue.changelog:jira`,
  `read:issue.vote:jira`, `read:priority:jira`, `read:project:jira`,
  `read:project-category:jira`, `read:project-role:jira`,
  `read:project-version:jira`, `read:project.component:jira`,
  `read:project.property:jira`, `read:status:jira`, `read:user:jira`,
  `write:attachment:jira`, `write:comment:jira`,
  `write:comment.property:jira` and `write:issue:jira`.
- **Confluence**: `read:space:confluence`, `read:page:confluence`,
  `write:page:confluence`, `read:attachment:confluence`,
  `read:content-details:confluence` and `write:attachment:confluence`.

None of them is a `delete:` scope. A scope cannot be added to a token after
it is made, so a missing one means a new token.

Two limits. Neither has been checked against a live site yet:

- **A scoped token may cover one app only.** Atlassian's steps for making
  one say to select the app. Setup needs Jira access, so a scoped Jira
  token gives you Jira, and setup reports Confluence as unavailable if the
  token has no Confluence scopes.
- **Every API token expires**, after at most one year. A 401 from any
  command says so.

## Third-party code

None. No packages are installed and no dependencies are pulled at runtime. It
is bash calling `curl` and `jq`, and Python 3's standard library.

One data file is vendored: Atlassian's published ADF JSON schema
(`@atlaskit/adf-schema`, Apache-2.0), at
`skills/atlassian/scripts/adf-schema/full.json` with its licence beside it. The
converter reads it as plain JSON to check nesting. It is not code and nothing
executes it.
