# Security

## Reporting a vulnerability

Email <dan@dbhq.uk> rather than opening a public issue. Include what you found,
how to reproduce it, and what an attacker could do with it. You will get a first
response within 48 hours.

## What this skill does

### Network

Jira Cloud REST API v3 over HTTPS, at the site URL you configure. Nothing else,
and no telemetry.

### Credentials

`~/.dbhq/jira/config.json`, mode 600, holding `site`, `email` and an API token. It is
written outside any repository. Setup verifies the credential against
`/rest/api/3/myself` before writing anything, so a wrong token costs you nothing.

**The token never reaches a command line.** `curl` reads the URL, the credentials
and the method from the 0600 config file, so the token does not appear in `ps`
output or in your shell history.

The file path is documented here deliberately. A credential store you cannot find
is harder to audit, not safer, and owner-only permissions are what `gh`, `aws`,
`docker` and `kubectl` all do.

### What it can do to your Jira

**It creates and reads only.** There is no delete, no bulk transition, and no
project administration. A partial bulk failure leaves the created issues in place
because there is no rollback, which is a consequence of the same constraint: the
skill cannot delete an issue it made. Anything destructive stays a human job in
the Jira UI.

`bulk` supports `--dry-run`, which prints the exact payload for every issue and
sends nothing.

### Scope of the token

The API token you give it is a full-account token - Atlassian does not offer a
scoped one for this API. It can therefore see and create whatever your Jira
account can. The skill limits itself to create and read; the credential does not.
Give it an account whose access you are comfortable with.

## Third-party code

None. No packages are installed and no dependencies are pulled at runtime. It is
bash calling `curl` and `jq`.
