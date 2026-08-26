#!/bin/bash
# Jira setup - store and verify Jira Cloud API credentials.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="$HOME/.jira"
CONFIG_FILE="$CONFIG_DIR/config.json"

echo "=== Jira Cloud API setup ==="
echo
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

# --- Verify before writing anything ---
echo
echo "Verifying against $SITE/rest/api/3/myself ..."

CFG=$(mktemp); chmod 600 "$CFG"
{
    printf 'url = "%s/rest/api/3/myself"\n' "$SITE"
    printf 'user = "%s:%s"\n' "$EMAIL" "$TOKEN"
    printf 'header = "Accept: application/json"\n'
    printf 'silent\nshow-error\n'
    printf 'write-out = "\\n%%{http_code}"\n'
} > "$CFG"
OUT=$(curl -K "$CFG" < /dev/null) || true
rm -f "$CFG"

STATUS="${OUT##*$'\n'}"
BODY="${OUT%$'\n'*}"

if [ "$STATUS" != "200" ]; then
    echo "Error: verification failed (HTTP $STATUS). Nothing was saved." >&2
    case "$STATUS" in
        401) echo "Cause: the email and token were rejected. Fix: check the token is for this account and has not been revoked." >&2 ;;
        404) echo "Cause: no Jira REST API at that site URL. Fix: check the site URL." >&2 ;;
        000) echo "Cause: could not reach the site. Fix: check the URL and your network." >&2 ;;
        *)   echo "Cause: $(printf '%s' "$BODY" | head -c 200)" >&2 ;;
    esac
    exit 1
fi

NAME=$(printf '%s' "$BODY" | jq -r '.displayName // "unknown"')
ACCOUNT=$(printf '%s' "$BODY" | jq -r '.accountId // "unknown"')

# --- Write with restrictive permissions ---
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
UMASK_OLD=$(umask); umask 077
jq -n --arg site "$SITE" --arg email "$EMAIL" --arg token "$TOKEN" \
    '{site: $site, email: $email, token: $token}' > "$CONFIG_FILE"
umask "$UMASK_OLD"
chmod 600 "$CONFIG_FILE"

echo
echo "Connected as $NAME ($ACCOUNT)."
echo "Saved to $CONFIG_FILE (permissions 600)."
echo
echo "Next: $SCRIPT_DIR/jira-meta.sh projects"
