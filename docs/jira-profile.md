# The Jira profile - what a Jira description may carry

**Source:** Atlassian's [Jira ADF structure page](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/), read on 2026-09-25
**Live check:** not yet run

`jira-issues.sh create --description-file` converts HTML+ to ADF and then
refuses any node or mark that is not in this profile. The profile lives in
`skills/atlassian/scripts/htmlplus.py` (`JIRA_LISTED_NODES`,
`JIRA_INFERRED_NODES` and `JIRA_MARKS`), and `TestJiraProfile` pins it. A
change to it fails the tests until the check below is repeated and recorded
here.

## Why a profile at all

Jira's ADF profile is narrower than Confluence's. A description that carries
a node Jira does not support can be accepted by the API and then show as
nothing on the issue. That looks like it worked, so the converter refuses it
by name before anything is sent.

Until 25 September 2026 this was a hand-kept list of "Confluence-only" nodes.
It refused `status`, `expand` and `nestedExpand`, which Atlassian lists for
Jira, and no live test stood behind it.

## What passes

Every node and mark on Atlassian's page:

- **Block nodes:** `doc`, `blockquote`, `bodiedSyncBlock`, `bulletList`,
  `codeBlock`, `expand`, `heading`, `mediaGroup`, `mediaSingle`,
  `orderedList`, `panel`, `paragraph`, `rule`, `syncBlock`, `table`,
  `multiBodiedExtension`, `blockTaskItem`, `extensionFrame`, `listItem`,
  `media`, `nestedExpand`, `tableCell`, `tableHeader`, `tableRow`
- **Inline nodes:** `date`, `emoji`, `hardBreak`, `inlineCard`, `mention`,
  `status`, `text`, `mediaInline`
- **Marks:** `border`, `code`, `em`, `link`, `strike`, `strong`, `subsup`,
  `textColor`, `underline`

And one inference: `taskList` and `taskItem`. The page lists
`blockTaskItem` but not `taskList`. The vendored ADF schema allows a
`blockTaskItem` only inside a `taskList`, so a listed `blockTaskItem`
implies the list around it. `TestJiraProfile` checks that the schema still
says so.

## What is refused

Anything else. The ones HTML+ can write by name are `decisionList`,
`decisionItem`, `layoutSection`, `layoutColumn`, `blockCard`, `embedCard`,
`caption`, and the `alignment`, `indentation` and `breakout` marks. A node
that arrives through the opaque wrapper, such as `bodiedExtension` or
`extension`, is refused the same way.

## The live check

Not yet run. To run it, on a scratch project on a test site:

1. For each node below, create one issue whose description holds only that
   node, with `jira-issues.sh create ... --description-file`. For a refused
   node, send the ADF with `curl` directly, because the script will not.
2. Open each issue in the Jira web UI and in the Jira mobile app.
3. Record here, per node, whether it renders, renders as something else, or
   shows nothing.

| Node | Profile says | Result |
|---|---|---|
| `status` | passes | not yet checked |
| `expand` | passes | not yet checked |
| `nestedExpand` (in a table cell) | passes | not yet checked |
| `taskList` and `taskItem` | passes (inferred) | not yet checked |
| `decisionList` | refused | not yet checked |
| `layoutSection` | refused | not yet checked |
| `blockCard` | refused | not yet checked |

Then make the profile match the results, update `TestJiraProfile` to the new
set, and change the "Live check" line at the top to the date.
