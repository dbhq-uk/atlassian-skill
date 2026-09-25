#!/bin/bash
# Reading and writing one Confluence page, shared by confluence-pages.sh and
# publish.sh. Sourced after _common.sh; not run directly.

# fetch_page <page-id> - read the page as it is right now. Sets
# CURRENT_VERSION, CURRENT_TITLE, CURRENT_STATUS, CURRENT_BODY (the ADF, as
# JSON text), and the latest version's CURRENT_MESSAGE, CURRENT_AUTHOR (an
# account id) and CURRENT_WHEN.
fetch_page() {
    local page_id="$1"
    api GET "/wiki/api/v2/pages/$page_id?body-format=atlas_doc_format"
    api_ok || api_fail "$API_BODY" "reading page $page_id before writing"
    # The CURRENT_* variables are how fetch_page returns. The scripts that
    # source this file read them; shellcheck sees only this file, so it
    # cannot tell, hence the SC2034 lines.
    CURRENT_VERSION=$(printf '%s' "$API_BODY" | jq -r '.version.number')
    # shellcheck disable=SC2034
    CURRENT_TITLE=$(printf '%s' "$API_BODY" | jq -r '.title')
    CURRENT_STATUS=$(printf '%s' "$API_BODY" | jq -r '.status')
    # shellcheck disable=SC2034
    CURRENT_BODY=$(printf '%s' "$API_BODY" | jq -r '.body.atlas_doc_format.value')
    # shellcheck disable=SC2034
    CURRENT_MESSAGE=$(printf '%s' "$API_BODY" | jq -r '.version.message // ""')
    # shellcheck disable=SC2034
    CURRENT_AUTHOR=$(printf '%s' "$API_BODY" | jq -r '.version.authorId // "an unknown author"')
    # shellcheck disable=SC2034
    CURRENT_WHEN=$(printf '%s' "$API_BODY" | jq -r '.version.createdAt // "an unknown date"')

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
    fetch_page "$page_id"

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
