#!/bin/bash
# Confluence search - find pages by CQL or by free text, and list spaces.
#
# Read only. Nothing here writes.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../_shared/scripts/_common.sh
. "$SCRIPT_DIR/../../_shared/scripts/_common.sh"

usage() {
    cat >&2 <<'USAGE'
Usage:
  confluence-search.sh spaces [limit]
  confluence-search.sh cql '<CQL query>' [limit]
  confluence-search.sh text '<words>' [limit]

Examples:
  confluence-search.sh spaces
  confluence-search.sh cql 'space = DOCS and type = page order by lastmodified desc' 10
  confluence-search.sh text 'egress address'

CQL is the leverage here. Useful clauses:
  space = DOCS                      a single space by key
  title ~ "payment"                 title contains
  text ~ "egress"                   body contains
  lastmodified >= now("-7d")        changed in the last week
  ancestor = 1234567                anywhere under a parent page
USAGE
    exit 1
}

# url_encode <string> - percent-encode a query string value with jq, so a
# space, a quote or an ampersand in a CQL query cannot break the URL.
url_encode() {
    jq -rn --arg v "$1" '$v | @uri'
}

CMD="${1:-}"
case "$CMD" in
    spaces)
        LIMIT="${2:-50}"
        require_config
        api GET "/wiki/api/v2/spaces?limit=$LIMIT"
        api_ok || api_fail "$API_BODY" "listing spaces"
        printf '%s' "$API_BODY" | jq -r '
            "ID\tKEY\tNAME",
            (.results[] | "\(.id)\t\(.key)\t\(.name)")' | column -t -s$'\t'
        ;;

    cql|text)
        [ -n "${2:-}" ] || usage
        LIMIT="${3:-25}"
        require_config
        if [ "$CMD" = "text" ]; then
            # Never splice $2 raw between literal quotes. An even number of
            # embedded quotes does not error - it closes the string early and
            # the rest becomes a second CQL clause. `text 'x" or type = page'`
            # measured live: raw interpolation returned HTTP 200 with 2069
            # results (every page) where the intended query matches zero. jq
            # emits the literal already quoted and escaped; CQL's string
            # escaping is a subset of JSON's, so @json output embeds directly
            # and safely, and there is no failure mode where the query
            # silently widens past what the words say.
            ESCAPED=$(jq -rn --arg w "$2" '$w | @json')
            QUERY="type = page and text ~ $ESCAPED"
        else
            QUERY="$2"
        fi
        api GET "/wiki/rest/api/search?cql=$(url_encode "$QUERY")&limit=$LIMIT"
        api_ok || api_fail "$API_BODY" "searching Confluence"
        COUNT=$(printf '%s' "$API_BODY" | jq -r '.results | length')
        if [ "$COUNT" = "0" ]; then
            echo "No pages matched."
            echo "Query: $QUERY"
            exit 0
        fi
        printf '%s' "$API_BODY" | jq -r '
            "ID\tSPACE\tTITLE",
            (.results[]
             | "\(.content.id // "-")\t\(.resultGlobalContainer.title // "-")\t\(.content.title // .title)")' \
            | column -t -s$'\t'
        echo
        echo "$COUNT result(s). Read one with: confluence-pages.sh read <ID>"
        ;;

    *) usage ;;
esac
