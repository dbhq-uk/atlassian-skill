# Security

## Reporting a vulnerability

Email <dan@dbhq.uk> rather than opening a public issue. Include what you found,
how to reproduce it, and what an attacker could do with it. You will get a first
response within 48 hours.

## What this skill does

### Network

Jira Cloud REST API v3, Confluence Cloud REST API v2, and the Confluence v1
attachment-upload endpoint - the one place this repository still uses v1,
because v2 has no attachment equivalent. All three go over HTTPS, at the site
URL you configure. Nothing else, and no telemetry.

### Credentials

`~/.dbhq/atlassian/config.json`, mode 600, holding `site`, `email`, an API
token and whether the account has Confluence access. It is written outside
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
converter cannot read back unchanged, which real-world measurement puts at
roughly 60% of a 289-page sample. Neither guard has a bypass - there is no
`--force` anywhere in this skill family. A refusal needs a human decision in
the Confluence UI, not a flag.

### Scope of the token

The API token you give it is a full-account token - Atlassian does not offer
a scoped one for this API. It can therefore see, create and update whatever
your Jira and Confluence account can, on both products, since one token
authenticates both. The skill limits itself to create, read and update; the
credential does not. Give it an account whose access you are comfortable
with.

## Third-party code

None. No packages are installed and no dependencies are pulled at runtime. It
is bash calling `curl` and `jq`, and Python 3's standard library.
