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
# shellcheck source=../../_shared/scripts/_common.sh
. "$SCRIPT_DIR/../../_shared/scripts/_common.sh"
HTMLPLUS="$SCRIPT_DIR/../../_shared/scripts/htmlplus.py"

usage() {
    cat >&2 <<'USAGE'
Usage:
  confluence-pages.sh read   <page-id> [--format html|markdown|adf]
  confluence-pages.sh create --space <space-id> --title <title> \
                             --body-file <file.html> [--parent <page-id>]
  confluence-pages.sh update <page-id> --body-file <file.html> \
                             [--title <title>] [--message <version message>]

The body file holds a Confluence HTML+ fragment. Read
_shared/references/html-patterns.md before writing one.

UPDATE REPLACES THE WHOLE BODY. Always:
  1. read the page immediately before writing, in --format html
  2. splice your change into what came back
  3. update, then read it again and check the section you changed
USAGE
    exit 1
}

# to_adf_string <html-file> - convert HTML+ to ADF and emit it as the JSON
# STRING the v2 API wants in body.value. It is a string containing JSON, not a
# nested object, and sending an object is what produces "400 Invalid".
to_adf_string() {
    local adf
    adf=$(python3 "$HTMLPLUS" to-adf < "$1") || {
        echo "Fix: correct the body and try again. Nothing was sent." >&2
        exit 1
    }
    printf '%s' "$adf" | jq -Rs .
}

CMD="${1:-}"; shift || usage

case "$CMD" in
    read)
        PAGE_ID="${1:-}"; shift || true
        [ -n "$PAGE_ID" ] || usage
        FORMAT="html"
        while [ $# -gt 0 ]; do
            case "$1" in
                --format) FORMAT="${2:-}"; shift 2 ;;
                *) usage ;;
            esac
        done
        require_config
        api GET "/wiki/api/v2/pages/$PAGE_ID?body-format=atlas_doc_format"
        api_ok || api_fail "$API_BODY" "reading page $PAGE_ID"
        TITLE=$(printf '%s' "$API_BODY" | jq -r '.title')
        VERSION=$(printf '%s' "$API_BODY" | jq -r '.version.number')
        echo "# $TITLE"
        echo "# page id $PAGE_ID, version $VERSION"
        echo
        BODY=$(printf '%s' "$API_BODY" | jq -r '.body.atlas_doc_format.value')
        case "$FORMAT" in
            adf)      printf '%s' "$BODY" | jq . ;;
            markdown) printf '%s' "$BODY" | python3 "$HTMLPLUS" to-markdown ;;
            html)     printf '%s' "$BODY" | python3 "$HTMLPLUS" to-html ;;
            *) echo "Error: --format must be html, markdown or adf." >&2; exit 1 ;;
        esac
        echo
        ;;

    create)
        SPACE=""; TITLE=""; PARENT=""; BODY_FILE=""
        while [ $# -gt 0 ]; do
            case "$1" in
                --space)     SPACE="${2:-}";     shift 2 ;;
                --title)     TITLE="${2:-}";     shift 2 ;;
                --parent)    PARENT="${2:-}";    shift 2 ;;
                --body-file) BODY_FILE="${2:-}"; shift 2 ;;
                *) usage ;;
            esac
        done
        [ -n "$SPACE" ] && [ -n "$TITLE" ] && [ -n "$BODY_FILE" ] || usage
        [ -f "$BODY_FILE" ] || { echo "Error: no such file: $BODY_FILE" >&2; exit 1; }
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
        BODY_FILE=""; TITLE=""; MESSAGE="Updated by the confluence skill"
        while [ $# -gt 0 ]; do
            case "$1" in
                --body-file) BODY_FILE="${2:-}"; shift 2 ;;
                --title)     TITLE="${2:-}";     shift 2 ;;
                --message)   MESSAGE="${2:-}";   shift 2 ;;
                *) usage ;;
            esac
        done
        [ -n "$BODY_FILE" ] || usage
        [ -f "$BODY_FILE" ] || { echo "Error: no such file: $BODY_FILE" >&2; exit 1; }
        require_config

        # Read the CURRENT version immediately before writing. Not a copy read
        # earlier in the session: another person or job may have landed a
        # version in between, and sending a stale version number is the only
        # thing standing between a concurrent edit and silent data loss.
        api GET "/wiki/api/v2/pages/$PAGE_ID"
        api_ok || api_fail "$API_BODY" "reading page $PAGE_ID before update"
        CURRENT_VERSION=$(printf '%s' "$API_BODY" | jq -r '.version.number')
        CURRENT_TITLE=$(printf '%s' "$API_BODY" | jq -r '.title')
        CURRENT_STATUS=$(printf '%s' "$API_BODY" | jq -r '.status')
        [ -n "$TITLE" ] || TITLE="$CURRENT_TITLE"
        NEXT_VERSION=$((CURRENT_VERSION + 1))

        VALUE=$(to_adf_string "$BODY_FILE")
        PAYLOAD=$(jq -n --arg id "$PAGE_ID" --arg t "$TITLE" \
                        --arg st "$CURRENT_STATUS" --arg m "$MESSAGE" \
                        --argjson n "$NEXT_VERSION" --argjson v "$VALUE" '
            {id: $id, status: $st, title: $t,
             version: {number: $n, message: $m},
             body: {representation: "atlas_doc_format", value: $v}}')
        api PUT "/wiki/api/v2/pages/$PAGE_ID" "$PAYLOAD"
        if ! api_ok; then
            if [ "$API_STATUS" = "409" ]; then
                echo "Error: page $PAGE_ID changed while you were working on it." >&2
                echo "Cause: it is no longer at version $CURRENT_VERSION." >&2
                echo "Fix: read it again, splice your change into the new body, and retry." >&2
                exit 1
            fi
            api_fail "$API_BODY" "updating page $PAGE_ID"
        fi
        echo "Updated page $PAGE_ID to version $NEXT_VERSION: $TITLE"
        echo "Now verify: confluence-pages.sh read $PAGE_ID | less"
        ;;

    *) usage ;;
esac
