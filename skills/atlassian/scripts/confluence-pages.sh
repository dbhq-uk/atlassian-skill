#!/bin/bash
# Confluence pages - read, create and update.
#
# There is no delete and there will not be one. Deleting a page is a human job
# in the Confluence UI.
#
# Bodies are Confluence HTML+ and are converted to ADF locally by htmlplus.py,
# which rejects invalid nesting before anything is sent.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_common.sh"
HTMLPLUS="$SCRIPT_DIR/htmlplus.py"
ADF_EDIT="$SCRIPT_DIR/adf_edit.py"

usage() {
    cat >&2 <<'USAGE'
Usage:
  confluence-pages.sh read   <page-id> [--format html|markdown|adf]
  confluence-pages.sh create --space <space-id> --title <title> \
                             --body-file <file.html> [--parent <page-id>]
  confluence-pages.sh update <page-id> --body-file <file.html> \
                             --base-version <n> \
                             [--title <title>] [--message <version message>] \
                             [--dry-run]
  confluence-pages.sh edit   <page-id> --base-version <n> \
                             (--replace <local-id> | --insert-after <local-id> | --append) \
                             --body-file <fragment.html> \
                             [--message <version message>] [--dry-run]

The body file holds a Confluence HTML+ fragment. Read
references/html-patterns.md before writing one.

--format markdown is one-way, for reading only. It exists so a page is easy
to skim; there is no markdown-to-ADF path, and writing a markdown render back
to a page would destroy every native component on it (a panel, a status
lozenge, a task list). Edit in --format html and update with that.

UPDATE REPLACES THE WHOLE BODY. The script enforces the safe route rather
than just recommending it:
  1. read the page immediately before writing, in --format html - it prints
     "pass --base-version N to update"
  2. splice your change into what came back
  3. update, passing that same --base-version. If the page has moved on
     since your read, the update refuses instead of overwriting the change
     you have not seen.

EDIT CHANGES ONE NODE. It converts only the fragment and splices it into
the live page by local id (a data-local-id value from read --format html):
in place of that node, after it, or at the end of the page. Nothing else on
the page goes through the converter. It needs --base-version the same way.

--dry-run on update or edit sends nothing and prints what the write would
remove: every node whose local id would be gone, and every node with no
named HTML+ form that would no longer be there.
USAGE
    exit 1
}

# require_numeric_page_id <value> - a Confluence page id is always numeric.
# Anything else is refused here rather than reaching api(), which builds it
# into a curl config file: a page id carrying a `"` or a newline can break
# out of that file and inject a second, attacker-chosen request that still
# carries the site's credentials. api() in _common.sh refuses the same shape
# independently, so this check existing or not is not what makes the request
# safe - but failing here gives a clearer message than a generic refusal
# deeper in the call chain, and a page id was never going to be anything but
# digits in the first place.
require_numeric_page_id() {
    case "$1" in
        ''|*[!0-9]*)
            echo "Error: '$1' is not a valid page id." >&2
            echo "Cause: a Confluence page id is always numeric." >&2
            echo "Fix: pass the numeric id printed by 'read' or 'create', e.g. 1234567." >&2
            exit 1
            ;;
    esac
}

# require_body_file <path> - the file must exist and must not be empty. A
# zero-byte file converts to a well-formed but empty ADF document
# ({"type":"doc","version":1,"content":[]}), which is a valid payload as far
# as the API is concerned - so a failed redirect or a truncated write would
# otherwise reach `update` and silently blank a live page.
require_body_file() {
    [ -f "$1" ] || { echo "Error: no such file: $1" >&2; exit 1; }
    [ -s "$1" ] || {
        echo "Error: $1 is empty." >&2
        echo "Cause: a zero-byte body file converts to a valid but empty document, which would blank the page." >&2
        echo "Fix: check the file has content. Nothing was sent." >&2
        exit 1
    }
}

# to_adf_string <html-file> - convert HTML+ to ADF and emit it as the JSON
# STRING the v2 API wants in body.value. It is a string containing JSON, not a
# nested object, and sending an object is what produces "400 Invalid".
to_adf_string() {
    local adf content_length
    adf=$(python3 "$HTMLPLUS" to-adf < "$1") || {
        echo "Fix: correct the body and try again. Nothing was sent." >&2
        exit 1
    }
    content_length=$(printf '%s' "$adf" | jq -r '.content | length')
    if [ "$content_length" = "0" ]; then
        echo "Error: $1 converted to an empty document." >&2
        echo "Cause: htmlplus.py returned {\"content\":[]} - the file parsed but carries nothing to publish." >&2
        echo "Fix: check the file has content. Nothing was sent." >&2
        exit 1
    fi
    printf '%s' "$adf" | jq -Rs .
}

# read_live_page <page-id> <base-version> - read the page as it is right now,
# and refuse if it has moved on from base-version. Sets CURRENT_VERSION,
# CURRENT_TITLE, CURRENT_STATUS and CURRENT_BODY.
#
# Read immediately before writing. Not a copy read earlier in the session:
# another person or job may have landed a version in between, and sending a
# stale version number is the only thing standing between a concurrent edit
# and silent data loss. body-format=atlas_doc_format is requested here too
# (not a second call) so the same response also carries the body the
# round-trip gate and the splice work on.
read_live_page() {
    local page_id="$1" base_version="$2"
    api GET "/wiki/api/v2/pages/$page_id?body-format=atlas_doc_format"
    api_ok || api_fail "$API_BODY" "reading page $page_id before writing"
    CURRENT_VERSION=$(printf '%s' "$API_BODY" | jq -r '.version.number')
    CURRENT_TITLE=$(printf '%s' "$API_BODY" | jq -r '.title')
    CURRENT_STATUS=$(printf '%s' "$API_BODY" | jq -r '.status')
    CURRENT_BODY=$(printf '%s' "$API_BODY" | jq -r '.body.atlas_doc_format.value')

    case "$CURRENT_VERSION" in
        ''|*[!0-9]*)
            echo "Error: could not read a valid version number for page $page_id." >&2
            echo "Cause: the API response had version.number = ${CURRENT_VERSION:-<empty>}." >&2
            echo "Fix: check the page id and try again." >&2
            exit 1
            ;;
    esac
    if [ -z "$CURRENT_STATUS" ] || [ "$CURRENT_STATUS" = "null" ]; then
        echo "Error: could not read a status for page $page_id." >&2
        echo "Cause: the API response had status = ${CURRENT_STATUS:-<empty>}." >&2
        echo "Fix: check the page id and try again." >&2
        exit 1
    fi

    # The version this write is based on must still be current. This is the
    # actual stale-write guard: the read above only makes the version number
    # correct at the instant of the write, which says nothing about whether
    # the BODY being sent was composed against a version that has since
    # moved on. If --base-version does not match what the page is on right
    # now, somebody else's edit landed while this body was being written,
    # and sending anyway would silently discard it.
    if [ "$base_version" != "$CURRENT_VERSION" ]; then
        echo "Error: page $page_id has moved on since your base version." >&2
        echo "Cause: --base-version was $base_version; the page is now at version $CURRENT_VERSION." >&2
        echo "Fix: read the page again, splice your change into what comes back, and pass --base-version $CURRENT_VERSION." >&2
        exit 1
    fi
}

# put_page <page-id> <title> <message> <value> - write version
# CURRENT_VERSION + 1. value is the ADF as the JSON string body.value takes.
put_page() {
    local page_id="$1" title="$2" message="$3" value="$4" next payload conflict
    next=$((CURRENT_VERSION + 1))
    payload=$(jq -n --arg id "$page_id" --arg t "$title" \
                    --arg st "$CURRENT_STATUS" --arg m "$message" \
                    --argjson n "$next" --argjson v "$value" '
        {id: $id, status: $st, title: $t,
         version: {number: $n, message: $m},
         body: {representation: "atlas_doc_format", value: $v}}')
    api PUT "/wiki/api/v2/pages/$page_id" "$payload"
    if ! api_ok; then
        # A version conflict is not always a literal 409: Confluence has
        # been observed to answer a stale version with a 412, and with a
        # 400 whose body names the version field rather than the status
        # line. All three get the same refusal, because the one thing
        # that matters - do not retry this exact command - is the same
        # in every case.
        conflict=0
        case "$API_STATUS" in
            409|412) conflict=1 ;;
            400)
                if printf '%s' "$API_BODY" | grep -qi 'version'; then
                    conflict=1
                fi
                ;;
        esac
        if [ "$conflict" = "1" ]; then
            echo "Error: page $page_id changed while you were working on it." >&2
            echo "Cause: it is no longer at version $CURRENT_VERSION (HTTP $API_STATUS)." >&2
            echo "Fix: do not re-run this command with the same body file. Read the page again, splice your change into what comes back, and pass the new --base-version." >&2
            exit 1
        fi
        api_fail "$API_BODY" "updating page $page_id"
    fi
    echo "Updated page $page_id to version $next: $title"
    echo "Now verify: confluence-pages.sh read $page_id | less"
}

# dry_run_report <page-id> <before-adf-file> <after-adf-file> - what a write
# of after over before would remove. Sends nothing.
dry_run_report() {
    echo "Dry run: page $1, version $CURRENT_VERSION (\"$CURRENT_TITLE\")."
    python3 "$ADF_EDIT" diff "$2" "$3"
    echo "Nothing was sent."
}

CMD="${1:-}"; shift || usage

case "$CMD" in
    read)
        PAGE_ID="${1:-}"; shift || true
        [ -n "$PAGE_ID" ] || usage
        require_numeric_page_id "$PAGE_ID"
        FORMAT="html"
        while [ $# -gt 0 ]; do
            case "$1" in
                --format) [ $# -ge 2 ] || usage; FORMAT="$2"; shift 2 ;;
                *) usage ;;
            esac
        done
        case "$FORMAT" in
            html|markdown|adf) ;;
            *) echo "Error: --format must be html, markdown or adf." >&2; exit 1 ;;
        esac
        require_config
        api GET "/wiki/api/v2/pages/$PAGE_ID?body-format=atlas_doc_format"
        api_ok || api_fail "$API_BODY" "reading page $PAGE_ID"
        TITLE=$(printf '%s' "$API_BODY" | jq -r '.title')
        VERSION=$(printf '%s' "$API_BODY" | jq -r '.version.number')
        # The two header lines (and the blank lines around them) go to
        # stderr, not stdout. The documented workflow is
        # `read N > /tmp/current.html`, splice, then `update --body-file`
        # that same file - and a "# page id N, version V - ..." line ahead
        # of the fragment is loose text outside any block, which htmlplus.py
        # correctly refuses. On a terminal both streams still interleave to
        # the same tty, so this changes nothing about what a human sees
        # interactively - only what a redirect captures. publish.sh reads
        # the version back out of this exact line; see its own read calls,
        # which merge stderr back in with 2>&1 for that reason.
        echo "# $TITLE" >&2
        echo "# page id $PAGE_ID, version $VERSION - pass --base-version $VERSION to update" >&2
        echo >&2
        BODY=$(printf '%s' "$API_BODY" | jq -r '.body.atlas_doc_format.value')
        case "$FORMAT" in
            adf)      printf '%s' "$BODY" | jq . ;;
            markdown) printf '%s' "$BODY" | python3 "$HTMLPLUS" to-markdown ;;
            html)     printf '%s' "$BODY" | python3 "$HTMLPLUS" to-html ;;
        esac
        echo >&2
        ;;

    create)
        SPACE=""; TITLE=""; PARENT=""; BODY_FILE=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --space)     [ $# -ge 2 ] || usage; SPACE="$2";     shift 2 ;;
                --title)     [ $# -ge 2 ] || usage; TITLE="$2";     shift 2 ;;
                --parent)    [ $# -ge 2 ] || usage; PARENT="$2";    shift 2 ;;
                --body-file) [ $# -ge 2 ] || usage; BODY_FILE="$2"; shift 2 ;;
                *) usage ;;
            esac
        done
        [ -n "$SPACE" ] && [ -n "$TITLE" ] && [ -n "$BODY_FILE" ] || usage
        [ -z "$PARENT" ] || require_numeric_page_id "$PARENT"
        require_body_file "$BODY_FILE"
        require_config
        VALUE=$(to_adf_string "$BODY_FILE")
        PAYLOAD=$(jq -n --arg s "$SPACE" --arg t "$TITLE" --arg p "$PARENT" \
                        --argjson v "$VALUE" '
            {spaceId: $s, status: "current", title: $t,
             body: {representation: "atlas_doc_format", value: $v}}
            + (if $p == "" then {} else {parentId: $p} end)')
        api POST "/wiki/api/v2/pages" "$PAYLOAD"
        api_ok || api_fail "$API_BODY" "creating page \"$TITLE\""
        NEW_ID=$(printf '%s' "$API_BODY" | jq -r '.id')
        echo "Created page $NEW_ID: $TITLE"
        printf '%s' "$API_BODY" | jq -r '"URL: \(._links.base // "")\(._links.webui // "")"'
        ;;

    update)
        PAGE_ID="${1:-}"; shift || true
        [ -n "$PAGE_ID" ] || usage
        require_numeric_page_id "$PAGE_ID"
        BODY_FILE=""; TITLE=""; MESSAGE="Updated by the atlassian skill"; BASE_VERSION=""
        DRY_RUN=0
        while [ $# -gt 0 ]; do
            case "$1" in
                --body-file)     [ $# -ge 2 ] || usage; BODY_FILE="$2";     shift 2 ;;
                --base-version)  [ $# -ge 2 ] || usage; BASE_VERSION="$2";  shift 2 ;;
                --title)         [ $# -ge 2 ] || usage; TITLE="$2";         shift 2 ;;
                --message)       [ $# -ge 2 ] || usage; MESSAGE="$2";       shift 2 ;;
                --dry-run)       DRY_RUN=1; shift ;;
                *) usage ;;
            esac
        done
        # --base-version is required, with no default and no inference. The
        # pre-read in read_live_page makes the version number correct at the
        # instant of the write - it says nothing about whether the body
        # being sent was ever based on that version. Only the caller knows
        # which version their edit started from, so only the caller can
        # supply it; a missing value refuses rather than silently proceeding
        # on the version the pre-read happens to find.
        [ -n "$BODY_FILE" ] && [ -n "$BASE_VERSION" ] || usage
        require_body_file "$BODY_FILE"
        require_config
        read_live_page "$PAGE_ID" "$BASE_VERSION"

        # Round-trip gate. UPDATE REPLACES THE WHOLE BODY, so this write is
        # only as safe as this converter's round-trip fidelity on the page
        # actually being replaced - not just on whatever the caller's own
        # edit touches. A page this converter cannot read back unchanged
        # (a node type it has no HTML+ for at all, or a known type that
        # silently drops an attribute or a mark) would otherwise convert to
        # HTML+ for editing and then lose that content on the way back in,
        # silently. There is no --force here: a page that fails this check
        # needs a human decision, not a flag, and create has no existing
        # page to lose, so it does not go through this gate. edit, below,
        # only converts the node it replaces, so it only checks that node.
        if ! ROUNDTRIP_ERROR=$(printf '%s' "$CURRENT_BODY" | python3 "$HTMLPLUS" check-roundtrip 2>&1 1>/dev/null); then
            echo "Error: page $PAGE_ID (\"$CURRENT_TITLE\") cannot be safely edited through this skill." >&2
            # htmlplus.py's main() already prefixes its own stderr with
            # "Error: " - stripped here so this Cause: line reads like
            # every other one in this file (a plain description), not a
            # doubled "Cause: Error: ...".
            echo "Cause: ${ROUNDTRIP_ERROR#Error: }" >&2
            echo "Fix: nothing was sent. This page carries something this converter cannot round-trip exactly - editing it here risks silently losing part of it that your change never touched. To change one block, use edit with that block's local id." >&2
            exit 1
        fi

        [ -n "$TITLE" ] || TITLE="$CURRENT_TITLE"
        VALUE=$(to_adf_string "$BODY_FILE")
        if [ "$DRY_RUN" = "1" ]; then
            BEFORE=$(mktemp); AFTER=$(mktemp)
            printf '%s' "$CURRENT_BODY" > "$BEFORE"
            printf '%s' "$VALUE" | jq -r . > "$AFTER"
            dry_run_report "$PAGE_ID" "$BEFORE" "$AFTER"
            rm -f "$BEFORE" "$AFTER"
            exit 0
        fi
        put_page "$PAGE_ID" "$TITLE" "$MESSAGE" "$VALUE"
        ;;

    edit)
        PAGE_ID="${1:-}"; shift || true
        [ -n "$PAGE_ID" ] || usage
        require_numeric_page_id "$PAGE_ID"
        BODY_FILE=""; MESSAGE="Edited by the atlassian skill"; BASE_VERSION=""
        OP=""; LOCAL_ID=""; DRY_RUN=0
        while [ $# -gt 0 ]; do
            case "$1" in
                --body-file)     [ $# -ge 2 ] || usage; BODY_FILE="$2";    shift 2 ;;
                --base-version)  [ $# -ge 2 ] || usage; BASE_VERSION="$2"; shift 2 ;;
                --message)       [ $# -ge 2 ] || usage; MESSAGE="$2";      shift 2 ;;
                --replace|--insert-after)
                    [ $# -ge 2 ] || usage
                    [ -z "$OP" ] || { echo "Error: pass one of --replace, --insert-after or --append, not two." >&2; exit 1; }
                    OP="${1#--}"; LOCAL_ID="$2"; shift 2 ;;
                --append)
                    [ -z "$OP" ] || { echo "Error: pass one of --replace, --insert-after or --append, not two." >&2; exit 1; }
                    OP="append"; shift ;;
                --dry-run)       DRY_RUN=1; shift ;;
                *) usage ;;
            esac
        done
        [ -n "$BODY_FILE" ] && [ -n "$BASE_VERSION" ] && [ -n "$OP" ] || usage
        require_body_file "$BODY_FILE"
        require_config
        read_live_page "$PAGE_ID" "$BASE_VERSION"

        # Only the fragment goes through the converter. The rest of the page
        # is spliced as parsed JSON, so a node this converter has no HTML+
        # for is carried through exactly as it came.
        BEFORE=$(mktemp); AFTER=$(mktemp)
        printf '%s' "$CURRENT_BODY" > "$BEFORE"
        SPLICE_ARGS=(splice --page "$BEFORE" --fragment "$BODY_FILE" --op "$OP")
        [ -z "$LOCAL_ID" ] || SPLICE_ARGS+=(--local-id "$LOCAL_ID")
        if ! python3 "$ADF_EDIT" "${SPLICE_ARGS[@]}" > "$AFTER"; then
            rm -f "$BEFORE" "$AFTER"
            echo "Fix: nothing was sent. Correct the fragment or the local id and try again." >&2
            exit 1
        fi
        if [ "$DRY_RUN" = "1" ]; then
            dry_run_report "$PAGE_ID" "$BEFORE" "$AFTER"
            rm -f "$BEFORE" "$AFTER"
            exit 0
        fi
        VALUE=$(jq -Rs . < "$AFTER")
        rm -f "$BEFORE" "$AFTER"
        put_page "$PAGE_ID" "$CURRENT_TITLE" "$MESSAGE" "$VALUE"
        ;;

    *) usage ;;
esac
