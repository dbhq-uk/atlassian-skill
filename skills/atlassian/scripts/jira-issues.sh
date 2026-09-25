#!/bin/bash
# Jira issues - create, read, comment on, and move one issue through its
# workflow. There is deliberately no delete and no bulk transition here: this
# skill cannot destroy work, and a transition moves one issue at a time.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_common.sh"
require_config

usage() {
    cat <<'EOF'
Jira issues (create, read, comment and transition - never delete)

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
  transitions <ISSUE-KEY>    The moves the issue's workflow allows from here

Comment and move:
  comment <ISSUE-KEY> <text> [--dry-run]
  comment <ISSUE-KEY> --body-file FILE [--dry-run]
                             Add a comment. FILE is an HTML+ fragment, held
                             to Jira's node list like --description-file.
  transition <ISSUE-KEY> <target> [--dry-run]
                             Move one issue. <target> is a transition name,
                             the status it leads to, or its id, and must be
                             one the issue's workflow offers now. One issue
                             per call: there is no bulk transition.

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

# require_issue_key <value> - a comment or a transition writes to this issue,
# so anything that is not a key like PAY-12 is refused before a request is
# built. It also keeps a key from carrying a second path or a query into the
# URL.
require_issue_key() {
    if ! [[ "$1" =~ ^[A-Za-z][A-Za-z0-9_]*-[0-9]+$ ]]; then
        echo "Error: '$1' is not an issue key." >&2
        echo "Cause: an issue key is a project key, a hyphen and a number, such as PAY-12." >&2
        echo "Fix: pass one issue key. Find it with: jira-issues.sh search '<JQL>'" >&2
        exit 1
    fi
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

    comment)
        shift
        KEY="${1:-}"; [ $# -gt 0 ] && shift
        TEXT=""; BODY_FILE=""; DRY=0
        if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
            TEXT="$1"; shift
        fi
        while [ $# -gt 0 ]; do
            case "$1" in
                --body-file) [ $# -ge 2 ] || { echo "Error: --body-file needs a file." >&2; exit 1; }
                             BODY_FILE="$2"; shift 2 ;;
                --dry-run)   DRY=1; shift ;;
                *) echo "Error: unknown option '$1'." >&2
                   echo "Usage: jira-issues.sh comment <ISSUE-KEY> <text> | --body-file FILE [--dry-run]" >&2
                   exit 1 ;;
            esac
        done
        require_issue_key "$KEY"
        if [ -n "$TEXT" ] && [ -n "$BODY_FILE" ]; then
            echo "Error: pass the comment as text or with --body-file, not both." >&2
            exit 1
        fi
        if [ -n "$BODY_FILE" ]; then
            [ -f "$BODY_FILE" ] || { echo "Error: $BODY_FILE does not exist." >&2; exit 1; }
            COMMENT_ADF=$(htmlplus_jira "$BODY_FILE")
        elif [ -n "${TEXT//[[:space:]]/}" ]; then
            COMMENT_ADF=$(text_to_adf "$TEXT")
        else
            echo "Error: the comment is empty." >&2
            echo "Usage: jira-issues.sh comment <ISSUE-KEY> <text> | --body-file FILE [--dry-run]" >&2
            exit 1
        fi
        if [ "$(printf '%s' "$COMMENT_ADF" | jq '.content | length')" = "0" ]; then
            echo "Error: the comment is empty. Nothing was sent." >&2
            exit 1
        fi
        PAYLOAD=$(jq -n --argjson b "$COMMENT_ADF" '{body: $b}')
        if [ "$DRY" = "1" ]; then
            printf '%s\n' "$PAYLOAD" | jq .
            echo "Dry run: this comment would go on $KEY. Nothing was sent."
            exit 0
        fi
        api POST "/rest/api/3/issue/$KEY/comment" "$PAYLOAD"
        api_ok || api_fail "$API_BODY" "commenting on $KEY"
        COMMENT_ID=$(printf '%s' "$API_BODY" | jq -r '.id // empty')
        echo "Commented on $KEY: $SITE/browse/$KEY${COMMENT_ID:+?focusedCommentId=$COMMENT_ID}"
        ;;

    transitions)
        KEY="${2:-}"
        require_issue_key "$KEY"
        api GET "/rest/api/3/issue/$KEY/transitions"
        api_ok || api_fail "$API_BODY" "reading the transitions of $KEY"
        if [ "$(printf '%s' "$API_BODY" | jq '.transitions | length')" = "0" ]; then
            echo "$KEY has no transition open to you from where it is now."
            exit 0
        fi
        printf '%s' "$API_BODY" | jq -r '
            "ID\tTRANSITION\tTO STATUS",
            (.transitions[] | "\(.id)\t\(.name)\t\(.to.name // "-")")' | column -t -s $'\t'
        ;;

    transition)
        shift
        KEY="${1:-}"; TARGET="${2:-}"
        [ $# -gt 0 ] && shift
        [ $# -gt 0 ] && shift
        DRY=0
        while [ $# -gt 0 ]; do
            case "$1" in
                --dry-run) DRY=1; shift ;;
                *)
                    echo "Error: unexpected argument '$1'." >&2
                    echo "Cause: transition moves one issue to one target. There is no bulk transition." >&2
                    echo "Usage: jira-issues.sh transition <ISSUE-KEY> <target> [--dry-run]" >&2
                    exit 1
                    ;;
            esac
        done
        require_issue_key "$KEY"
        if [ -z "$TARGET" ] || [ "${TARGET#--}" != "$TARGET" ]; then
            echo "Usage: jira-issues.sh transition <ISSUE-KEY> <target> [--dry-run]" >&2
            echo "See where it can go with: jira-issues.sh transitions $KEY" >&2
            exit 1
        fi
        # Where the issue is now, so the result can say how to move it back.
        api GET "/rest/api/3/issue/$KEY?fields=status"
        api_ok || api_fail "$API_BODY" "reading $KEY"
        FROM=$(printf '%s' "$API_BODY" | jq -r '.fields.status.name // "its current status"')
        # The valid targets come from the issue's live workflow, never from a
        # name assumed to exist. Fields are expanded so a transition that
        # needs input this skill does not collect is refused up front.
        api GET "/rest/api/3/issue/$KEY/transitions?expand=transitions.fields"
        api_ok || api_fail "$API_BODY" "reading the transitions of $KEY"
        TRANSITIONS="$API_BODY"
        MATCHES=$(printf '%s' "$TRANSITIONS" | jq -c --arg t "$TARGET" '
            ($t | ascii_downcase) as $want
            | [.transitions[]
               | select(.id == $t
                        or ((.name // "") | ascii_downcase) == $want
                        or ((.to.name // "") | ascii_downcase) == $want)]')
        OFFERED=$(printf '%s' "$TRANSITIONS" | jq -r '
            [.transitions[] | "\(.name) (id \(.id), to \(.to.name // "-"))"] | join("; ")')
        case "$(printf '%s' "$MATCHES" | jq length)" in
            0)
                echo "Error: $KEY cannot move to \"$TARGET\" from \"$FROM\"." >&2
                echo "Cause: its workflow offers ${OFFERED:-no transition} from there." >&2
                echo "Fix: pick one of those, by name or id. Nothing was sent." >&2
                exit 1
                ;;
            1) ;;
            *)
                echo "Error: \"$TARGET\" matches more than one transition on $KEY." >&2
                echo "Cause: $(printf '%s' "$MATCHES" | jq -r '[.[] | "\(.name) (id \(.id), to \(.to.name // "-"))"] | join("; ")')." >&2
                echo "Fix: pass the id of the one you mean. Nothing was sent." >&2
                exit 1
                ;;
        esac
        TRANSITION_ID=$(printf '%s' "$MATCHES" | jq -r '.[0].id')
        TRANSITION_NAME=$(printf '%s' "$MATCHES" | jq -r '.[0].name')
        TO=$(printf '%s' "$MATCHES" | jq -r '.[0].to.name // .[0].name')
        REQUIRED=$(printf '%s' "$MATCHES" | jq -r '
            .[0].fields // {} | to_entries
            | map(select(.value.required == true and (.value.hasDefaultValue // false) == false))
            | map(.value.name // .key) | join(", ")')
        if [ -n "$REQUIRED" ]; then
            echo "Error: the \"$TRANSITION_NAME\" transition on $KEY needs fields this command does not set: $REQUIRED." >&2
            echo "Fix: make this move in the Jira UI, where its screen asks for them. Nothing was sent." >&2
            exit 1
        fi
        PAYLOAD=$(jq -n --arg id "$TRANSITION_ID" '{transition: {id: $id}}')
        if [ "$DRY" = "1" ]; then
            printf '%s\n' "$PAYLOAD" | jq .
            echo "Dry run: $KEY would move from \"$FROM\" to \"$TO\" (transition \"$TRANSITION_NAME\", id $TRANSITION_ID). Nothing was sent."
            exit 0
        fi
        api POST "/rest/api/3/issue/$KEY/transitions" "$PAYLOAD"
        api_ok || api_fail "$API_BODY" "moving $KEY to $TO"
        echo "Moved $KEY from \"$FROM\" to \"$TO\": $SITE/browse/$KEY"
        echo "To move it back, if the workflow allows: jira-issues.sh transition $KEY \"$FROM\""
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
