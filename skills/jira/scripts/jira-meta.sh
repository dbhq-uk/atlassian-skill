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

case "${1:-}" in
    whoami)
        api GET "/rest/api/3/myself"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "whoami"
        printf '%s' "$RESPONSE" | jq -r '"\(.displayName)  <\(.emailAddress // "hidden")>\naccountId: \(.accountId)\ntimeZone:  \(.timeZone // "unknown")"'
        ;;

    projects)
        SEARCH="${2:-}"
        api GET "/rest/api/3/project/search?maxResults=100&orderBy=key"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "listing projects"
        printf '%s' "$RESPONSE" | jq -r --arg s "$SEARCH" '
            .values[]
            | select($s == "" or ((.key + " " + .name) | ascii_downcase | contains($s | ascii_downcase)))
            | "\(.key)\t\(.name)\t(\(.projectTypeKey // "?"))"' | column -t -s $'\t'
        ;;

    types)
        PROJECT="${2:-}"
        [ -n "$PROJECT" ] || { echo "Usage: jira-meta.sh types <PROJECT>" >&2; exit 1; }
        api GET "/rest/api/3/issue/createmeta/$PROJECT/issuetypes"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "listing issue types for $PROJECT"
        printf '%s' "$RESPONSE" | jq -r '.issueTypes[] | "\(.id)\t\(.name)\t\(if .subtask then "(subtask)" else "" end)"' | column -t -s $'\t'
        ;;

    fields)
        PROJECT="${2:-}"; TYPE="${3:-}"
        [ -n "$PROJECT" ] && [ -n "$TYPE" ] || { echo "Usage: jira-meta.sh fields <PROJECT> <TYPE>" >&2; exit 1; }
        api GET "/rest/api/3/issue/createmeta/$PROJECT/issuetypes"
        TYPES="$API_BODY"
        api_ok || api_fail "$TYPES" "looking up issue types for $PROJECT"
        TYPE_ID=$(printf '%s' "$TYPES" | jq -r --arg t "$TYPE" '.issueTypes[] | select((.name | ascii_downcase) == ($t | ascii_downcase)) | .id' | head -1)
        if [ -z "$TYPE_ID" ]; then
            echo "Error: no issue type '$TYPE' in $PROJECT." >&2
            echo "Fix: pick one of these:" >&2
            printf '%s' "$TYPES" | jq -r '.issueTypes[] | "  " + .name' >&2
            exit 1
        fi
        api GET "/rest/api/3/issue/createmeta/$PROJECT/issuetypes/$TYPE_ID"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "listing fields for $PROJECT/$TYPE"
        echo "Required:"
        printf '%s' "$RESPONSE" | jq -r '.fields[] | select(.required) | "  \(.fieldId)\t\(.name)"' | column -t -s $'\t'
        echo
        echo "Optional:"
        printf '%s' "$RESPONSE" | jq -r '.fields[] | select(.required | not) | "  \(.fieldId)\t\(.name)"' | column -t -s $'\t'
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
