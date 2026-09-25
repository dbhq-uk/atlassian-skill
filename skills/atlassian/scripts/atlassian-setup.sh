#!/bin/bash
# Atlassian setup - store and verify the one Atlassian Cloud credential every
# script in this skill uses, for Jira and Confluence alike.

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

SETUP="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"

usage() {
    cat <<EOF
Usage:
  $SETUP
      Ask for the site URL, the email and the API token. Needs a terminal.

  $SETUP --site <URL> --email <ADDRESS> [--overwrite] < token-file
      The same, for a script: the token is read from stdin, never from an
      argument. An existing credential is replaced only with --overwrite,
      and only once the new one verifies.
EOF
}

SITE=""
EMAIL=""
OVERWRITE_FLAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --site|--email)
            [ $# -ge 2 ] && [ -n "$2" ] || { echo "Error: $1 needs a value." >&2; usage >&2; exit 1; }
            if [ "$1" = "--site" ]; then SITE="$2"; else EMAIL="$2"; fi
            shift 2 ;;
        --overwrite) OVERWRITE_FLAG=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Error: unknown option '$1'." >&2; usage >&2; exit 1 ;;
    esac
done

# Two ways in. With --site and --email, the site and email come from the
# arguments and the token from stdin, so a script can set up a machine. With
# neither, setup asks for all three, which needs a person at a terminal.
SCRIPTED=""
if [ -n "$SITE" ] || [ -n "$EMAIL" ]; then
    [ -n "$SITE" ] && [ -n "$EMAIL" ] || {
        echo "Error: --site and --email go together. Give both, or neither to be asked." >&2
        exit 1
    }
    SCRIPTED=1
fi

# An agent that finds no credential is told to run setup, and an agent's
# shell has no terminal. `read` then hits end of input and, under set -e,
# the script used to exit 1 with no message at all. Say what to do instead.
if [ -z "$SCRIPTED" ] && [ ! -t 0 ]; then
    {
        echo "Error: setup needs a terminal, and there is none here. Nothing was saved."
        echo "Cause: it asks for an API token with the input hidden, so a person has to type it."
        echo "Fix: run this in your own terminal:"
        echo
        echo "  $SETUP"
        echo
        echo "Or, from a script, give the site and email and put the token on stdin:"
        echo
        echo "  $SETUP --site https://mycompany.atlassian.net --email you@example.com < token-file"
    } >&2
    exit 1
fi

if [ -n "$SCRIPTED" ]; then
    if [ -f "$CONFIG_FILE" ] && [ -z "$OVERWRITE_FLAG" ]; then
        echo "Error: a credential already exists at $CONFIG_FILE. Nothing was changed." >&2
        echo "Fix: add --overwrite to replace it. The new one is saved only if it verifies." >&2
        exit 1
    fi
    if [ -t 0 ]; then
        read -r -s -p "API token (input hidden): " TOKEN || true
        echo
    else
        IFS= read -r TOKEN || true
    fi
else
    echo "=== Atlassian Cloud API setup ==="
    echo
    echo "One credential authenticates both Jira and Confluence on the same site."
    echo "You need three things:"
    echo "  1. Your site URL, e.g. https://mycompany.atlassian.net"
    echo "  2. The email address on your Atlassian account"
    echo "  3. An API token from https://id.atlassian.com/manage-profile/security/api-tokens"
    echo

    if [ -f "$CONFIG_FILE" ] && [ -z "$OVERWRITE_FLAG" ]; then
        echo "Existing configuration found at $CONFIG_FILE."
        # `|| true` on every read: end of input (Ctrl-D) must reach the
        # checks below and their messages, not end the script silently.
        read -r -p "Overwrite? (y/N): " OVERWRITE || true
        if [ "$OVERWRITE" != "y" ] && [ "$OVERWRITE" != "Y" ]; then
            echo "Keeping existing configuration."
            exit 0
        fi
    fi

    echo
    read -r -p "Site URL: " SITE || true
    echo
    read -r -p "Email: " EMAIL || true
    echo
    # -s so the token is not echoed to the terminal or left on screen
    read -r -s -p "API token (input hidden): " TOKEN || true
    echo
fi

SITE="${SITE%/}"
case "$SITE" in
    https://*) ;;
    http://*)  echo "Error: use https, not http." >&2; exit 1 ;;
    "")        echo "Error: the site URL is required." >&2; exit 1 ;;
    *)         SITE="https://$SITE" ;;
esac
[ -n "$EMAIL" ] || { echo "Error: the email is required." >&2; exit 1; }
# A token file often ends in a newline, and a pasted one can carry a space.
# An API token never contains whitespace, so strip all of it.
TOKEN="${TOKEN//[[:space:]]/}"
[ -n "$TOKEN" ] || { echo "Error: the token is required. Nothing was saved." >&2; exit 1; }

# verify <url> [auth] - call a URL, with the entered credentials unless the
# second argument is "anonymous". Sets STATUS and BODY. The token goes in a
# 0600 curl config file, never on the command line, so it stays out of ps
# output and shell history.
verify() {
    local cfg out nl
    cfg=$(mktemp); chmod 600 "$cfg"
    # Same reason as _common.sh's api(): the `rm -f "$cfg"` below only runs
    # on a normal return, and a signal mid-request would otherwise leave a
    # file naming the token in plain text sitting in /tmp.
    trap 'rm -f "$cfg"' EXIT INT TERM HUP
    {
        printf 'url = "%s"\n' "$1"
        [ "${2:-}" = "anonymous" ] || printf 'user = "%s:%s"\n' "$EMAIL" "$TOKEN"
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

# The URL may only be a plain https host: it goes inside a quoted curl config
# value, where a double quote or a newline would start a second directive.
case "$SITE" in
    *'"'*|*$'\n'*|*' '*) echo "Error: '$SITE' is not a site URL." >&2; exit 1 ;;
esac

# --- Verify before writing anything ---
# Two kinds of API token. A classic token calls the site itself. A scoped
# token (one created with scopes) must call Atlassian's gateway,
# api.atlassian.com/ex/<product>/<cloud id>, and the site answers it with
# 401. So try the site first and, on a 401, the gateway. /myself is the
# probe because it has no anonymous answer: other endpoints can return 200
# and an empty list to a token the site does not recognise. The cloud id
# comes from the site's public tenant_info, which needs no credential.
GATEWAY="https://api.atlassian.com/ex"
JIRA_BASE="$SITE"
CONFLUENCE_BASE="$SITE"
SCOPED="no"
verify "$SITE/_edge/tenant_info" anonymous
CLOUD_ID=""
if [ "$STATUS" = "200" ]; then
    CLOUD_ID=$(printf '%s' "$BODY" | jq -r '.cloudId // empty' 2>/dev/null || true)
    # A cloud id is a UUID. Anything else is not put into a URL.
    case "$CLOUD_ID" in
        *[!0-9a-fA-F-]*) CLOUD_ID="" ;;
    esac
fi

echo
echo "Verifying Jira access at $SITE/rest/api/3/myself ..."
verify "$SITE/rest/api/3/myself"
if [ "$STATUS" = "401" ] && [ -n "$CLOUD_ID" ]; then
    echo "The site refused the token. Trying it as a scoped token at $GATEWAY/jira/$CLOUD_ID ..."
    verify "$GATEWAY/jira/$CLOUD_ID/rest/api/3/myself"
    if [ "$STATUS" = "200" ]; then
        SCOPED="yes"
        JIRA_BASE="$GATEWAY/jira/$CLOUD_ID"
        CONFLUENCE_BASE="$GATEWAY/confluence/$CLOUD_ID"
    fi
fi
if [ "$STATUS" != "200" ]; then
    echo "Error: verification failed (HTTP $STATUS). Nothing was saved." >&2
    case "$STATUS" in
        401) echo "Cause: the email and token were rejected, at the site and, for a scoped token, at api.atlassian.com." >&2
             echo "Fix: check the token belongs to this email's account and has not been revoked or expired. An API token lasts at most one year. A scoped token also needs the Jira scopes listed in SECURITY.md." >&2 ;;
        403) echo "Cause: the token was accepted but may not read your own profile." >&2
             echo "Fix: a scoped token needs the scopes listed in SECURITY.md." >&2 ;;
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
echo "Verifying Confluence access at $CONFLUENCE_BASE/wiki/api/v2/spaces ..."
verify "$CONFLUENCE_BASE/wiki/api/v2/spaces?limit=1"
CONFLUENCE="yes"
if [ "$STATUS" != "200" ]; then
    CONFLUENCE="no"
    echo "Warning: no Confluence access (HTTP $STATUS)." >&2
    if [ "$SCOPED" = "yes" ]; then
        echo "Cause: this account has no Confluence licence on $SITE, the site has no Confluence, or the token has no Confluence scopes." >&2
    else
        echo "Cause: this account has no Confluence licence on $SITE, or the site has no Confluence." >&2
    fi
    echo "Fix: the Jira commands will work. The Confluence commands and publish.sh will not until that is granted." >&2
fi

# --- Write with restrictive permissions ---
mkdir -p "$CONFIG_DIR"
chmod 700 "$CONFIG_DIR"
UMASK_OLD=$(umask); umask 077
# Written to a temp file and moved into place, not `jq ... > "$CONFIG_FILE"`
# directly - that redirection truncates CONFIG_FILE the instant the shell
# opens it, before jq ever runs, so a jq failure (or this process being
# killed mid-write) would destroy a working credential and replace it with
# nothing, not just fail to update it. mv within the same directory is
# atomic, the same reasoning frontmatter.py's _write() already documents
# for the file it rewrites.
TMP_CONFIG=$(mktemp "$CONFIG_DIR/.config.json.XXXXXX")
trap 'rm -f "$TMP_CONFIG"' EXIT INT TERM HUP
# The token goes in through the environment (env.TOKEN), not --arg. jq's
# argv is its own process's command line, and /proc/<pid>/cmdline is
# world-readable - the exact leak the token-never-reaches-a-command-line
# rule elsewhere in this repository exists to close. TOKEN="$TOKEN" sets it
# only in this one child process's environment, which is not world-readable
# the way its argv is.
if ! TOKEN="$TOKEN" jq -n --arg site "$SITE" --arg email "$EMAIL" --arg conf "$CONFLUENCE" \
        --arg scoped "${SCOPED:-no}" --arg cloud "${CLOUD_ID:-}" \
        --arg jira_base "${JIRA_BASE:-}" --arg confluence_base "${CONFLUENCE_BASE:-}" \
        '{site: $site, email: $email, token: env.TOKEN, confluence: ($conf == "yes"),
          scoped: ($scoped == "yes"), cloud_id: $cloud,
          jira_base: $jira_base, confluence_base: $confluence_base}' \
        > "$TMP_CONFIG"; then
    umask "$UMASK_OLD"
    echo "Error: could not build the credential file - nothing was saved." >&2
    echo "Fix: any existing credential at $CONFIG_FILE is untouched. Try again." >&2
    exit 1
fi
umask "$UMASK_OLD"
chmod 600 "$TMP_CONFIG"
mv "$TMP_CONFIG" "$CONFIG_FILE"

echo
echo "Connected as $NAME ($ACCOUNT)."
echo "Jira: yes.  Confluence: $CONFLUENCE.  Scoped token: $SCOPED."
echo "Saved to $CONFIG_FILE (permissions 600)."
echo
echo "Next: $SCRIPT_DIR/jira-meta.sh projects"
