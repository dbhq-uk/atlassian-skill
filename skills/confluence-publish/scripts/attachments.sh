#!/bin/bash
# Upload a file as a Confluence page attachment and print its media identity.
#
# The attachment API is v1 only - there is no v2 equivalent - so this is the
# one place the skill touches v1. It is a documented, supported endpoint; the
# v1 retirement covers the content APIs that v2 replaced, and attachments are
# not among them.
#
# There is no delete. Removing an attachment is a human job in the UI.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../_shared/scripts/_common.sh
. "$SCRIPT_DIR/../../_shared/scripts/_common.sh"

usage() {
    cat >&2 <<'USAGE'
Usage: attachments.sh upload <page-id> <file>

Uploads the file as an attachment on that page and prints, tab separated:
  <media-id>  <collection>

Feed both into a figure:
  <figure data-type="media-single" data-layout="center" data-width="80">
    <div data-type="media" data-media-type="file" data-id="MEDIA_ID"
         data-collection="COLLECTION" data-alt="diagram.png"></div>
    <figcaption>What it shows</figcaption>
  </figure>

Re-uploading a file with the same name replaces it in place - same
attachment, version incremented, no duplicate - but issues a NEW media id
every time (proven live against a real site, Task 14: not assumed). A
figure already on a page keeps pointing at the id it was written with, so
re-uploading an image invalidates any figure already published from the
old id - re-run this and splice the new id in, do not just re-run the
upload and leave the old figure as it was.
USAGE
    exit 1
}

[ "${1:-}" = "upload" ] || usage
PAGE_ID="${2:-}"; FILE="${3:-}"
[ -n "$PAGE_ID" ] && [ -n "$FILE" ] || usage
case "$PAGE_ID" in
    ''|*[!0-9]*)
        echo "Error: '$PAGE_ID' is not a valid page id." >&2
        echo "Cause: a Confluence page id is always numeric." >&2
        echo "Fix: pass the numeric id printed by 'confluence-pages.sh read' or 'create'." >&2
        exit 1
        ;;
esac
[ -f "$FILE" ] || { echo "Error: no such file: $FILE" >&2; exit 1; }

# $FILE lands verbatim in a curl -K config file as `form = "file=@$FILE"`,
# the same shape _common.sh's api() guards for PATH. A double quote or a
# newline in the path breaks out of that quoted value and starts a new curl
# directive on the next line - a `proxy =` line needs no slashes to work, and
# the `user = "email:TOKEN"` line already above it in the same file applies
# to whatever transfer the injected directive describes. Uploading a cloned
# repo's own images is this skill's documented job, so the filename here is
# exactly as attacker-controlled as PATH is in api() - refused the same way,
# before the config file is ever built.
case "$FILE" in
    *'"'*|*$'\n'*)
        echo "Error: refusing to upload this file." >&2
        echo "Cause: its path contains a double quote or a newline, which can break out of the curl config file and inject a second, attacker-chosen request that still carries the site's credentials." >&2
        echo "Fix: rename the file (or the directory holding it) to remove that character." >&2
        exit 1
        ;;
esac

require_config

# Multipart upload cannot go through api(), which sends JSON. Same discipline
# applies: credentials come from a 0600 curl config file, never the command
# line, so the token stays out of ps output and shell history.
#
# PUT, not POST: this endpoint creates on first upload and replaces the same
# attachment (same attachment id, version incremented, no duplicate) on
# every subsequent one with the same filename - proven live against a real
# site (Task 14), not assumed from the v1 docs, which describe POST as
# create-only and route an update through a second, attachment-id-scoped
# endpoint instead. PUT here does both in one call. The media id
# (.extensions.fileId, what a figure's data-id holds) is NOT stable across
# a replace, though - also proven live, and the opposite of what a first
# reading of "replaces it" would suggest - so the usage text above says so.
CFG=$(mktemp) || { echo "Error: cannot create a temp file for the curl config." >&2; exit 1; }
chmod 600 "$CFG"
# Same reason as _common.sh's api(): the `rm -f "$CFG"` below only runs on a
# normal return, and a signal mid-upload would otherwise leave a file naming
# the token in plain text sitting in /tmp.
trap 'rm -f "$CFG"' EXIT INT TERM HUP
{
    printf 'url = "%s/wiki/rest/api/content/%s/child/attachment"\n' "$SITE" "$PAGE_ID"
    printf 'user = "%s:%s"\n' "$EMAIL" "$TOKEN"
    printf 'request = "PUT"\n'
    printf 'header = "X-Atlassian-Token: nocheck"\n'
    printf 'header = "Accept: application/json"\n'
    printf 'form = "file=@%s"\n' "$FILE"
    printf 'form = "minorEdit=true"\n'
    printf 'silent\n'
    printf 'show-error\n'
    printf 'write-out = "\\n%%{http_code}"\n'
} > "$CFG"

# errexit off around curl itself, same reason as _common.sh's api(): a
# network, DNS or TLS failure must not abort the script before the config
# file - which names the token in plain text - is removed below.
set +e
OUT=$(curl -K "$CFG" < /dev/null)
CURL_STATUS=$?
set -e
rm -f "$CFG"

if [ "$CURL_STATUS" -ne 0 ]; then
    echo "Error: the request to the Atlassian API failed (curl exit $CURL_STATUS)." >&2
    echo "Cause: a network, DNS, TLS or proxy failure - see curl's own message above, if any." >&2
    echo "Fix: check connectivity and try again." >&2
    exit 1
fi

NL=$'\n'
STATUS="${OUT##*"$NL"}"
BODY="${OUT%"$NL"*}"

if [ "$STATUS" != "200" ]; then
    echo "Error: uploading $(basename "$FILE") to page $PAGE_ID failed (HTTP $STATUS)." >&2
    echo "Cause: $(printf '%s' "$BODY" | jq -r '.message // .' 2>/dev/null | head -c 300)" >&2
    case "$STATUS" in
        403) echo "Fix: you need edit permission on that page to attach a file." >&2 ;;
        404) echo "Fix: check the page id exists and is visible to you." >&2 ;;
        413) echo "Fix: the file is over the site's attachment size limit." >&2 ;;
    esac
    exit 1
fi

# PUT returns {results:[...]} on first upload; a straight object on a
# replace - proven live rather than assumed (Task 14). Handle both rather
# than picking one.
MEDIA_LINE=$(printf '%s' "$BODY" | jq -r '
    (if .results then .results[0] else . end)
    | "\(.extensions.fileId // .id)\t\(.extensions.collectionName // ("contentId-" + .container.id))"')

if [ -z "$MEDIA_LINE" ] || [ "$MEDIA_LINE" = "	" ]; then
    echo "Error: uploaded $(basename "$FILE") but could not read back a media id and collection." >&2
    echo "Cause: the response did not carry .extensions.fileId/.id or a way to infer the collection." >&2
    echo "Fix: check the page manually - the file may have attached without a usable media reference." >&2
    exit 1
fi

printf '%s\n' "$MEDIA_LINE"
