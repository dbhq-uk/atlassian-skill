#!/bin/bash
# Confluence comments - list, read, add, reply to, update, resolve and delete
# the comments on a page, footer and inline alike.
#
# This file holds the only Confluence delete in the skill, and it is narrow:
# a comment the credential's own account wrote, with no replies under it.
# Confluence's own delete is permanent and cannot be reverted, and a delete
# of a comment with replies would take other people's words with it. A
# page, an attachment or a space is never deleted.
#
# Bodies are plain text or a Confluence HTML+ fragment, converted to ADF
# locally by htmlplus.py, the same as a page.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_common.sh"
# shellcheck source=_confluence.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_confluence.sh"
HTMLPLUS="$SCRIPT_DIR/htmlplus.py"
ADF_EDIT="$SCRIPT_DIR/adf_edit.py"

usage() {
    cat >&2 <<'USAGE'
Usage:
  confluence-comments.sh list    <page-id> [--limit <n>]
  confluence-comments.sh read    <comment-id> [--format html|markdown|adf]
  confluence-comments.sh create  <page-id> (<text> | --body-file <file.html>) \
                                 [--inline <text on the page> [--match <n>]] [--dry-run]
  confluence-comments.sh reply   <comment-id> (<text> | --body-file <file.html>) [--dry-run]
  confluence-comments.sh update  <comment-id> (<text> | --body-file <file.html>) \
                                 --base-version <n> [--message <version message>] [--dry-run]
  confluence-comments.sh resolve <comment-id> [--dry-run]
  confluence-comments.sh reopen  <comment-id> [--dry-run]
  confluence-comments.sh delete  <comment-id> [--dry-run]

A comment id works for footer and inline comments alike; the script finds
which kind it is. list prints every id.

create without --inline adds a footer comment, at the foot of the page.
--inline anchors the comment to a piece of text on the page. If that text
appears more than once, --match picks which one, counting from 1.

update and delete act only on a comment your own account wrote. delete also
refuses a comment with replies. Confluence's delete is permanent.
USAGE
    exit 1
}

# require_numeric_id <value> <what> - a page or comment id is always numeric.
# Anything else is refused before a request is built, which also keeps a
# second path or a query out of the URL.
require_numeric_id() {
    case "$1" in
        ''|*[!0-9]*)
            echo "Error: '$1' is not a valid $2 id." >&2
            echo "Cause: a Confluence $2 id is always numeric." >&2
            echo "Fix: pass the numeric id, e.g. 1234567. confluence-comments.sh list <page-id> prints every comment id." >&2
            exit 1
            ;;
    esac
}

# comment_value <text> <body-file> - the comment as the JSON string the v2
# API wants in body.value: ADF, serialised, inside a string.
comment_value() {
    local text="$1" file="$2" adf
    if [ -n "$text" ] && [ -n "$file" ]; then
        echo "Error: pass the comment as text or with --body-file, not both." >&2
        exit 1
    fi
    if [ -n "$file" ]; then
        [ -f "$file" ] || { echo "Error: no such file: $file" >&2; exit 1; }
        adf=$(python3 "$HTMLPLUS" to-adf < "$file") || {
            echo "Fix: correct the body and try again. Nothing was sent." >&2
            exit 1
        }
    elif [ -n "${text//[[:space:]]/}" ]; then
        adf=$(text_to_adf "$text")
    else
        echo "Error: the comment is empty. Nothing was sent." >&2
        exit 1
    fi
    if [ "$(printf '%s' "$adf" | jq '.content | length')" = "0" ]; then
        echo "Error: the comment is empty. Nothing was sent." >&2
        exit 1
    fi
    printf '%s' "$adf" | jq -c . | jq -Rs .
}

# fetch_comment <comment-id> - read one comment as it is now. Sets
# COMMENT_KIND (the API collection, footer-comments or inline-comments),
# COMMENT_LABEL (footer or inline), COMMENT_JSON, COMMENT_VERSION,
# COMMENT_PAGE and COMMENT_BODY (the ADF, as JSON text).
#
# The v2 API keeps the two kinds in separate collections, and an id from one
# answers 404 in the other, so a 404 moves on to the next.
fetch_comment() {
    local id="$1" kind
    for kind in footer-comments inline-comments; do
        api GET "/wiki/api/v2/$kind/$id?body-format=atlas_doc_format"
        if api_ok; then
            COMMENT_KIND="$kind"
            COMMENT_LABEL="${kind%%-*}"
            COMMENT_JSON="$API_BODY"
            COMMENT_VERSION=$(printf '%s' "$API_BODY" | jq -r '.version.number')
            COMMENT_PAGE=$(printf '%s' "$API_BODY" | jq -r '.pageId // .blogPostId // "unknown"')
            COMMENT_BODY=$(printf '%s' "$API_BODY" | jq -r '.body.atlas_doc_format.value // empty')
            [ -n "$COMMENT_BODY" ] || COMMENT_BODY='{"type":"doc","version":1,"content":[]}'
            case "$COMMENT_VERSION" in
                ''|*[!0-9]*)
                    echo "Error: could not read a version number for comment $id." >&2
                    echo "Cause: the API response had version.number = ${COMMENT_VERSION:-<empty>}." >&2
                    echo "Fix: check the comment id and try again." >&2
                    exit 1
                    ;;
            esac
            return 0
        fi
        [ "$API_STATUS" = "404" ] || api_fail "$API_BODY" "reading comment $id"
    done
    echo "Error: there is no comment $id visible to you." >&2
    echo "Cause: neither the footer nor the inline comments hold that id (HTTP 404)." >&2
    echo "Fix: list the page's comments with: confluence-comments.sh list <page-id>" >&2
    exit 1
}

# my_account_id - the account id the credential authenticates as. It is the
# same Atlassian account on Jira and Confluence, and /rest/api/3/myself is
# the call setup already relies on.
my_account_id() {
    api GET "/rest/api/3/myself"
    api_ok || api_fail "$API_BODY" "reading who the token authenticates as"
    printf '%s' "$API_BODY" | jq -r '.accountId // empty'
}

# require_own <verb> - refuse unless the credential's account wrote the
# comment fetch_comment last read. The writer is the author of version 1:
# the latest version's author is whoever edited it last.
require_own() {
    local verb="$1" me creator
    me=$(my_account_id)
    api GET "/wiki/api/v2/$COMMENT_KIND/$COMMENT_ID/versions/1"
    api_ok || api_fail "$API_BODY" "reading who wrote comment $COMMENT_ID"
    creator=$(printf '%s' "$API_BODY" | jq -r '.authorId // empty')
    if [ -z "$me" ] || [ "$creator" != "$me" ]; then
        echo "Error: comment $COMMENT_ID was written by someone else." >&2
        echo "Cause: this skill will only $verb a comment your own account wrote. Its author is ${creator:-unknown}; you are ${me:-unknown}." >&2
        echo "Fix: nothing was sent. Reply to it instead, or ask its author. A space admin can $verb it in the Confluence UI." >&2
        exit 1
    fi
}

# comment_link <response> <page-id> <comment-id> - the browser link to a
# comment. The response's own webui link when it has one.
comment_link() {
    local link
    link=$(printf '%s' "$1" | jq -r 'if ._links.webui then "\(._links.base // "")\(._links.webui)" else empty end')
    if [ -n "$link" ] && [ "${link#/}" = "$link" ]; then
        printf '%s' "$link"
    else
        printf '%s/wiki/pages/viewpage.action?pageId=%s&focusedCommentId=%s' "$SITE" "$2" "$3"
    fi
}

# print_comment <json> <label> <depth> <me> - one comment and, beneath it,
# every reply, indented by depth.
print_comment() {
    local json="$1" label="$2" depth="$3" me="$4" pad id author replies
    pad=$(printf '%*s' $((depth * 4)) '')
    id=$(printf '%s' "$json" | jq -r '.id')
    author=$(printf '%s' "$json" | jq -r '.version.authorId // "unknown"')
    [ "$author" != "$me" ] || author="$author (you)"
    echo
    printf '%s' "$json" | jq -r --arg pad "$pad" --arg label "$label" --arg author "$author" '
        "\($pad)-- \($label) comment \(.id), version \(.version.number), \($author), \(.version.createdAt // "")"
        + (if .resolutionStatus and $label == "inline" then ", \(.resolutionStatus)" else "" end),
        (if .properties.inlineOriginalSelection then "\($pad)   on: \"\(.properties.inlineOriginalSelection)\"" else empty end)'
    printf '%s' "$json" | jq -r '.body.atlas_doc_format.value // "{\"type\":\"doc\",\"version\":1,\"content\":[]}"' \
        | python3 "$HTMLPLUS" to-markdown | sed "s/^/$pad   /" \
        || echo "$pad   (this comment could not be rendered - see the error above)"
    echo

    api GET "/wiki/api/v2/$label-comments/$id/children?body-format=atlas_doc_format&sort=created-date&limit=$LIMIT"
    api_ok || api_fail "$API_BODY" "reading the replies to comment $id"
    replies="$API_BODY"
    if printf '%s' "$replies" | jq -e '._links.next // empty' > /dev/null; then
        echo "$pad   (only the first $LIMIT replies are shown - raise --limit to see more)"
    fi
    local reply
    local -a list
    mapfile -t list < <(printf '%s' "$replies" | jq -c '.results[]')
    for reply in "${list[@]}"; do
        print_comment "$reply" "$label" $((depth + 1)) "$me"
    done
}

# parse_body_args "$@" - the text or --body-file, --dry-run, --message and
# --base-version shared by create, reply and update. Sets TEXT, BODY_FILE,
# DRY, MESSAGE, BASE_VERSION, INLINE and MATCH.
parse_body_args() {
    TEXT=""; BODY_FILE=""; DRY=0; MESSAGE=""; BASE_VERSION=""; INLINE=""; MATCH=""
    if [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; then
        TEXT="$1"; shift
    fi
    while [ $# -gt 0 ]; do
        case "$1" in
            --body-file)    [ $# -ge 2 ] || usage; BODY_FILE="$2";    shift 2 ;;
            --message)      [ $# -ge 2 ] || usage; MESSAGE="$2";      shift 2 ;;
            --base-version) [ $# -ge 2 ] || usage; BASE_VERSION="$2"; shift 2 ;;
            --inline)       [ $# -ge 2 ] || usage; INLINE="$2";       shift 2 ;;
            --match)        [ $# -ge 2 ] || usage; MATCH="$2";        shift 2 ;;
            --dry-run)      DRY=1; shift ;;
            *) echo "Error: unknown option '$1'." >&2; usage ;;
        esac
    done
}

# post_comment <collection> <payload> <page-id> <what> - send a new comment
# and print its id and link, or print the payload on a dry run.
post_comment() {
    local kind="$1" payload="$2" page="$3" what="$4" new_id
    if [ "$DRY" = "1" ]; then
        printf '%s\n' "$payload" | jq '.body.value |= fromjson'
        echo "Dry run: $what. Nothing was sent."
        exit 0
    fi
    api POST "/wiki/api/v2/$kind" "$payload"
    api_ok || api_fail "$API_BODY" "adding the comment"
    new_id=$(printf '%s' "$API_BODY" | jq -r '.id')
    echo "Added ${kind%%-*} comment $new_id: $what."
    echo "URL: $(comment_link "$API_BODY" "$page" "$new_id")"
}

CMD="${1:-}"; shift || usage

case "$CMD" in
    list)
        PAGE_ID="${1:-}"; shift || true
        require_numeric_id "$PAGE_ID" "page"
        LIMIT=50
        while [ $# -gt 0 ]; do
            case "$1" in
                --limit) [ $# -ge 2 ] || usage; LIMIT="$2"; shift 2 ;;
                *) usage ;;
            esac
        done
        case "$LIMIT" in
            ''|*[!0-9]*|0) echo "Error: --limit needs a number from 1 to 250." >&2; exit 1 ;;
        esac
        [ "$LIMIT" -le 250 ] || { echo "Error: --limit needs a number from 1 to 250." >&2; exit 1; }
        require_config
        ME=$(my_account_id)
        TOTAL=0
        for LABEL in footer inline; do
            api GET "/wiki/api/v2/pages/$PAGE_ID/$LABEL-comments?body-format=atlas_doc_format&sort=created-date&limit=$LIMIT"
            api_ok || api_fail "$API_BODY" "listing the $LABEL comments on page $PAGE_ID"
            ROOTS="$API_BODY"
            COUNT=$(printf '%s' "$ROOTS" | jq '.results | length')
            TOTAL=$((TOTAL + COUNT))
            [ "$COUNT" -gt 0 ] || continue
            echo
            echo "${LABEL^} comments ($COUNT thread(s)):"
            mapfile -t THREADS < <(printf '%s' "$ROOTS" | jq -c '.results[]')
            for THREAD in "${THREADS[@]}"; do
                print_comment "$THREAD" "$LABEL" 0 "$ME"
            done
            if printf '%s' "$ROOTS" | jq -e '._links.next // empty' > /dev/null; then
                echo
                echo "More $LABEL comments exist beyond these $COUNT - raise --limit (currently $LIMIT)."
            fi
        done
        [ "$TOTAL" -gt 0 ] || echo "Page $PAGE_ID has no comments."
        ;;

    read)
        COMMENT_ID="${1:-}"; shift || true
        require_numeric_id "$COMMENT_ID" "comment"
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
        fetch_comment "$COMMENT_ID"
        # The header goes to stderr, as it does for a page, so
        # `read N > /tmp/comment.html` captures a clean fragment.
        printf '%s' "$COMMENT_JSON" | jq -r --arg label "$COMMENT_LABEL" '
            "# \($label) comment \(.id) on page \(.pageId // .blogPostId // "unknown"), by \(.version.authorId // "unknown")"
            + (if .parentCommentId then ", a reply to \(.parentCommentId)" else "" end)
            + (if $label == "inline" and .resolutionStatus then ", \(.resolutionStatus)" else "" end),
            (if .properties.inlineOriginalSelection then "# on: \"\(.properties.inlineOriginalSelection)\"" else empty end),
            "# version \(.version.number) - pass --base-version \(.version.number) to update"' >&2
        echo >&2
        case "$FORMAT" in
            adf)      printf '%s' "$COMMENT_BODY" | jq . ;;
            markdown) printf '%s' "$COMMENT_BODY" | python3 "$HTMLPLUS" to-markdown ;;
            html)     printf '%s' "$COMMENT_BODY" | python3 "$HTMLPLUS" to-html ;;
        esac
        echo >&2
        ;;

    create)
        PAGE_ID="${1:-}"; shift || true
        require_numeric_id "$PAGE_ID" "page"
        parse_body_args "$@"
        [ -z "$BASE_VERSION" ] && [ -z "$MESSAGE" ] || { echo "Error: create takes no --base-version or --message." >&2; exit 1; }
        [ -n "$INLINE" ] || [ -z "$MATCH" ] || { echo "Error: --match only goes with --inline." >&2; exit 1; }
        VALUE=$(comment_value "$TEXT" "$BODY_FILE")
        require_config
        if [ -z "$INLINE" ]; then
            PAYLOAD=$(jq -n --arg p "$PAGE_ID" --argjson v "$VALUE" '
                {pageId: $p, body: {representation: "atlas_doc_format", value: $v}}')
            post_comment footer-comments "$PAYLOAD" "$PAGE_ID" "a footer comment on page $PAGE_ID"
            exit 0
        fi
        # An inline comment names the text it highlights, how many times
        # that text is on the page, and which of them it means. The count is
        # read from the live page rather than trusted from the caller.
        fetch_page "$PAGE_ID"
        PAGE_FILE=$(mktemp)
        printf '%s' "$CURRENT_BODY" > "$PAGE_FILE"
        FOUND=$(python3 "$ADF_EDIT" count-text "$PAGE_FILE" "$INLINE") || { rm -f "$PAGE_FILE"; exit 1; }
        rm -f "$PAGE_FILE"
        if [ "$FOUND" = "0" ]; then
            echo "Error: the text \"$INLINE\" is not on page $PAGE_ID." >&2
            echo "Cause: an inline comment highlights text inside one paragraph, heading or cell, and this text is not in any of them. A run broken by a mention, a status or a date does not match." >&2
            echo "Fix: copy the text exactly from the page (confluence-pages.sh read $PAGE_ID --format markdown), or add a footer comment instead. Nothing was sent." >&2
            exit 1
        fi
        if [ -z "$MATCH" ]; then
            if [ "$FOUND" != "1" ]; then
                echo "Error: the text \"$INLINE\" is on page $PAGE_ID $FOUND times." >&2
                echo "Fix: pass --match <n> to pick one, counting from 1 in page order, or quote more of the text. Nothing was sent." >&2
                exit 1
            fi
            MATCH=1
        fi
        case "$MATCH" in
            ''|*[!0-9]*|0) echo "Error: --match needs a number from 1 to $FOUND." >&2; exit 1 ;;
        esac
        [ "$MATCH" -le "$FOUND" ] || { echo "Error: --match $MATCH is past the last match; the text is on the page $FOUND times. Nothing was sent." >&2; exit 1; }
        PAYLOAD=$(jq -n --arg p "$PAGE_ID" --argjson v "$VALUE" --arg sel "$INLINE" \
                        --argjson count "$FOUND" --argjson index "$((MATCH - 1))" '
            {pageId: $p, body: {representation: "atlas_doc_format", value: $v},
             inlineCommentProperties: {textSelection: $sel,
                                       textSelectionMatchCount: $count,
                                       textSelectionMatchIndex: $index}}')
        post_comment inline-comments "$PAYLOAD" "$PAGE_ID" \
            "an inline comment on page $PAGE_ID, on match $MATCH of $FOUND of \"$INLINE\""
        ;;

    reply)
        COMMENT_ID="${1:-}"; shift || true
        require_numeric_id "$COMMENT_ID" "comment"
        parse_body_args "$@"
        [ -z "$BASE_VERSION" ] && [ -z "$MESSAGE" ] && [ -z "$INLINE" ] && [ -z "$MATCH" ] \
            || { echo "Error: reply takes only the text or --body-file, and --dry-run." >&2; exit 1; }
        VALUE=$(comment_value "$TEXT" "$BODY_FILE")
        require_config
        fetch_comment "$COMMENT_ID"
        PAYLOAD=$(jq -n --arg c "$COMMENT_ID" --argjson v "$VALUE" '
            {parentCommentId: $c, body: {representation: "atlas_doc_format", value: $v}}')
        post_comment "$COMMENT_KIND" "$PAYLOAD" "$COMMENT_PAGE" \
            "a reply to $COMMENT_LABEL comment $COMMENT_ID on page $COMMENT_PAGE"
        ;;

    update)
        COMMENT_ID="${1:-}"; shift || true
        require_numeric_id "$COMMENT_ID" "comment"
        parse_body_args "$@"
        [ -z "$INLINE" ] && [ -z "$MATCH" ] || { echo "Error: update cannot move an inline comment's highlight." >&2; exit 1; }
        if [ -z "$BASE_VERSION" ]; then
            echo "Error: update needs --base-version <n>." >&2
            echo "Cause: it is the version your change was written against, and it is what lets update refuse to overwrite an edit that landed since." >&2
            echo "Fix: read the comment (confluence-comments.sh read $COMMENT_ID), note the version in the header, and pass it." >&2
            exit 1
        fi
        case "$BASE_VERSION" in
            *[!0-9]*) echo "Error: --base-version must be a number." >&2; exit 1 ;;
        esac
        VALUE=$(comment_value "$TEXT" "$BODY_FILE")
        require_config
        fetch_comment "$COMMENT_ID"
        if [ "$BASE_VERSION" != "$COMMENT_VERSION" ]; then
            echo "Error: comment $COMMENT_ID has moved on since your base version." >&2
            echo "Cause: --base-version was $BASE_VERSION; the comment is now at version $COMMENT_VERSION." >&2
            echo "Fix: read it again, make your change against what comes back, and pass --base-version $COMMENT_VERSION." >&2
            exit 1
        fi
        require_own "update"
        PAYLOAD=$(jq -n --argjson n "$((COMMENT_VERSION + 1))" --arg m "$MESSAGE" --argjson v "$VALUE" '
            {version: ({number: $n} + (if $m == "" then {} else {message: $m} end)),
             body: {representation: "atlas_doc_format", value: $v}}')
        if [ "$DRY" = "1" ]; then
            printf '%s\n' "$PAYLOAD" | jq '.body.value |= fromjson'
            echo "Dry run: $COMMENT_LABEL comment $COMMENT_ID would go to version $((COMMENT_VERSION + 1)). Nothing was sent."
            exit 0
        fi
        api PUT "/wiki/api/v2/$COMMENT_KIND/$COMMENT_ID" "$PAYLOAD"
        if ! api_ok; then
            if [ "$API_STATUS" = "409" ]; then
                echo "Error: comment $COMMENT_ID changed while you were working on it (HTTP 409)." >&2
                echo "Fix: do not re-run this command. Read the comment again and pass its new --base-version." >&2
                exit 1
            fi
            api_fail "$API_BODY" "updating comment $COMMENT_ID"
        fi
        echo "Updated $COMMENT_LABEL comment $COMMENT_ID to version $((COMMENT_VERSION + 1))."
        echo "URL: $(comment_link "$API_BODY" "$COMMENT_PAGE" "$COMMENT_ID")"
        ;;

    resolve|reopen)
        COMMENT_ID="${1:-}"; shift || true
        require_numeric_id "$COMMENT_ID" "comment"
        DRY=0
        while [ $# -gt 0 ]; do
            case "$1" in
                --dry-run) DRY=1; shift ;;
                *) usage ;;
            esac
        done
        require_config
        fetch_comment "$COMMENT_ID"
        if [ "$COMMENT_LABEL" != "inline" ]; then
            echo "Error: comment $COMMENT_ID is a footer comment." >&2
            echo "Cause: only an inline comment can be resolved or reopened." >&2
            exit 1
        fi
        [ "$CMD" = "resolve" ] && RESOLVED=true || RESOLVED=false
        STATE=$(printf '%s' "$COMMENT_JSON" | jq -r '.resolutionStatus // "unknown"')
        # The body goes back exactly as it was read, as a string, so the
        # new version changes nothing but the resolution.
        PAYLOAD=$(jq -n --argjson n "$((COMMENT_VERSION + 1))" --argjson r "$RESOLVED" \
                        --arg v "$COMMENT_BODY" '
            {version: {number: $n}, resolved: $r,
             body: {representation: "atlas_doc_format", value: $v}}')
        if [ "$DRY" = "1" ]; then
            echo "Dry run: inline comment $COMMENT_ID is $STATE; it would be ${CMD}d. Nothing was sent."
            exit 0
        fi
        api PUT "/wiki/api/v2/inline-comments/$COMMENT_ID" "$PAYLOAD"
        api_ok || api_fail "$API_BODY" "marking comment $COMMENT_ID ${CMD}d"
        echo "Inline comment $COMMENT_ID was $STATE and is now ${CMD}d."
        if [ "$CMD" = "resolve" ]; then
            echo "To undo: confluence-comments.sh reopen $COMMENT_ID"
        fi
        ;;

    delete)
        COMMENT_ID="${1:-}"; shift || true
        require_numeric_id "$COMMENT_ID" "comment"
        DRY=0
        while [ $# -gt 0 ]; do
            case "$1" in
                --dry-run) DRY=1; shift ;;
                *) usage ;;
            esac
        done
        require_config
        fetch_comment "$COMMENT_ID"
        require_own "delete"
        api GET "/wiki/api/v2/$COMMENT_KIND/$COMMENT_ID/children?limit=1"
        api_ok || api_fail "$API_BODY" "reading the replies to comment $COMMENT_ID"
        if [ "$(printf '%s' "$API_BODY" | jq '.results | length')" != "0" ]; then
            echo "Error: comment $COMMENT_ID has replies." >&2
            echo "Cause: deleting it would delete the replies too, and those are other people's words." >&2
            echo "Fix: nothing was sent. Edit it with update instead, or delete it in the Confluence UI." >&2
            exit 1
        fi
        echo "$COMMENT_LABEL comment $COMMENT_ID on page $COMMENT_PAGE, version $COMMENT_VERSION:"
        printf '%s' "$COMMENT_BODY" | python3 "$HTMLPLUS" to-markdown | sed 's/^/    /' || true
        echo
        if [ "$DRY" = "1" ]; then
            echo "Dry run: this comment would be deleted permanently. Nothing was sent."
            exit 0
        fi
        api DELETE "/wiki/api/v2/$COMMENT_KIND/$COMMENT_ID"
        api_ok || api_fail "$API_BODY" "deleting comment $COMMENT_ID"
        echo "Deleted $COMMENT_LABEL comment $COMMENT_ID. Confluence cannot restore it."
        ;;

    *)
        usage
        ;;
esac
