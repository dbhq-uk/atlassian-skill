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
                                 "priority": "High", "parent": "ABC-1",
                                 "fields": {"customfield_10050": "Ops"}}]
        "type" defaults to Task. Every other field is optional. "fields"
        works like create's --field. A rate limit (429) is waited out and
        retried once. Anything not created is written to a remaining
        file (tickets.json gives tickets.remaining.json), so a re-run of
        that file sends only those.

Read:
  get <ISSUE-KEY> [--comments N]
                             Show one issue and its last N comments (default 5)
  search <JQL> [max]         Search with JQL (default 25 results)
  mine [max]                 Open issues assigned to you

Every create prints the issue key and its browse URL. --dry-run prints the
payload and sends nothing.

--description-file takes an HTML+ fragment and formats it through the same
converter Confluence pages use - a real panel, a syntax-highlighted code
block, a table, a status lozenge, instead of a wall of plain text.
--description-file refuses any node Atlassian does not list for Jira (a
decision list, a layout, a block card, a Confluence macro), which Jira may
accept and then render as nothing. It is mutually exclusive with the
plain-text [description]; pass one or the other.

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

# bulk_post <payload> - one create request for bulk. It runs in a subshell,
# so a network failure fails this item rather than ending the run. Sets
# POST_STATUS, POST_RETRY_AFTER and POST_BODY; POST_STATUS is 000 when the
# request did not complete.
bulk_post() {
    local out nl=$'\n' rest
    if out=$(api POST "/rest/api/3/issue" "$1"
             printf '%s\n%s\n%s' "$API_STATUS" "$API_RETRY_AFTER" "$API_BODY"); then
        POST_STATUS="${out%%"$nl"*}"
        rest="${out#*"$nl"}"
        POST_RETRY_AFTER="${rest%%"$nl"*}"
        POST_BODY="${rest#*"$nl"}"
    else
        POST_STATUS="000"; POST_RETRY_AFTER=""; POST_BODY=""
    fi
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
        # Where the entries not created go. A re-run of a remaining file
        # writes back to that same file, so it only ever shrinks.
        case "$FILE" in
            *.remaining.json) REMAINING_FILE="$FILE" ;;
            *.json)           REMAINING_FILE="${FILE%.json}.remaining.json" ;;
            *)                REMAINING_FILE="$FILE.remaining.json" ;;
        esac
        echo "$COUNT issue(s) to create in $PROJECT."
        [ "$DRY" = "1" ] && echo "(dry run - nothing will be sent)"
        echo

        CREATED=0; FAILED=0; NOT_SENT="[]"; STOPPED=""
        for i in $(seq 0 $((COUNT - 1))); do
            # Once a rate limit has not cleared, send nothing more: every
            # further request would be refused too. The rest go to the
            # remaining file untouched.
            if [ -n "$STOPPED" ]; then
                NOT_SENT=$(printf '%s' "$NOT_SENT" | jq -c --argjson i "$i" '. + [$i]')
                FAILED=$((FAILED + 1))
                continue
            fi
            SUMMARY=$(jq -r --argjson i "$i" '.[$i].summary // empty' "$FILE")
            if [ -z "$SUMMARY" ]; then
                echo "[$((i + 1))/$COUNT] skipped: no summary" >&2
                NOT_SENT=$(printf '%s' "$NOT_SENT" | jq -c --argjson i "$i" '. + [$i]')
                FAILED=$((FAILED + 1))
                continue
            fi
            TYPE=$(jq -r --argjson i "$i" '.[$i].type // "Task"' "$FILE")
            DESCRIPTION=$(jq -r --argjson i "$i" '.[$i].description // ""' "$FILE")
            LABELS=$(jq -c --argjson i "$i" '.[$i].labels // []' "$FILE")
            PRIORITY=$(jq -r --argjson i "$i" '.[$i].priority // ""' "$FILE")
            PARENT=$(jq -r --argjson i "$i" '.[$i].parent // ""' "$FILE")
            # "fields" is create's --field, per entry: any field the seven
            # above do not cover, merged into the payload last. It is
            # already JSON, so every value goes as written.
            ENTRY_FIELDS=$(jq -c --argjson i "$i" '.[$i].fields // {}' "$FILE")
            FIELDS_ERROR=$(printf '%s' "$ENTRY_FIELDS" | jq -r '
                if type != "object" then "\"fields\" must be an object, e.g. {\"customfield_10050\": \"Ops\"}."
                else ([keys[] | select(IN("project","issuetype","summary","description","labels","priority","parent"))]
                      | if length > 0 then "\"fields\" cannot set \(join(", ")) - use the entry'"'"'s own key for it instead." else empty end)
                end')
            if [ -n "$FIELDS_ERROR" ]; then
                echo "[$((i + 1))/$COUNT] skipped: $FIELDS_ERROR" >&2
                NOT_SENT=$(printf '%s' "$NOT_SENT" | jq -c --argjson i "$i" '. + [$i]')
                FAILED=$((FAILED + 1))
                continue
            fi
            # bulk takes plain-text description only - no --description-file
            # equivalent field, so this always goes through text_to_adf.
            DESC_ADF="null"
            [ -n "$DESCRIPTION" ] && DESC_ADF=$(text_to_adf "$DESCRIPTION")
            PAYLOAD=$(build_payload "$PROJECT" "$TYPE" "$SUMMARY" "$DESC_ADF" "$LABELS" "$PRIORITY" "$PARENT" "$ENTRY_FIELDS")

            if [ "$DRY" = "1" ]; then
                printf '[%d/%d] %s\n' "$((i + 1))" "$COUNT" "$SUMMARY"
                printf '%s\n' "$PAYLOAD" | jq .
                CREATED=$((CREATED + 1))
                # No request, so nothing to pace: a dry run does not sleep.
                continue
            fi

            printf '[%d/%d] %s ... ' "$((i + 1))" "$COUNT" "$SUMMARY"
            bulk_post "$PAYLOAD"
            if [ "$POST_STATUS" = "429" ]; then
                # Atlassian's guidance: wait for Retry-After (seconds), or
                # back off from 2 seconds when there is none. One retry per
                # item; a wait longer than a minute is not sat through - that
                # is an hourly quota, and the remaining file is the way back.
                WAIT="$POST_RETRY_AFTER"
                case "$WAIT" in ''|*[!0-9]*) WAIT=2 ;; esac
                if [ "$WAIT" -le 60 ]; then
                    printf 'rate limited, waiting %ss ... ' "$WAIT"
                    sleep "$WAIT"
                    bulk_post "$PAYLOAD"
                fi
            fi
            if [ "$POST_STATUS" = "201" ] || [ "$POST_STATUS" = "200" ]; then
                KEY=$(printf '%s' "$POST_BODY" | jq -r '.key')
                echo "$KEY  $SITE/browse/$KEY"
                CREATED=$((CREATED + 1))
            else
                echo "FAILED"
                if [ "$POST_STATUS" = "000" ]; then
                    echo "    the request did not complete - see the error above" >&2
                else
                    # api_fail exits, so it runs in a subshell: it words the
                    # error, and this item fails without ending the run.
                    ( API_STATUS="$POST_STATUS"; API_RETRY_AFTER="$POST_RETRY_AFTER"
                      api_fail "$POST_BODY" "creating the issue" ) 2>&1 | sed 's/^/    /' >&2 || true
                fi
                NOT_SENT=$(printf '%s' "$NOT_SENT" | jq -c --argjson i "$i" '. + [$i]')
                FAILED=$((FAILED + 1))
                if [ "$POST_STATUS" = "429" ]; then
                    STOPPED=1
                    echo "Stopping: the rate limit did not clear, so nothing more is sent." >&2
                fi
            fi
            # A pause between creates, so a long file does not arrive as one
            # burst. Atlassian publishes per-second burst limits and an hourly
            # points quota rather than a per-minute figure:
            # https://developer.atlassian.com/cloud/jira/platform/rate-limiting/
            sleep 1
        done
        echo
        if [ "$DRY" = "1" ]; then
            echo "Would create: $CREATED   Skipped: $FAILED   (dry run - nothing was sent)"
            [ "$FAILED" -eq 0 ] || exit 1
            exit 0
        fi
        echo "Created: $CREATED   Failed: $FAILED"
        if [ "$FAILED" -gt 0 ]; then
            # The entries exactly as they were in the file, in order, so the
            # re-run sends the same thing and never repeats an issue that was
            # created.
            jq --argjson idx "$NOT_SENT" '[. as $all | $idx[] | $all[.]]' "$FILE" > "$REMAINING_FILE.tmp"
            mv "$REMAINING_FILE.tmp" "$REMAINING_FILE"
            echo "The $FAILED not created are in $REMAINING_FILE."
            echo "Fix what failed, then run: jira-issues.sh bulk $PROJECT $REMAINING_FILE"
            exit 1
        fi
        if [ "$REMAINING_FILE" = "$FILE" ]; then
            # Every entry is now created. Emptied rather than deleted, so a
            # second run of it creates nothing twice.
            echo "[]" > "$FILE"
            echo "$FILE is now empty: everything in it was created."
        fi
        ;;

    get)
        KEY="${2:-}"
        [ -n "$KEY" ] || { echo "Usage: jira-issues.sh get <ISSUE-KEY> [--comments N]" >&2; exit 1; }
        SHOW_COMMENTS=5
        if [ "${3:-}" = "--comments" ]; then
            SHOW_COMMENTS="${4:-}"
            case "$SHOW_COMMENTS" in
                ''|*[!0-9]*) echo "Error: --comments needs a number, e.g. --comments 10." >&2; exit 1 ;;
            esac
        elif [ -n "${3:-}" ]; then
            echo "Error: unknown option '$3'. Usage: jira-issues.sh get <ISSUE-KEY> [--comments N]" >&2
            exit 1
        fi
        api GET "/rest/api/3/issue/$KEY?fields=summary,status,issuetype,assignee,reporter,priority,labels,created,updated,description"
        RESPONSE="$API_BODY"
        api_ok || api_fail "$RESPONSE" "fetching $KEY"
        printf '%s' "$RESPONSE" | jq -r --arg site "$SITE" '
            "\(.key)  \(.fields.summary)",
            "URL:      \($site)/browse/\(.key)",
            "Type:     \(.fields.issuetype.name)",
            "Status:   \(.fields.status.name)",
            "Assignee: \(.fields.assignee.displayName // "unassigned")",
            "Priority: \(.fields.priority.name // "none")",
            "Labels:   \(if (.fields.labels | length) > 0 then (.fields.labels | join(", ")) else "none" end)",
            "Updated:  \(.fields.updated)",
            "",
            "Description:"'
        # The description goes through the same converter a Confluence page
        # is read with. A hand-written jq renderer used to stand here, and it
        # knew only text, links and line breaks: a table came out as one run
        # of words, a nested list lost its nesting, and a mention vanished.
        DESC=$(printf '%s' "$RESPONSE" | jq -c '.fields.description // empty')
        if [ -z "$DESC" ]; then
            echo "  (empty)"
        else
            printf '%s' "$DESC" | htmlplus_markdown || echo "  (the description could not be rendered - see the error above)"
            echo
        fi

        # Comments come from their own endpoint, newest first, because the
        # comment list embedded in the issue is not guaranteed to be the
        # latest. Shown oldest to newest, so they read as a conversation.
        [ "$SHOW_COMMENTS" -gt 0 ] || exit 0
        api GET "/rest/api/3/issue/$KEY/comment?orderBy=-created&maxResults=$SHOW_COMMENTS"
        COMMENTS="$API_BODY"
        api_ok || api_fail "$COMMENTS" "fetching the comments on $KEY"
        TOTAL=$(printf '%s' "$COMMENTS" | jq '.total // (.comments | length)')
        SHOWN=$(printf '%s' "$COMMENTS" | jq '.comments | length')
        echo
        if [ "$SHOWN" -eq 0 ]; then
            echo "Comments: none"
            exit 0
        fi
        if [ "$TOTAL" -gt "$SHOWN" ]; then
            echo "Comments (the last $SHOWN of $TOTAL - raise --comments to see more):"
        else
            echo "Comments ($SHOWN):"
        fi
        printf '%s' "$COMMENTS" | jq -c '.comments | reverse | .[]' | while IFS= read -r comment; do
            echo
            printf '%s' "$comment" | jq -r '"-- \(.author.displayName // "unknown"), \(.created // "")"'
            printf '%s' "$comment" | jq -c '.body // {"type": "doc", "version": 1, "content": []}' \
                | htmlplus_markdown || echo "  (this comment could not be rendered - see the error above)"
            echo
        done
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
