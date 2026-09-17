#!/bin/bash
# Shared configuration and request helpers for the Jira skill.
# Sourced by the other scripts; not run directly.
#
# The API token never reaches the command line. curl reads the URL, the
# credentials and the method from a 0600 config file, so the token is not
# visible in `ps`, in shell history, or in any process listing.

# Settings live under ~/.dbhq/<skill>/, which is the house rule for every DBHQ
# skill: one directory per skill, never a new top-level dotfile in $HOME. This
# was ~/.jira until 17 Sep 2026.
#
# The move happens here, on first run, guarded on the new directory not
# existing - so an existing install keeps working and nobody has to be told to
# move a file. mv preserves the 600 mode on config.json.
CONFIG_DIR="$HOME/.dbhq/jira"
CONFIG_FILE="$CONFIG_DIR/config.json"
LEGACY_DIR="$HOME/.jira"

jira_migrate_legacy_config() {
    [ -d "$CONFIG_DIR" ] && return 0
    [ -d "$LEGACY_DIR" ] || return 0
    mkdir -p "$HOME/.dbhq"
    chmod 700 "$HOME/.dbhq" 2>/dev/null || true
    mv "$LEGACY_DIR" "$CONFIG_DIR"
    chmod 700 "$CONFIG_DIR" 2>/dev/null || true
    echo "Moved Jira credentials from $LEGACY_DIR to $CONFIG_DIR." >&2
}

jira_migrate_legacy_config

require_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        echo "Error: no credentials. Cause: $CONFIG_FILE does not exist." >&2
        echo "Fix: run jira-setup.sh" >&2
        exit 1
    fi
    SITE=$(jq -r '.site // empty' "$CONFIG_FILE")
    EMAIL=$(jq -r '.email // empty' "$CONFIG_FILE")
    TOKEN=$(jq -r '.token // empty' "$CONFIG_FILE")
    if [ -z "$SITE" ] || [ -z "$EMAIL" ] || [ -z "$TOKEN" ]; then
        echo "Error: $CONFIG_FILE is missing site, email or token." >&2
        echo "Fix: re-run jira-setup.sh" >&2
        exit 1
    fi
}

# api <METHOD> <PATH> [JSON_BODY]
# PATH is appended to the site, e.g. /rest/api/3/myself
# Sets API_BODY and API_STATUS. Prints nothing.
#
# Call it as a plain statement, never as $(api ...) - a command substitution
# runs in a subshell, so API_STATUS would never reach the caller.
api() {
    local method="$1" path="$2" body="${3:-}"
    local cfg out

    cfg=$(mktemp) || { echo "Error: cannot create a temp file for the curl config." >&2; exit 1; }
    chmod 600 "$cfg"
    {
        printf 'url = "%s%s"\n' "$SITE" "$path"
        printf 'user = "%s:%s"\n' "$EMAIL" "$TOKEN"
        printf 'request = "%s"\n' "$method"
        printf 'header = "Accept: application/json"\n'
        printf 'silent\n'
        printf 'show-error\n'
        printf 'write-out = "\\n%%{http_code}"\n'
        if [ -n "$body" ]; then
            printf 'header = "Content-Type: application/json"\n'
            printf 'data-binary = "@-"\n'
        fi
    } > "$cfg"

    if [ -n "$body" ]; then
        out=$(printf '%s' "$body" | curl -K "$cfg")
    else
        out=$(curl -K "$cfg" < /dev/null)
    fi
    rm -f "$cfg"

    # write-out appended "\n<status>". $'\n' is literal inside a quoted
    # expansion, so the newline goes through a variable.
    local nl=$'\n'
    API_STATUS="${out##*"$nl"}"
    # These two are how api() returns. jira-meta.sh and jira-issues.sh source
    # this file and read them; shellcheck sees only this file, so it cannot.
    # shellcheck disable=SC2034
    API_BODY="${out%"$nl"*}"
}

# api_ok - true when the last api call returned a 2xx status.
api_ok() {
    case "$API_STATUS" in
        2*) return 0 ;;
        *)  return 1 ;;
    esac
}

# api_fail <response> <context> - report a Jira error as cause + fix, then exit 1.
api_fail() {
    local response="$1" context="$2" msg
    echo "Error: $context failed (HTTP $API_STATUS)." >&2
    msg=$(printf '%s' "$response" | jq -r '
        ((.errorMessages // []) + ((.errors // {}) | to_entries | map("\(.key): \(.value)")))
        | if length > 0 then join("; ") else empty end' 2>/dev/null)
    if [ -n "$msg" ]; then
        echo "Cause: $msg" >&2
    else
        echo "Cause: $(printf '%s' "$response" | head -c 300)" >&2
    fi
    case "$API_STATUS" in
        401) echo "Fix: the email or token is wrong. Re-run jira-setup.sh." >&2 ;;
        403) echo "Fix: your account lacks permission for this project." >&2 ;;
        404) echo "Fix: check the project key or issue key exists and is visible to you." >&2 ;;
        429) echo "Fix: rate limited (60 requests/minute). Wait a minute and retry." >&2 ;;
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
