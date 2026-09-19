#!/bin/bash
# Jira metadata - projects, issue types, and the fields a create call requires.
# Read-only. Run these before creating an issue so the payload is right first time.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../_shared/scripts/_common.sh
. "$SCRIPT_DIR/../../_shared/scripts/_common.sh"
require_config

usage() {
    cat <<'EOF'
Jira metadata (read-only)

Usage: jira-meta.sh <command> [args]

Commands:
  whoami                     Show the authenticated account
  projects [search]          List projects you can see, optionally filtered
  types <PROJECT>            List issue type names and ids for a project
  fields <PROJECT> <TYPE>    List the fields a create call accepts, marking the required ones
  priorities                 List priority names valid on this site
EOF
}

# fetch_all_issue_types <project> - every issue type across every page,
# merged into one JSON array on stdout. Cloud v3's issuetypes createmeta
# response carries startAt/maxResults/total but, unlike project/search, no
# isLast - so completion is judged by "have we consumed total yet",
# with a hard page cap as a backstop against a response shape that never
# satisfies that (a stalled or missing total must not spin forever).
fetch_all_issue_types() {
    local project="$1" start=0 pages=0 all="[]"
    local response page_types count total next_start
    while :; do
        api GET "/rest/api/3/issue/createmeta/$project/issuetypes?startAt=$start"
        response="$API_BODY"
        api_ok || api_fail "$response" "listing issue types for $project"
        page_types=$(printf '%s' "$response" | jq '.issueTypes')
        count=$(printf '%s' "$page_types" | jq 'length')
        all=$(jq -n --argjson a "$all" --argjson b "$page_types" '$a + $b')
        total=$(printf '%s' "$response" | jq -r '.total // empty')
        next_start=$((start + count))
        pages=$((pages + 1))
        # Stop on: an empty page, 50 pages fetched (5000 issue types - a
        # backstop, not a real limit), or - only when the API told us a
        # total - having consumed it.
        if [ "$count" -eq 0 ] || [ "$pages" -ge 50 ]; then
            break
        fi
        if [ -n "$total" ] && [ "$next_start" -ge "$total" ]; then
            break
        fi
        start="$next_start"
    done
    printf '%s' "$all"
}

case "${1:-}" in
    whoami)
        api GET "/rest/api/3/myself"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "whoami"
        printf '%s' "$RESPONSE" | jq -r '"\(.displayName)  <\(.emailAddress // "hidden")>\naccountId: \(.accountId)\ntimeZone:  \(.timeZone // "unknown")"'
        ;;

    projects)
        SEARCH="${2:-}"
        # Every page, not just the first 100 - project/search's own
        # isLast says when to stop, with the same empty-page and page-count
        # backstops fetch_all_issue_types uses, in case a future response
        # ever leaves isLast out. A project living past the first page used
        # to be invisible to this command and to the search below it,
        # which directly undermines "discover the project before creating".
        ALL="[]"
        START=0
        PAGES=0
        while :; do
            api GET "/rest/api/3/project/search?maxResults=100&startAt=$START&orderBy=key"
            RESPONSE="$API_BODY"
            api_ok || api_fail "$RESPONSE" "listing projects"
            PAGE_VALUES=$(printf '%s' "$RESPONSE" | jq '.values')
            COUNT=$(printf '%s' "$PAGE_VALUES" | jq 'length')
            ALL=$(jq -n --argjson a "$ALL" --argjson b "$PAGE_VALUES" '$a + $b')
            IS_LAST=$(printf '%s' "$RESPONSE" | jq -r '.isLast // false')
            NEXT_START=$((START + COUNT))
            PAGES=$((PAGES + 1))
            if [ "$IS_LAST" = "true" ] || [ "$COUNT" -eq 0 ] || [ "$PAGES" -ge 50 ]; then
                break
            fi
            START="$NEXT_START"
        done
        printf '%s' "$ALL" | jq -r --arg s "$SEARCH" '
            .[]
            | select($s == "" or ((.key + " " + .name) | ascii_downcase | contains($s | ascii_downcase)))
            | "\(.key)\t\(.name)\t(\(.projectTypeKey // "?"))"' | column -t -s $'\t'
        ;;

    types)
        PROJECT="${2:-}"
        [ -n "$PROJECT" ] || { echo "Usage: jira-meta.sh types <PROJECT>" >&2; exit 1; }
        fetch_all_issue_types "$PROJECT" | jq -r '.[] | "\(.id)\t\(.name)\t\(if .subtask then "(subtask)" else "" end)"' | column -t -s $'\t'
        ;;

    fields)
        PROJECT="${2:-}"; TYPE="${3:-}"
        [ -n "$PROJECT" ] && [ -n "$TYPE" ] || { echo "Usage: jira-meta.sh fields <PROJECT> <TYPE>" >&2; exit 1; }
        TYPES=$(fetch_all_issue_types "$PROJECT")
        TYPE_ID=$(printf '%s' "$TYPES" | jq -r --arg t "$TYPE" '.[] | select((.name | ascii_downcase) == ($t | ascii_downcase)) | .id' | head -1)
        if [ -z "$TYPE_ID" ]; then
            echo "Error: no issue type '$TYPE' in $PROJECT." >&2
            echo "Fix: pick one of these:" >&2
            printf '%s' "$TYPES" | jq -r '.[] | "  " + .name' >&2
            exit 1
        fi
        # This page does carry isLast (Cloud's PageBeanFieldMetadata), so
        # that is the stop condition; the empty-page and page-count checks
        # stay as the same backstop the two loops above use. A required
        # field living past the first page used to be invisible here, which
        # is exactly the gap that leaves a create call missing a field the
        # project actually requires.
        ALL_FIELDS="[]"
        START=0
        PAGES=0
        while :; do
            api GET "/rest/api/3/issue/createmeta/$PROJECT/issuetypes/$TYPE_ID?startAt=$START"
            RESPONSE="$API_BODY"
            api_ok || api_fail "$RESPONSE" "listing fields for $PROJECT/$TYPE"
            PAGE_FIELDS=$(printf '%s' "$RESPONSE" | jq '.fields')
            COUNT=$(printf '%s' "$PAGE_FIELDS" | jq 'length')
            ALL_FIELDS=$(jq -n --argjson a "$ALL_FIELDS" --argjson b "$PAGE_FIELDS" '$a + $b')
            IS_LAST=$(printf '%s' "$RESPONSE" | jq -r '.isLast // false')
            PAGES=$((PAGES + 1))
            if [ "$IS_LAST" = "true" ] || [ "$COUNT" -eq 0 ] || [ "$PAGES" -ge 50 ]; then
                break
            fi
            START=$((START + COUNT))
        done
        echo "Required:"
        printf '%s' "$ALL_FIELDS" | jq -r '.[] | select(.required) | "  \(.fieldId)\t\(.name)"' | column -t -s $'\t'
        echo
        echo "Optional:"
        printf '%s' "$ALL_FIELDS" | jq -r '.[] | select(.required | not) | "  \(.fieldId)\t\(.name)"' | column -t -s $'\t'
        ;;

    priorities)
        api GET "/rest/api/3/priority"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "listing priorities"
        printf '%s' "$RESPONSE" | jq -r '.[] | "\(.id)\t\(.name)"' | column -t -s $'\t'
        ;;

    *)
        usage
        ;;
esac
