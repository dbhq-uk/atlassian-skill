#!/bin/bash
# Publish a markdown file to Confluence, idempotently.
#
# The file is the master and Confluence is the rendering. The binding lives in
# the file's own frontmatter, so a move or a rename cannot break it:
#
#   ---
#   confluence:
#     space: "98765"
#     parent: "1234567"
#     page_id: "8901234"
#   ---
#
# page_id is written back on the first publish. No page_id means create; a
# page_id means update that page.
#
# Three things worth knowing about this script's design, each deliberate:
#
# - confluence-pages.sh update REQUIRES --base-version <n>, with no default
#   and no inference - its stale-write guard. The file being the master does
#   not make that guard pointless: it still catches a
#   concurrent edit landing in the exact window between this run starting
#   and it writing. What it changes is the REMEDY - a human editor splices
#   their change into the newer version and retries; this script has
#   nothing to splice, because the file on disk is already the whole
#   intended content, so the remedy for a moved-on version is simply to run
#   this again. The version passed is read fresh, immediately before the
#   write, the same discipline confluence-pages.sh's own docs ask a human
#   to follow by hand.
#
# - update also runs a round-trip gate: it refuses to overwrite a page whose
#   current content this converter cannot read back unchanged (observed on
#   real pages to refuse roughly 60% of the time). There is no --force
#   anywhere in this skill and this script adds none. A refusal here
#   is not a bug to work around - it means the live page carries something,
#   usually a direct edit made through the Confluence editor, that this
#   converter cannot carry through safely, and only a human in the
#   Confluence UI can resolve that. --dry-run against a file that already
#   carries a page_id previews this by running the same check against the
#   page as it stands right now, so the refusal is not a surprise on the
#   real run.
#
# - the binding is parsed with parameter expansion, never with
#   `sed 's/^/FM_/' | eval`. eval runs the value half of a frontmatter line
#   as shell too, and a markdown file is exactly the kind of thing that gets
#   cloned from somewhere else - a space value crafted as
#   `98765"; rm -rf ~ #` would execute the moment this script read the file
#   it was asked to publish, before it ever spoke to Confluence. Nothing
#   here is ever eval'd.

set -e
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTMLPLUS="$SCRIPT_DIR/htmlplus.py"
PAGES="$SCRIPT_DIR/confluence-pages.sh"

usage() {
    cat >&2 <<'USAGE'
Usage: publish.sh <file.md> [--dry-run] [--space <space-id>] [--parent <page-id>]

  --dry-run   Convert everything and preview what would happen. Sends
              nothing. If the file already carries a page_id, this also
              reads (GET only) the live page to preview whether the
              round-trip gate below would accept or refuse the update -
              a file with no page_id yet needs no credentials at all.
  --space     Space id, if the file's frontmatter does not carry one.
  --parent    Parent page id, if the file's frontmatter does not carry one.

The file's frontmatter is the binding and wins over the flags where both
are present.
USAGE
    exit 1
}

FILE="${1:-}"; shift || usage
[ -n "$FILE" ] && [ -f "$FILE" ] || usage

DRY_RUN=0; SPACE_ARG=""; PARENT_ARG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --space)   [ $# -ge 2 ] || usage; SPACE_ARG="$2";  shift 2 ;;
        --parent)  [ $# -ge 2 ] || usage; PARENT_ARG="$2"; shift 2 ;;
        *) usage ;;
    esac
done

# --- Binding: plain key=value lines, picked apart with parameter expansion.
# Never eval'd - see the header comment.
FM_OUT=$(python3 "$SCRIPT_DIR/frontmatter.py" read "$FILE")
FM_SPACE=$(printf '%s\n' "$FM_OUT" | sed -n 's/^space=//p')
FM_PARENT=$(printf '%s\n' "$FM_OUT" | sed -n 's/^parent=//p')
FM_PAGE_ID=$(printf '%s\n' "$FM_OUT" | sed -n 's/^page_id=//p')
SPACE="${FM_SPACE:-$SPACE_ARG}"
PARENT="${FM_PARENT:-$PARENT_ARG}"
PAGE_ID="$FM_PAGE_ID"

# --- Convert. This is where a bad body fails, before anything is sent. ---
# `set -o pipefail` above matters here specifically: without it, a crash in
# frontmatter.py (a non-UTF-8 file, say) still leaves this pipeline's exit
# status at 0, because only md_to_htmlplus.py's own status is seen - and
# md_to_htmlplus.py, fed nothing because the first stage died, converts an
# empty input to an empty BODY and exits cleanly. pipefail makes the
# pipeline's exit status the first non-zero status in it, so that crash now
# stops this script here, at set -e, instead of sailing through as a
# successful empty conversion.
BODY=$(mktemp); trap 'rm -f "$BODY"' EXIT
python3 "$SCRIPT_DIR/frontmatter.py" body "$FILE" \
    | python3 "$SCRIPT_DIR/md_to_htmlplus.py" > "$BODY"

# --- Refuse an empty body here, before the banner below ever gets built. ---
# The banner is never empty by itself - it is a fixed info panel - so a
# BANNER built by prepending it to an empty BODY is itself non-empty and
# sails straight past both of confluence-pages.sh's guards: require_body_file
# only checks the file is non-zero bytes, and to_adf_string's
# `content|length == 0` check runs on the banner-plus-body ADF, which is
# never empty once the banner is in it. Checked here, on BODY alone, before
# either guard ever sees this write: a file that is only frontmatter, or one
# truncated to something that converts to nothing (an HTML comment, stray
# whitespace), must not silently become "the page now says only the source
# banner" with an exit 0 and no warning.
BODY_ADF=$(python3 "$HTMLPLUS" to-adf < "$BODY") || {
    echo "Error: $FILE did not convert to a usable body." >&2
    echo "Cause: the HTML+ conversion failed - see the error above. Nothing was sent." >&2
    echo "Fix: correct $FILE and try again." >&2
    exit 1
}
if [ "$(printf '%s' "$BODY_ADF" | jq -r '.content | length')" = "0" ]; then
    echo "Error: $FILE converts to an empty body." >&2
    echo "Cause: the file is only frontmatter, or its content converts to nothing to publish - htmlplus.py returned {\"content\":[]}." >&2
    echo "Fix: add content to $FILE and try again. Nothing was sent - not even the source banner." >&2
    exit 1
fi

# --- Title: the first <h1> the conversion emitted, else the filename ---
# Not `grep -m1 '^# ' "$FILE"` over the raw file. That pattern also matches
# a YAML comment inside the frontmatter block ("# a note to self") and a
# shell comment inside a fenced code block ("# install the thing first"),
# both ordinary shapes in a real document, and both become the page --title
# on every subsequent update - silently renaming a live page to the wrong
# thing.
# md_to_htmlplus.py has already stripped the frontmatter (it reads
# frontmatter.py's `body`, not the raw file) and tracked fences correctly
# by the time BODY exists, so the <h1> it actually emitted is read back
# out here instead, rather than re-deriving the same wrong answer a second
# way. Tags inside the heading (**bold** becomes <strong>, and similarly
# for the other inline marks) are stripped and entities unescaped, so the
# title is plain text, not a fragment of HTML+.
TITLE=$(python3 -c '
import html, re, sys
body = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"<h1>(.*?)</h1>", body, re.S)
if match:
    text = re.sub(r"<[^>]+>", "", match.group(1))
    print(html.unescape(text).strip())
' "$BODY")
[ -n "$TITLE" ] || TITLE=$(basename "$FILE" .md)

# The source banner. A reader who cannot edit needs somewhere to put a
# correction, so the warning names the route as well as the rule - a warning
# without a route just tells people their feedback has nowhere to go.
#
# $FILE is escaped before it goes into this HTML+ text content - a path
# containing < or > would otherwise corrupt the fragment rather than read
# oddly. The to-adf proof just below still refuses a broken fragment and
# sends nothing either way, so this was never a way to smuggle something
# through - but a correct conversion is better than one that merely fails
# safe.
FILE_ESC=$(printf '%s' "$FILE" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
BANNER=$(mktemp); trap 'rm -f "$BODY" "$BANNER"' EXIT
{
    printf '<div data-type="panel-info"><p>'
    printf 'Generated from <code>%s</code> in version control. ' "$FILE_ESC"
    printf 'An edit made here is lost at the next publish - '
    printf 'leave a page comment instead and it is read back into the source.'
    printf '</p></div>'
    cat "$BODY"
} > "$BANNER.full" && mv "$BANNER.full" "$BANNER"

# Prove it converts before reporting anything as safe.
if ! python3 "$HTMLPLUS" to-adf < "$BANNER" > /dev/null; then
    echo "Fix: correct $FILE and try again. Nothing was sent." >&2
    exit 1
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "Dry run: $FILE"
    echo "  Title:  $TITLE"
    echo "  Space:  ${SPACE:-<none - pass --space>}"
    echo "  Parent: ${PARENT:-<none>}"
    if [ -n "$PAGE_ID" ]; then
        echo "  Action: UPDATE page $PAGE_ID"
        # Preview the round-trip gate against the page as it stands right
        # now. "read" is GET only, so this does not break the "--dry-run
        # sends nothing" promise - it can still go stale before the real
        # run, which is why a refusal is previewed as "would be", not
        # asserted as certain.
        if READ_OUT=$("$PAGES" read "$PAGE_ID" --format adf 2>&1); then
            ADF_JSON=$(printf '%s\n' "$READ_OUT" | grep -v '^#')
            if ROUNDTRIP_ERR=$(printf '%s' "$ADF_JSON" \
                    | python3 "$HTMLPLUS" check-roundtrip 2>&1 1>/dev/null); then
                echo "  Round trip: page $PAGE_ID reads back unchanged - the update is expected to be accepted"
            else
                echo "  Round trip: WOULD LIKELY BE REFUSED - ${ROUNDTRIP_ERR#Error: }"
                echo "    No --force exists for this. Fix the page in the Confluence UI first, or the real run will refuse the same way."
            fi
        else
            echo "  Round trip: could not check - reading page $PAGE_ID failed:"
            printf '%s\n' "$READ_OUT" | sed 's/^/    /'
        fi
    else
        echo "  Action: CREATE, then write page_id back into the frontmatter"
    fi
    echo "  Body:   converts cleanly to ADF"
    echo "Nothing was sent."
    exit 0
fi

if [ -n "$PAGE_ID" ]; then
    # Read the version fresh, immediately before the write - not cached from
    # anywhere earlier in this run. See the header comment: the file is the
    # master, so there is nothing to splice, only something to notice if a
    # concurrent edit is in flight right now.
    #
    # 2>&1: confluence-pages.sh read now prints its "# page id N, version V"
    # line on stderr (so a redirected `read > file` yields a clean body),
    # and the version below is parsed off exactly that line - so stderr has
    # to be captured here too, unlike a plain stdout-only read.
    if ! READ_OUT=$("$PAGES" read "$PAGE_ID" --format adf 2>&1); then
        echo >&2
        echo "Publish stopped: could not read page $PAGE_ID before updating. Nothing was sent." >&2
        printf '%s\n' "$READ_OUT" | sed 's/^/    /' >&2
        exit 1
    fi
    BASE_VERSION=$(printf '%s\n' "$READ_OUT" \
        | sed -n 's/^# page id [0-9]*, version \([0-9]*\).*/\1/p')
    [ -n "$BASE_VERSION" ] || {
        echo "Error: could not read the current version of page $PAGE_ID." >&2
        echo "Cause: 'read' did not print a version line for it." >&2
        echo "Fix: run confluence-pages.sh read $PAGE_ID directly and check the page exists." >&2
        exit 1
    }
    if ! "$PAGES" update "$PAGE_ID" --body-file "$BANNER" --title "$TITLE" \
            --base-version "$BASE_VERSION" --message "Published from $FILE"; then
        echo >&2
        echo "Publish stopped: $FILE was not sent to page $PAGE_ID. Nothing changed. See the error above." >&2
        echo "If the page moved on since this run started, just run publish.sh again - the file is the master, so there is nothing to splice by hand." >&2
        echo "If the converter refused the round trip, there is no --force: resolve it directly in the Confluence UI, then re-run." >&2
        exit 1
    fi
else
    [ -n "$SPACE" ] || {
        echo "Error: no space id." >&2
        echo "Cause: the frontmatter has no confluence.space and --space was not passed." >&2
        echo "Fix: add one, or pass --space <id>. List ids with confluence-search.sh spaces." >&2
        exit 1
    }
    CREATE_ARGS=(--space "$SPACE" --title "$TITLE" --body-file "$BANNER")
    [ -n "$PARENT" ] && CREATE_ARGS+=(--parent "$PARENT")
    if ! OUT=$("$PAGES" create "${CREATE_ARGS[@]}"); then
        echo >&2
        echo "Publish stopped: $FILE was not sent. Nothing was created. See the error above." >&2
        exit 1
    fi
    echo "$OUT"
    NEW_ID=$(printf '%s' "$OUT" | sed -n 's/^Created page \([0-9]*\):.*/\1/p')
    [ -n "$NEW_ID" ] || { echo "Error: could not read the new page id." >&2; exit 1; }
    # This is the one failure this script can detect that SKILL.md's own
    # "commit that change, or the next run creates a second page" warning
    # is about - a page now exists live, and if its id does not reach the
    # file, nothing else will notice until that second page appears.
    # Guarded like every other write path above, not left to a raw
    # traceback (proven: a read-only directory makes frontmatter.py's
    # set-page-id die with an uncaught PermissionError).
    if ! WRITE_ERR=$(python3 "$SCRIPT_DIR/frontmatter.py" set-page-id "$FILE" "$NEW_ID" 2>&1); then
        echo >&2
        echo "Error: page $NEW_ID was created but its id could not be written to $FILE." >&2
        echo "Cause: $(printf '%s' "$WRITE_ERR" | tail -n1)" >&2
        echo "Fix: add    page_id: \"$NEW_ID\"    under confluence: in $FILE by hand before re-running," >&2
        echo "     or the next run will create a second page." >&2
        exit 1
    fi
    echo "Wrote page_id $NEW_ID into $FILE - commit that change."
fi
