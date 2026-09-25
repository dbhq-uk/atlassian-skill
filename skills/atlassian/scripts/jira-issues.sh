#!/bin/bash
# Jira issues - create and read. There is deliberately no delete and no bulk
# transition here: this skill cannot destroy work.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_common.sh"
require_config

usage() {
    cat <<'EOF'
Jira issues (create and read only)

Usage: jira-issues.sh <command> [args]

Create:
  create <PROJECT> <TYPE> <summary> [description] [--label L]... [--priority P] [--parent KEY] [--description-file FILE] [--field KEY=VALUE]... [--dry-run]
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

--description-file takes an HTML+ fragment and formats it through the same
converter Confluence pages use - a real panel, a syntax-highlighted code
block, real checkboxes, instead of a wall of plain text. --description-file
refuses a Confluence-only component (a status lozenge, a decision list, an
expand, a layout) that Jira would accept and then render as nothing. It is
mutually exclusive with the plain-text [description]; pass one or the other.

--field KEY=VALUE (repeatable) sets any field project/type/summary/
description/labels/priority/parent do not cover - a project-specific
required field, or a component with no default. Read it with
jira-meta.sh fields first, then pass the field id it names, e.g.
--field customfield_10050=Ops or --field components='[{"name":"Backend"}]'.
VALUE that parses as JSON is sent as JSON; anything else is sent as a
plain string. It cannot be used to set the seven fields above - use their
own option instead.
EOF
}

# build_payload <project> <type> <summary> <desc-adf-json> <labels-json> <priority> <parent> [extra-fields-json]
# desc-adf-json is a complete ADF document already built by the caller (via
# text_to_adf or htmlplus_jira), or the literal string "null" - never plain
# text. Kept out of this function so it has no opinion on which converter a
# caller used to get there.
#
# extra-fields-json is a JSON object (default {}) merged into `fields` last -
# create's --field escape hatch for a project's own required custom field or
# a component with no default, which this payload otherwise has no way to
# serve at all. It is built from options already checked against the fixed
# field names above, so there is nothing for it to collide with.
build_payload() {
    local project="$1" type="$2" summary="$3" desc_adf="$4" labels="$5" priority="$6" parent="$7"
    local extra_fields="${8:-}"
    [ -n "$extra_fields" ] || extra_fields="{}"

    jq -n \
        --arg project "$project" \
        --arg type "$type" \
        --arg summary "$summary" \
        --argjson description "${desc_adf:-null}" \
        --argjson labels "${labels:-[]}" \
        --arg priority "$priority" \
        --arg parent "$parent" \
        --argjson extra "$extra_fields" '
        {fields: (
            {project: {key: $project},
             issuetype: {name: $type},
             summary: $summary}
            + (if $description == null then {} else {description: $description} end)
            + (if ($labels | length) == 0 then {} else {labels: $labels} end)
            + (if $priority == "" then {} else {priority: {name: $priority}} end)
            + (if $parent == "" then {} else {parent: {key: $parent}} end)
            + $extra
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
        DESCRIPTION=""; DESCRIPTION_FILE=""; LABELS="[]"; PRIORITY=""; PARENT=""; DRY=0
        EXTRA_FIELDS="{}"
        # An unflagged first remaining argument is the description
        if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
            DESCRIPTION="$1"; shift
        fi
        while [ $# -gt 0 ]; do
            case "$1" in
                --label)             LABELS=$(printf '%s' "$LABELS" | jq --arg l "$2" '. + [$l]'); shift 2 ;;
                --priority)          PRIORITY="$2"; shift 2 ;;
                --parent)            PARENT="$2"; shift 2 ;;
                --description-file)  DESCRIPTION_FILE="$2"; shift 2 ;;
                --field)
                    FIELD_KV="$2"
                    FIELD_KEY="${FIELD_KV%%=*}"
                    if [ "$FIELD_KEY" = "$FIELD_KV" ] || [ -z "$FIELD_KEY" ]; then
                        echo "Error: --field needs key=value (got '$FIELD_KV')." >&2
                        exit 1
                    fi
                    FIELD_VAL="${FIELD_KV#*=}"
                    case "$FIELD_KEY" in
                        project|issuetype|summary|description|labels|priority|parent)
                            echo "Error: --field cannot set '$FIELD_KEY' - use the dedicated option for it instead." >&2
                            exit 1
                            ;;
                    esac
                    # A value that parses as JSON (a number, an object like
                    # {"id":"10000"}, an array, true/false/null) is taken as
                    # JSON - most custom fields want a shape, not a string.
                    # Anything else is taken as a plain string, so an
                    # ordinary --field customfield_10050=Ops still works
                    # without the caller having to quote it as JSON.
                    EXTRA_FIELDS=$(printf '%s' "$EXTRA_FIELDS" | jq --arg k "$FIELD_KEY" --arg v "$FIELD_VAL" \
                        '. + {($k): ($v | try fromjson catch $v)}')
                    shift 2
                    ;;
                --dry-run)           DRY=1; shift ;;
                *) echo "Error: unknown option '$1'." >&2; exit 1 ;;
            esac
        done
        if [ -n "$DESCRIPTION" ] && [ -n "$DESCRIPTION_FILE" ]; then
            echo "Error: --description and --description-file are mutually exclusive." >&2
            echo "Fix: pass plain text as the positional description, or an HTML+ fragment with --description-file, not both." >&2
            exit 1
        fi
        DESC_ADF="null"
        if [ -n "$DESCRIPTION_FILE" ]; then
            [ -f "$DESCRIPTION_FILE" ] || { echo "Error: $DESCRIPTION_FILE does not exist." >&2; exit 1; }
            DESC_ADF=$(htmlplus_jira "$DESCRIPTION_FILE")
        elif [ -n "$DESCRIPTION" ]; then
            DESC_ADF=$(text_to_adf "$DESCRIPTION")
        fi
        create_issue "$(build_payload "$PROJECT" "$TYPE" "$SUMMARY" "$DESC_ADF" "$LABELS" "$PRIORITY" "$PARENT" "$EXTRA_FIELDS")" "$DRY"
        ;;

    bulk)
        shift
        PROJECT="${1:-}"; FILE="${2:-}"; shift 2 2>/dev/null || true
        DRY=0
        # Same loop create uses below: an unrecognised option is rejected
        # rather than silently ignored, which is what let a misspelt
        # --dryrun fall through to a live run.
        while [ $# -gt 0 ]; do
            case "$1" in
                --dry-run) DRY=1; shift ;;
                *)
                    echo "Error: unknown option '$1'." >&2
                    echo "Usage: jira-issues.sh bulk <PROJECT> <file.json> [--dry-run]" >&2
                    exit 1
                    ;;
            esac
        done
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
            # bulk takes plain-text description only - no --description-file
            # equivalent field, so this always goes through text_to_adf.
            DESC_ADF="null"
            [ -n "$DESCRIPTION" ] && DESC_ADF=$(text_to_adf "$DESCRIPTION")

            if [ "$DRY" = "1" ]; then
                printf '[%d/%d] %s\n' "$((i + 1))" "$COUNT" "$SUMMARY"
            else
                printf '[%d/%d] %s ... ' "$((i + 1))" "$COUNT" "$SUMMARY"
            fi
            if RESULT=$(create_issue "$(build_payload "$PROJECT" "$TYPE" "$SUMMARY" "$DESC_ADF" "$LABELS" "$PRIORITY" "$PARENT")" "$DRY" 2>&1); then
                echo "$RESULT"
                CREATED=$((CREATED + 1))
            else
                echo "FAILED"
                echo "$RESULT" | sed 's/^/    /' >&2
                FAILED=$((FAILED + 1))
            fi
            # Stay inside the roughly 60 requests/minute limit - one
            # request a second, not five: 0.2s here used to pace this at
            # 300/minute, five times faster than the limit the docs claimed.
            sleep 1
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
        # /rest/api/3/search/jql is cursor-paged and only the first page is
        # fetched here. It no longer returns a total count at all, so
        # nextPageToken is the only documented signal - and Atlassian's own
        # forums report it can be flaky, so a full page (COUNT == MAX) is
        # treated as a second, weaker signal rather than trusted silence.
        NEXT_TOKEN=$(printf '%s' "$RESPONSE" | jq -r '.nextPageToken // empty')
        if [ -n "$NEXT_TOKEN" ]; then
            echo "More results exist beyond these $COUNT - raise max (currently $MAX) or narrow the JQL."
        elif [ "$COUNT" -eq "$MAX" ]; then
            echo "This is a full page ($COUNT of max $MAX) - there may be more. Raise max or narrow the JQL."
        fi
        ;;

    mine)
        MAX="${2:-25}"
        exec "$0" search "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC" "$MAX"
        ;;

    *)
        usage
        ;;
esac
