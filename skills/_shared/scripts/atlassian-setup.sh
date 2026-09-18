#!/bin/bash
# Atlassian setup - store and verify one Atlassian Cloud credential for jira,
# confluence and confluence-publish.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# See _common.sh for the ~/.jira -> ~/.dbhq/jira -> ~/.dbhq/atlassian moves and
# why they are guarded the way they are. Repeated rather than sourced: setup
# must run before any credential exists, and _common.sh's require_config
# assumes one does.
CONFIG_DIR="$HOME/.dbhq/atlassian"
CONFIG_FILE="$CONFIG_DIR/config.json"

if [ ! -d "$CONFIG_DIR" ]; then
    FROM=""
    if [ -d "$HOME/.dbhq/jira" ]; then
        FROM="$HOME/.dbhq/jira"
    elif [ -d "$HOME/.jira" ]; then
        FROM="$HOME/.jira"
    fi
    if [ -n "$FROM" ]; then
        mkdir -p "$HOME/.dbhq"
        chmod 700 "$HOME/.dbhq" 2>/dev/null || true
        mv "$FROM" "$CONFIG_DIR"
        chmod 700 "$CONFIG_DIR" 2>/dev/null || true
        echo "Moved Atlassian credentials from $FROM to $CONFIG_DIR."
        echo
    fi
fi

echo "=== Atlassian Cloud API setup ==="
echo
echo "One credential authenticates both Jira and Confluence on the same site."
echo "You need three things:"
echo "  1. Your site URL, e.g. https://mycompany.atlassian.net"
echo "  2. The email address on your Atlassian account"
echo "  3. An API token from https://id.atlassian.com/manage-profile/security/api-tokens"
echo

if [ -f "$CONFIG_FILE" ]; then
    echo "Existing configuration found at $CONFIG_FILE."
    read -r -p "Overwrite? (y/N): " OVERWRITE
    if [ "$OVERWRITE" != "y" ] && [ "$OVERWRITE" != "Y" ]; then
        echo "Keeping existing configuration."
        exit 0
    fi
fi

echo
read -r -p "Site URL: " SITE
SITE="${SITE%/}"
case "$SITE" in
    https://*) ;;
    http://*)  echo "Error: use https, not http." >&2; exit 1 ;;
    "")        echo "Error: the site URL is required." >&2; exit 1 ;;
    *)         SITE="https://$SITE" ;;
esac

echo
read -r -p "Email: " EMAIL
[ -n "$EMAIL" ] || { echo "Error: the email is required." >&2; exit 1; }

echo
# -s so the token is not echoed to the terminal or left on screen
read -r -s -p "API token (input hidden): " TOKEN
echo
[ -n "$TOKEN" ] || { echo "Error: the token is required." >&2; exit 1; }

# verify <path> - call SITE+path with the entered credentials.
# Sets STATUS and BODY. The token goes in a 0600 curl config file, never on
# the command line, so it stays out of ps output and shell history.
verify() {
    local cfg out nl
    cfg=$(mktemp); chmod 600 "$cfg"
    # Same reason as _common.sh's api(): the `rm -f "$cfg"` below only runs
    # on a normal return, and a signal mid-request would otherwise leave a
    # file naming the token in plain text sitting in /tmp.
    trap 'rm -f "$cfg"' EXIT INT TERM HUP
    {
        printf 'url = "%s%s"\n' "$SITE" "$1"
        printf 'user = "%s:%s"\n' "$EMAIL" "$TOKEN"
        printf 'header = "Accept: application/json"\n'
        printf 'silent\nshow-error\n'
        printf 'write-out = "\\n%%{http_code}"\n'
    } > "$cfg"
    out=$(curl -K "$cfg" < /dev/null) || true
    rm -f "$cfg"
    nl=$'\n'
    STATUS="${out##*"$nl"}"
    BODY="${out%"$nl"*}"
}

# --- Verify before writing anything ---
echo
echo "Verifying Jira access at $SITE/rest/api/3/myself ..."
verify "/rest/api/3/myself"
if [ "$STATUS" != "200" ]; then
    echo "Error: verification failed (HTTP $STATUS). Nothing was saved." >&2
    case "$STATUS" in
        401) echo "Cause: the email and token were rejected." >&2
             echo "Fix: check the token is for this account and has not been revoked." >&2 ;;
        404) echo "Cause: no Jira REST API at that site URL." >&2
             echo "Fix: check the site URL." >&2 ;;
        000) echo "Cause: could not reach the site." >&2
             echo "Fix: check the URL and your network." >&2 ;;
        *)   echo "Cause: $(printf '%s' "$BODY" | head -c 200)" >&2 ;;
    esac
    exit 1
fi
NAME=$(printf '%s' "$BODY" | jq -r '.displayName // "unknown"')
ACCOUNT=$(printf '%s' "$BODY" | jq -r '.accountId // "unknown"')

# Confluence is checked second and a failure is a WARNING, not an error. A
# token can be valid for Jira and carry no Confluence licence, and a Jira-only
# install is legitimate - so say what is unavailable and save anyway, rather
# than refusing a credential that works for the skill the user came for.
echo "Verifying Confluence access at $SITE/wiki/api/v2/spaces ..."
verify "/wiki/api/v2/spaces?limit=1"
CONFLUENCE="yes"
if [ "$STATUS" != "200" ]; then
    CONFLUENCE="no"
    echo "Warning: no Confluence access (HTTP $STATUS)." >&2
    echo "Cause: this account has no Confluence licence on $SITE, or the site has no Confluence." >&2
    echo "Fix: jira will work. confluence and confluence-publish will not until that is granted." >&2
fi

# --- Write with restrictive permissions ---
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
UMASK_OLD=$(umask); umask 077
# The token goes in through the environment (env.TOKEN), not --arg. jq's
# argv is its own process's command line, and /proc/<pid>/cmdline is
# world-readable - the exact leak the token-never-reaches-a-command-line
# rule elsewhere in this repository exists to close. TOKEN="$TOKEN" sets it
# only in this one child process's environment, which is not world-readable
# the way its argv is.
TOKEN="$TOKEN" jq -n --arg site "$SITE" --arg email "$EMAIL" --arg conf "$CONFLUENCE" \
    '{site: $site, email: $email, token: env.TOKEN, confluence: ($conf == "yes")}' > "$CONFIG_FILE"
umask "$UMASK_OLD"
chmod 600 "$CONFIG_FILE"

echo
echo "Connected as $NAME ($ACCOUNT)."
echo "Jira: yes.  Confluence: $CONFLUENCE."
echo "Saved to $CONFIG_FILE (permissions 600)."
echo
echo "Next: $SCRIPT_DIR/../../jira/scripts/jira-meta.sh projects"
