#!/bin/bash
# Jira issues - create and read. There is deliberately no delete and no bulk
# transition here: this skill cannot destroy work.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../_shared/scripts/_common.sh
. "$SCRIPT_DIR/../../_shared/scripts/_common.sh"
require_config

usage() {
    cat <<'EOF'
Jira issues (create and read only)

Usage: jira-issues.sh <command> [args]

Create:
  create <PROJECT> <TYPE> <summary> [description] [--label L]... [--priority P] [--parent KEY] [--dry-run]
  bulk <PROJECT> <file.json> [--dry-run]
        file.json is an array: [{"summary": "...", "type": "Task",
                                 "description": "...", "labels": ["a"],
                                 "priority": "High", "parent": "ABC-1"}]
        "type" defaults to Task. Every other field is optional.

Read:
  get <ISSUE-KEY>            Show one issue
  search <JQL> [max]         Search with JQL (default 25 results)
  mine [max]                 Open issues assigned to you

Every create prints the issue key and its browse URL. --dry-run prints the
payload and sends nothing.
EOF
}

# build_payload <project> <type> <summary> <description> <labels-json> <priority> <parent>
build_payload() {
    local project="$1" type="$2" summary="$3" description="$4" labels="$5" priority="$6" parent="$7"
    local desc_adf="null"
    [ -n "$description" ] && desc_adf=$(text_to_adf "$description")

    jq -n \
        --arg project "$project" \
        --arg type "$type" \
        --arg summary "$summary" \
        --argjson description "$desc_adf" \
        --argjson labels "${labels:-[]}" \
        --arg priority "$priority" \
        --arg parent "$parent" '
        {fields: (
            {project: {key: $project},
             issuetype: {name: $type},
             summary: $summary}
            + (if $description == null then {} else {description: $description} end)
            + (if ($labels | length) == 0 then {} else {labels: $labels} end)
            + (if $priority == "" then {} else {priority: {name: $priority}} end)
            + (if $parent == "" then {} else {parent: {key: $parent}} end)
        )}'
}

create_issue() {
    local payload="$1" dry="$2"
    if [ "$dry" = "1" ]; then
        printf '%s\n' "$payload" | jq .
        return 0
    fi
    local response
    api POST "/rest/api/3/issue" "$payload"
    response="$API_BODY"
    api_ok || api_fail "$response" "creating the issue"
    local key
    key=$(printf '%s' "$response" | jq -r '.key')
    echo "$key  $SITE/browse/$key"
}

case "${1:-}" in
    create)
        shift
        PROJECT="${1:-}"; TYPE="${2:-}"; SUMMARY="${3:-}"; shift 3 2>/dev/null || true
        if [ -z "$PROJECT" ] || [ -z "$TYPE" ] || [ -z "$SUMMARY" ]; then
            echo "Usage: jira-issues.sh create <PROJECT> <TYPE> <summary> [description] [options]" >&2
            exit 1
        fi
        DESCRIPTION=""; LABELS="[]"; PRIORITY=""; PARENT=""; DRY=0
        # An unflagged first remaining argument is the description
        if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
            DESCRIPTION="$1"; shift
        fi
        while [ $# -gt 0 ]; do
            case "$1" in
                --label)    LABELS=$(printf '%s' "$LABELS" | jq --arg l "$2" '. + [$l]'); shift 2 ;;
                --priority) PRIORITY="$2"; shift 2 ;;
                --parent)   PARENT="$2"; shift 2 ;;
                --dry-run)  DRY=1; shift ;;
                *) echo "Error: unknown option '$1'." >&2; exit 1 ;;
            esac
        done
        create_issue "$(build_payload "$PROJECT" "$TYPE" "$SUMMARY" "$DESCRIPTION" "$LABELS" "$PRIORITY" "$PARENT")" "$DRY"
        ;;

    bulk)
        PROJECT="${2:-}"; FILE="${3:-}"; DRY=0
        [ "${4:-}" = "--dry-run" ] && DRY=1
        if [ -z "$PROJECT" ] || [ -z "$FILE" ]; then
            echo "Usage: jira-issues.sh bulk <PROJECT> <file.json> [--dry-run]" >&2
            exit 1
        fi
        [ -f "$FILE" ] || { echo "Error: $FILE does not exist." >&2; exit 1; }
        jq -e 'type == "array"' "$FILE" > /dev/null 2>&1 || {
            echo "Error: $FILE must contain a JSON array of issue objects." >&2; exit 1; }

        COUNT=$(jq 'length' "$FILE")
        echo "$COUNT issue(s) to create in $PROJECT."
        [ "$DRY" = "1" ] && echo "(dry run - nothing will be sent)"
        echo

        CREATED=0; FAILED=0
        for i in $(seq 0 $((COUNT - 1))); do
            SUMMARY=$(jq -r --argjson i "$i" '.[$i].summary // empty' "$FILE")
            if [ -z "$SUMMARY" ]; then
                echo "[$((i + 1))/$COUNT] skipped: no summary" >&2
                FAILED=$((FAILED + 1))
                continue
            fi
            TYPE=$(jq -r --argjson i "$i" '.[$i].type // "Task"' "$FILE")
            DESCRIPTION=$(jq -r --argjson i "$i" '.[$i].description // ""' "$FILE")
            LABELS=$(jq -c --argjson i "$i" '.[$i].labels // []' "$FILE")
            PRIORITY=$(jq -r --argjson i "$i" '.[$i].priority // ""' "$FILE")
            PARENT=$(jq -r --argjson i "$i" '.[$i].parent // ""' "$FILE")

            if [ "$DRY" = "1" ]; then
                printf '[%d/%d] %s\n' "$((i + 1))" "$COUNT" "$SUMMARY"
            else
                printf '[%d/%d] %s ... ' "$((i + 1))" "$COUNT" "$SUMMARY"
            fi
            if RESULT=$(create_issue "$(build_payload "$PROJECT" "$TYPE" "$SUMMARY" "$DESCRIPTION" "$LABELS" "$PRIORITY" "$PARENT")" "$DRY" 2>&1); then
                echo "$RESULT"
                CREATED=$((CREATED + 1))
            else
                echo "FAILED"
                echo "$RESULT" | sed 's/^/    /' >&2
                FAILED=$((FAILED + 1))
            fi
            # Stay inside the 60 requests/minute limit
            sleep 0.2
        done
        echo
        if [ "$DRY" = "1" ]; then
            echo "Would create: $CREATED   Skipped: $FAILED   (dry run - nothing was sent)"
        else
            echo "Created: $CREATED   Failed: $FAILED"
        fi
        [ "$FAILED" -eq 0 ] || exit 1
        ;;

    get)
        KEY="${2:-}"
        [ -n "$KEY" ] || { echo "Usage: jira-issues.sh get <ISSUE-KEY>" >&2; exit 1; }
        api GET "/rest/api/3/issue/$KEY?fields=summary,status,issuetype,assignee,reporter,priority,labels,created,updated,description"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "fetching $KEY"
        printf '%s' "$RESPONSE" | jq -r --arg site "$SITE" '
            # ADF nests text arbitrarily deep (a list item holds a paragraph
            # holds the text), so collect it recursively. A one-level map
            # silently renders a bulleted description as empty.
            def nodetext:
                [recurse(.content[]?)
                 | if   .type == "text"       then .text
                   elif .type == "inlineCard" then (.attrs.url // "")
                   elif .type == "hardBreak"  then "\n"
                   else empty end]
                | join("");
            def blocktext:
                if .type == "bulletList" or .type == "orderedList"
                then [.content[]? | "  - " + nodetext] | join("\n")
                else nodetext end;
            "\(.key)  \(.fields.summary)",
            "URL:      \($site)/browse/\(.key)",
            "Type:     \(.fields.issuetype.name)",
            "Status:   \(.fields.status.name)",
            "Assignee: \(.fields.assignee.displayName // "unassigned")",
            "Priority: \(.fields.priority.name // "none")",
            "Labels:   \(if (.fields.labels | length) > 0 then (.fields.labels | join(", ")) else "none" end)",
            "Updated:  \(.fields.updated)",
            "",
            "Description:",
            ((.fields.description.content // []) | map(blocktext) | map(select(length > 0)) | join("\n") | if . == "" then "  (empty)" else . end)'
        ;;

    search)
        JQL="${2:-}"; MAX="${3:-25}"
        [ -n "$JQL" ] || { echo "Usage: jira-issues.sh search <JQL> [max]" >&2; exit 1; }
        BODY=$(jq -n --arg jql "$JQL" --argjson max "$MAX" \
            '{jql: $jql, maxResults: $max, fields: ["summary", "status", "issuetype", "assignee"]}')
        api POST "/rest/api/3/search/jql" "$BODY"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "searching"
        COUNT=$(printf '%s' "$RESPONSE" | jq '.issues | length')
        if [ "$COUNT" = "0" ]; then
            echo "No issues match that JQL."
            exit 0
        fi
        printf '%s' "$RESPONSE" | jq -r '.issues[] | "\(.key)\t\(.fields.status.name)\t\(.fields.issuetype.name)\t\(.fields.summary)"' | column -t -s $'\t'
        echo
        echo "$COUNT issue(s) shown."
        ;;

    mine)
        MAX="${2:-25}"
        exec "$0" search "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC" "$MAX"
        ;;

    *)
        usage
        ;;
esac
