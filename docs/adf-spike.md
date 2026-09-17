# ADF write path - spike result

**Date:** 2026-09-17
**Verdict:** PASS

Confluence Cloud v2 accepts `body.representation=atlas_doc_format` on both
`POST /wiki/api/v2/pages` and `PUT /wiki/api/v2/pages/{id}`.

## What was proved

A page was created carrying a `panel`, a `status` node and a `taskList`, then
read back with `?body-format=atlas_doc_format`. All three returned unchanged:

```
node types:      paragraph, panel, paragraph, taskList
panel type:      warning
status:          Built/green
taskItem state:  TODO
```

The status code was not the test. A `200` that flattened the panel into a
paragraph would be the worst outcome, because it looks like it worked - so the
nodes were read back and checked by type.

Update then succeeded and returned version 2.

## What the API actually wants

- **`body.value` is a JSON string containing the ADF document, not a nested
  object.** This is almost certainly what the `400 Invalid` reports in the
  Atlassian developer community are: sending the document as an object.
- Update requires `id`, `status`, `title` and `version.number` set to the
  current version plus one. Omitting any of them fails.
- A `taskList` and a `taskItem` each take a `localId` attribute. An empty
  string is accepted and the server assigns one.

## Consequence

The design stands: author in Confluence HTML+, convert to ADF locally, publish
over plain REST with no MCP server. The storage-XHTML fallback is not needed.
