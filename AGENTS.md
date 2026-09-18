# AGENTS.md

Guidance for AI agents (and people) working in this repository.

## What this is

**atlassian** - three agent skills that talk to an Atlassian Cloud site over
its REST API, on one credential and with no MCP server. `jira` creates and
reads issues, `confluence` searches, reads, creates and updates pages, and
`confluence-publish` turns a repository's markdown into pages in a space.
They follow the [Agent Skills](https://agentskills.io) layout
(`skills/<name>/SKILL.md`) and ship as a
[Claude Code plugin](https://code.claude.com/docs/en/plugins).

## Layout

```
.claude-plugin/plugin.json          # plugin manifest
skills/_shared/scripts/             # one credential helper, one setup, one converter
skills/_shared/references/          # house style, HTML+ patterns, pre-publish checklist
skills/_shared/tests/               # converter and repo-constraint tests
skills/jira/                        # issues
skills/confluence/                  # pages
skills/confluence-publish/          # markdown to pages
install.sh / install-codex.sh       # local symlink installers (Claude / Codex)
```

`_shared` is not a skill - it has no `SKILL.md`. It is the directory the three
skills reach as `${CLAUDE_SKILL_DIR}/../_shared/`. `install-codex.sh` treats
it differently for exactly that reason: every other directory under `skills/`
gets its `SKILL.md` rewritten at install time, and `_shared` has none to
rewrite, so it is symlinked whole instead - the way `install.sh` already
handles every directory for Claude Code.

## The constraints that must not be broken

Everything else here is a preference. These are not.

**1. It creates, reads and updates. It does not delete.** No delete anywhere -
not a page, not an issue, not an attachment, not a space. No bulk transition,
no project or space administration. `jira bulk` keeps `--dry-run`. A partial
failure leaves the successful work in place, and that is stated rather than
hidden, because the skills have no way to roll back. Anything destructive
stays a human job in the Atlassian UI. `test_constraints.py`'s
`TestNoDestructiveVerb` asserts no script issues a `DELETE`.

**2. The token never reaches a command line.** `curl` reads the URL, the
credentials and the method from a 0600 config file, so the token stays out of
`ps` output and out of shell history. Never pass it as an argument, never
interpolate it into a URL, and never echo it. This holds for the multipart
attachment upload too (`confluence-publish/scripts/attachments.sh`) - it
cannot go through the shared `api()` helper, because a file upload needs
`curl -F`, not a JSON body, so it builds its own `-K` config file for the
same reason instead.

**3. Check before you write.** Jira: the project key, the issue type and the
required fields are read from the live Jira before a create call is built -
issue type names are per-project, so read `types` rather than assuming.
Confluence: `update` requires `--base-version <n>` - the version the edit was
composed against - with no default and no inference. It re-reads the page's
live version immediately before writing and compares that against
`--base-version`; if they differ, it refuses rather than overwriting. The
re-read on its own guards nothing - it only makes the version number correct
at the instant of the write. **`--base-version` is what makes the refusal
possible**: it is the one value in that comparison that dates from before the
edit was composed, and a mismatch is exactly what a concurrent edit produces.

**4. Bodies are HTML+, converted locally to ADF.** Never markdown on the
wire, never storage format, and no MCP server. `htmlplus.py` rejects invalid
nesting locally before anything is sent, naming the element and its parent -
not because the Confluence REST API can be trusted to catch it. Posted
against a live site, it accepted five of six deliberately malformed
documents; the one it rejected returned a bare `500` with no usable detail.
Local validation is still correct, but the reason is not "the API rejects
this with a descriptive error" - the API mostly does not reject it at all.
The Confluence editor silently repairs what the API swallowed, on the next
human edit, with nobody told, which is worse than a clean local rejection.
Do not weaken this validator to make a test pass, and do not relax a rule on
the grounds that "the API accepted it" - the API accepting a document is not
evidence it is well-formed.

**5. Named rendering is never worse than opaque passthrough.** An ADF node
type or mark this converter does not recognise by name still converts - as an
HTML+ element carrying its ADF JSON verbatim, which converts straight back -
rather than raising and refusing the whole document. Before this existed, the
converter refused 35 of 40 real pages sampled from a live site. A handler for
a *known* type that would otherwise drop an attribute it cannot represent
must degrade the same way, to an opaque blob, rather than silently narrowing
the page.

**6. A write that cannot be round-tripped is refused, not attempted, and
there is no `--force`.** Before `confluence-pages.sh update` (and
`confluence-publish`'s `publish.sh`, which calls it) sends anything, it
converts the page's current live content back through the converter and
checks the result matches (Python value equality on the ADF, not a byte- or
string-identical comparison - `1800.0 == 1800` is equal, and correctly so).
If it does not - typically because a
human edited the page directly in the Confluence editor and wrote something
this converter has no HTML+ form for - the write is refused, because sending
it would silently drop whatever does not survive the round trip, not only the
part the caller meant to change. Measured across 289 real pages, this refuses
roughly 60% of the time. That is expected behaviour on a page with editing
history outside this pipeline, not a sign anything is broken, and it is not a
bug to route around: there is no `--force` anywhere in this skill family, on
either script.

**7. No packages, no venv, no credential in the repo.** Bash plus `curl` and
`jq`; Python 3 standard library only. Nothing is installed at runtime.

**8. Nothing client-specific.** The references were sanitised from a client's
house style. `test_constraints.py` fails the build on a client name, a
security-proxy host, a real-looking ticket id, an IP address, or a source
proper noun that slipped past those shapes. A user's own conventions go in
`~/.dbhq/atlassian/house-style.md`, outside the repository - and every write
path's `SKILL.md` is required to say so (`TestHouseStyleIsOnTheWritePath`).

## Conventions

- Any path `SKILL.md` names goes through `${CLAUDE_SKILL_DIR}`, which Claude Code
  substitutes for personal, project and plugin installs alike. **Never hardcode
  `~/.claude/skills/jira` (or `confluence`, or `confluence-publish`) or any
  absolute path** - it is wrong under a Codex install and wrong under a plugin
  install. `install-codex.sh` rewrites the variable at install time for each of
  the three skills, because Codex does not substitute it; `_shared` carries no
  `SKILL.md` of its own, so it is symlinked whole instead of stepped through
  that rewrite (see Layout, above).
- `SKILL.md` is the short half on purpose. The workflow, the constraints and the
  checks live there; the reasoning and the reference material live in
  `references/` and are read on demand.
- Shell scripts use `set -e`; errors go to stderr, output to stdout.
- Every example is generic: `PAY-12`, `mycompany.atlassian.net`. `test_constraints.py`
  enforces this - not a CI grep on its own - and fails the build on anything that
  looks like a real client name, a security-proxy host, a ticket id matching
  `[A-Z]{3,}-[0-9]{3,}`, or an IP address, scanned across every file `git ls-files`
  tracks.
- House style: British English, plain hyphens, **no em dashes** -
  `test_constraints.py`'s `TestNoEmDash` fails the build on one. No trailing full
  stops on headings.

## Validating a change

```bash
cd ~/dbhq-uk/atlassian-skill
for s in skills/*/scripts/*.sh install.sh install-codex.sh; do bash -n "$s"; shellcheck -S warning -x "$s"; done
python3 -m unittest discover -s skills/_shared/tests -v
python3 -m unittest discover -s skills/confluence-publish/tests -v
for p in skills/*/scripts/*.py; do python3 -m py_compile "$p"; done
jq empty .claude-plugin/plugin.json
```

`-x` on shellcheck follows the `# shellcheck source=` directives that pull in
`_shared/scripts/_common.sh`, which it cannot do without it.

CI runs the Python test suite and the constraint gate above (`.github/workflows/validate.yml`,
job `validate`), plus a repository-wide shellcheck and `ruff --select E9,F` pass over every
script (job `lint`, shared across every DBHQ skill repo - see that job's own comment for why it
is scoped the way it is) and the SKILL.md frontmatter check.

## History

This skill lived here, moved into the `devskills` pack on 13 September 2026, and
came back to its own repository on 17 September 2026. The pack was split because
its two skills shared no API, no credential and no subject - `devskills` itself
recorded that they "have nothing in common beyond being things a developer needs
mid-task". The other half is [gitview](https://github.com/dbhq-uk/gitview-skill).

`jira` became `atlassian` on 17 September 2026, absorbing `confluence` and
`confluence-publish`. The reason is the rule the `devskills` split produced
that same morning: a repository holds the skills that change together. Jira
Cloud and Confluence Cloud take the same site URL, the same account email and
the same API token, so they share a credential store and a setup script.
GitHub redirects `dbhq-uk/jira-skill` indefinitely.
