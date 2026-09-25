#!/bin/bash
# Publish a markdown file to Confluence, idempotently.
#
# The file is the master and Confluence is the rendering. The binding lives in
# the file's own frontmatter, so a move or a rename cannot break it:
#
#   ---
#   confluence:
#     space: "98765"
#     parent: "1234567"
#     page_id: "8901234"
#   ---
#
# page_id is written back on the first publish. No page_id means create; a
# page_id means update that page.
#
# Things worth knowing about this script's design, each deliberate:
#
# - Publish replaces the page body with the file, by design. So the hazard
#   is not what the converter can or cannot round-trip - the file replaces
#   all of it either way - but a person having edited the page in
#   Confluence since the last publish. Every publish writes its version
#   with the message "Published from <file>", so the latest version's
#   message says who wrote last. Anything else is refused, naming that
#   version, its author and its date, until the edit is brought into the
#   file and the publish confirms it with --base-version <that version>.
#   That flag is an acknowledgement of one exact version, not a --force: if
#   another edit lands after it, the publish refuses again.
#
# - A publish that would change nothing sends nothing. The page's current
#   body is compared with what the file would write, ignoring what
#   Confluence assigns on save (local ids, an image's measured size), so
#   re-running an unchanged file does not fill the page history with empty
#   versions.
#
# - A local image the file shows is uploaded as a page attachment, and the
#   figure points at it. The upload carries the image's sha256 hash in its
#   comment, and an attachment of the same name and the same hash already
#   on the page is reused rather than uploaded again - a re-upload would
#   issue a new media id and make every publish look like a change.
#
# - The binding is parsed with parameter expansion, never with
#   `sed 's/^/FM_/' | eval`. eval runs the value half of a frontmatter line
#   as shell too, and a markdown file is exactly the kind of thing that gets
#   cloned from somewhere else - a space value crafted as
#   `98765"; rm -rf ~ #` would execute the moment this script read the file
#   it was asked to publish, before it ever spoke to Confluence. Nothing
#   here is ever eval'd.

set -e
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTMLPLUS="$SCRIPT_DIR/htmlplus.py"
MD="$SCRIPT_DIR/md_to_htmlplus.py"
ADF_EDIT="$SCRIPT_DIR/adf_edit.py"
PAGES="$SCRIPT_DIR/confluence-pages.sh"
ATTACH="$SCRIPT_DIR/attachments.sh"
# shellcheck source=_common.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_common.sh"
# shellcheck source=_confluence.sh source-path=SCRIPTDIR
. "$SCRIPT_DIR/_confluence.sh"

usage() {
    cat >&2 <<'USAGE'
Usage: publish.sh <file.md> [--dry-run] [--space <space-id>] [--parent <page-id>]
                  [--base-version <n>]

  --dry-run       Convert everything and preview what would happen. Sends
                  nothing. If the file already carries a page_id, this also
                  reads (GET only) the live page, to say whether it was
                  edited in Confluence since the last publish and whether the
                  file changes it at all - a file with no page_id yet needs
                  no credentials.
  --space         Space id, if the file's frontmatter does not carry one.
  --parent        Parent page id, if the file's frontmatter does not carry one.
  --base-version  After a refusal because the page was edited in
                  Confluence: the version you brought into the file. The
                  publish goes ahead only while the page is still at it.

The file's frontmatter is the binding and wins over the flags where both
are present.
USAGE
    exit 1
}

FILE="${1:-}"; shift || usage
[ -n "$FILE" ] && [ -f "$FILE" ] || usage

DRY_RUN=0; SPACE_ARG=""; PARENT_ARG=""; BASE_VERSION=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run)      DRY_RUN=1; shift ;;
        --space)        [ $# -ge 2 ] || usage; SPACE_ARG="$2";    shift 2 ;;
        --parent)       [ $# -ge 2 ] || usage; PARENT_ARG="$2";   shift 2 ;;
        --base-version) [ $# -ge 2 ] || usage; BASE_VERSION="$2"; shift 2 ;;
        *) usage ;;
    esac
done
MESSAGE="Published from $FILE"
FILE_DIR="$(cd "$(dirname "$FILE")" && pwd)"

# --- Binding: plain key=value lines, picked apart with parameter expansion.
# Never eval'd - see the header comment.
FM_OUT=$(python3 "$SCRIPT_DIR/frontmatter.py" read "$FILE")
FM_SPACE=$(printf '%s\n' "$FM_OUT" | sed -n 's/^space=//p')
FM_PARENT=$(printf '%s\n' "$FM_OUT" | sed -n 's/^parent=//p')
FM_PAGE_ID=$(printf '%s\n' "$FM_OUT" | sed -n 's/^page_id=//p')
SPACE="${FM_SPACE:-$SPACE_ARG}"
PARENT="${FM_PARENT:-$PARENT_ARG}"
PAGE_ID="$FM_PAGE_ID"

WORK=$(mktemp -d)
cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

# --- The markdown, and the local images it shows. ---
# `set -o pipefail` above matters for every pipeline here: without it, a
# crash in frontmatter.py (a non-UTF-8 file, say) still leaves a pipeline's
# exit status at 0, because only the last stage's is seen - and a converter
# fed nothing converts it to an empty body and exits cleanly. With it, that
# crash stops this script at set -e instead of sailing through as a
# successful empty conversion.
python3 "$SCRIPT_DIR/frontmatter.py" body "$FILE" > "$WORK/body.md"
python3 "$MD" --list-images < "$WORK/body.md" > "$WORK/images.txt"

# Each image path is resolved against the file's own folder. Attachments are
# named by file name on the page, so two different images with the same name
# cannot both be attached - refused rather than one silently replacing the
# other.
IMAGE_COUNT=0
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
        /*) local_file="$path" ;;
        *)  local_file="$FILE_DIR/$path" ;;
    esac
    if [ ! -f "$local_file" ]; then
        echo "Error: $FILE shows an image that is not there: $path" >&2
        echo "Fix: check the path - it is read relative to the folder $FILE is in. Nothing was sent." >&2
        exit 1
    fi
    name=$(basename "$local_file")
    if grep -qxF "$name" "$WORK/names.txt" 2>/dev/null; then
        echo "Error: $FILE shows two different images named $name." >&2
        echo "Cause: a page attachment is named by its file name, so the second would replace the first." >&2
        echo "Fix: rename one of them. Nothing was sent." >&2
        exit 1
    fi
    printf '%s\n' "$name" >> "$WORK/names.txt"
    printf '%s\t%s\n' "$path" "$local_file" >> "$WORK/image-files.txt"
    IMAGE_COUNT=$((IMAGE_COUNT + 1))
done < "$WORK/images.txt"

# build_page <image-map.json> - convert the file with that map for its local
# images, and write $WORK/page.html (banner and body) and $WORK/page.json
# (its ADF). Sets TITLE. Stops the script, naming the problem, if the file
# does not convert or converts to nothing.
build_page() {
    python3 "$MD" --image-map "$1" < "$WORK/body.md" > "$WORK/body.html"

    # Refuse an empty body here, before the banner is ever added. The banner
    # is never empty by itself, so a banner in front of an empty body would
    # sail past every later check, and a file that is only frontmatter, or
    # one that converts to nothing (an HTML comment, stray whitespace), must
    # not silently become "the page now says only the source banner".
    local body_adf
    body_adf=$(python3 "$HTMLPLUS" to-adf < "$WORK/body.html") || {
        echo "Error: $FILE did not convert to a usable body." >&2
        echo "Cause: the HTML+ conversion failed - see the error above. Nothing was sent." >&2
        echo "Fix: correct $FILE and try again." >&2
        exit 1
    }
    if [ "$(printf '%s' "$body_adf" | jq -r '.content | length')" = "0" ]; then
        echo "Error: $FILE converts to an empty body." >&2
        echo "Cause: the file is only frontmatter, or its content converts to nothing to publish - htmlplus.py returned {\"content\":[]}." >&2
        echo "Fix: add content to $FILE and try again. Nothing was sent - not even the source banner." >&2
        exit 1
    fi

    # The title is the first <h1> the conversion emitted, else the file
    # name. Not `grep -m1 '^# '` over the raw file, which also matches a YAML
    # comment in the frontmatter and a shell comment in a fenced code block
    # - both would rename a live page to the wrong thing. Tags inside the
    # heading are stripped and entities unescaped, so the title is text.
    TITLE=$(python3 -c '
import html, re, sys
body = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"<h1>(.*?)</h1>", body, re.S)
if match:
    text = re.sub(r"<[^>]+>", "", match.group(1))
    print(html.unescape(text).strip())
' "$WORK/body.html")
    [ -n "$TITLE" ] || TITLE=$(basename "$FILE" .md)

    # The source banner. A reader who cannot edit needs somewhere to put a
    # correction, so the warning names the route as well as the rule. $FILE
    # is escaped first - a path holding < or > would otherwise corrupt the
    # fragment.
    local file_esc
    file_esc=$(printf '%s' "$FILE" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
    {
        printf '<div data-type="panel-info"><p>'
        printf 'Generated from <code>%s</code> in version control. ' "$file_esc"
        printf 'An edit made here is lost at the next publish - '
        printf 'leave a page comment instead and it is read back into the source.'
        printf '</p></div>'
        cat "$WORK/body.html"
    } > "$WORK/page.html"
    if ! python3 "$HTMLPLUS" to-adf < "$WORK/page.html" > "$WORK/page.json"; then
        echo "Fix: correct $FILE and try again. Nothing was sent." >&2
        exit 1
    fi
}

# placeholder_map - an image map with a stand-in id for every local image,
# so the file can be converted and checked before anything is uploaded.
placeholder_map() {
    if [ -f "$WORK/image-files.txt" ]; then
        jq -Rn '[inputs | split("\t") | {key: .[0], value: {id: "not-uploaded-yet", collection: "not-uploaded-yet"}}] | from_entries' \
            < "$WORK/image-files.txt" > "$WORK/map.json"
    else
        echo '{}' > "$WORK/map.json"
    fi
}

# upload_images <page-id> - attach every local image to the page, reusing
# one already there with the same name and the same sha256 hash, and write the
# map build_page reads.
upload_images() {
    local page_id="$1" path local_file hash found media
    echo '{}' > "$WORK/map.json"
    [ -f "$WORK/image-files.txt" ] || return 0
    while IFS=$'\t' read -r path local_file; do
        hash=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$local_file")
        found=$("$ATTACH" find "$page_id" "$(basename "$local_file")")
        if [ -n "$found" ] && [ "$(printf '%s' "$found" | cut -f3)" = "sha256:$hash" ]; then
            media=$(printf '%s' "$found" | cut -f1,2)
            echo "Image $path is already attached, unchanged."
        else
            media=$("$ATTACH" upload "$page_id" "$local_file" --comment "sha256:$hash")
            echo "Uploaded $path."
        fi
        jq --arg p "$path" --arg id "$(printf '%s' "$media" | cut -f1)" \
           --arg c "$(printf '%s' "$media" | cut -f2)" \
           '. + {($p): {id: $id, collection: $c}}' "$WORK/map.json" > "$WORK/map.next"
        mv "$WORK/map.next" "$WORK/map.json"
    done < "$WORK/image-files.txt"
}

# read_live - in a subshell, so a failed read stops only the read: the page
# as it is now, as JSON on stdout ({version, status, title, message, author,
# when}), and its body in $WORK/live.json.
read_live() {
    (
        require_config
        fetch_page "$PAGE_ID"
        printf '%s' "$CURRENT_BODY" > "$WORK/live.json"
        jq -n --arg v "$CURRENT_VERSION" --arg s "$CURRENT_STATUS" --arg t "$CURRENT_TITLE" \
              --arg m "$CURRENT_MESSAGE" --arg a "$CURRENT_AUTHOR" --arg w "$CURRENT_WHEN" \
              '{version: $v, status: $s, title: $t, message: $m, author: $a, when: $w}'
    )
}

# last_edit_was_publish <live-json> - whether the page's latest version is
# this file's own publish: its message is "Published from" a file of this
# name (the path can differ with the directory it was run from), or it is
# version 1 with no message, which is what a create leaves.
last_edit_was_publish() {
    local message version published
    message=$(printf '%s' "$1" | jq -r .message)
    version=$(printf '%s' "$1" | jq -r .version)
    if [ "$version" = "1" ] && [ -z "$message" ]; then
        return 0
    fi
    case "$message" in
        "Published from "*)
            published="${message#Published from }"
            [ "$(basename "$published")" = "$(basename "$FILE")" ]
            ;;
        *) return 1 ;;
    esac
}

placeholder_map
build_page "$WORK/map.json"

if [ "$DRY_RUN" = "1" ]; then
    echo "Dry run: $FILE"
    echo "  Title:  $TITLE"
    echo "  Space:  ${SPACE:-<none - pass --space>}"
    echo "  Parent: ${PARENT:-<none>}"
    if [ "$IMAGE_COUNT" -gt 0 ]; then
        echo "  Images: $IMAGE_COUNT local, uploaded as attachments (or reused if already attached unchanged)"
    fi
    if [ -n "$PAGE_ID" ]; then
        echo "  Action: UPDATE page $PAGE_ID"
        # GET only, so this keeps the "--dry-run sends nothing" promise. The
        # page can still change before the real run, so this is a preview.
        if LIVE=$(read_live 2> "$WORK/read.err"); then
            version=$(printf '%s' "$LIVE" | jq -r .version)
            if last_edit_was_publish "$LIVE" || [ "$BASE_VERSION" = "$version" ]; then
                echo "  Live page: version $version, last written by a publish"
                if [ "$IMAGE_COUNT" -eq 0 ] && python3 "$ADF_EDIT" same "$WORK/page.json" "$WORK/live.json"; then
                    echo "  Change: none - the page already holds this file, so nothing would be sent"
                fi
            else
                echo "  Live page: WOULD BE REFUSED - version $version was edited in Confluence by $(printf '%s' "$LIVE" | jq -r .author) on $(printf '%s' "$LIVE" | jq -r .when)"
                echo "    Bring that edit into $FILE, then publish with --base-version $version."
            fi
        else
            echo "  Live page: not checked - reading page $PAGE_ID failed:"
            sed 's/^/    /' "$WORK/read.err"
        fi
    else
        echo "  Action: CREATE, then write page_id back into the frontmatter"
    fi
    echo "  Body:   converts cleanly to ADF"
    echo "Nothing was sent."
    exit 0
fi

if [ -n "$PAGE_ID" ]; then
    if ! LIVE=$(read_live 2> "$WORK/read.err"); then
        cat "$WORK/read.err" >&2
        echo >&2
        echo "Publish stopped: could not read page $PAGE_ID before updating. Nothing was sent." >&2
        exit 1
    fi
    CURRENT_VERSION=$(printf '%s' "$LIVE" | jq -r .version)
    CURRENT_STATUS=$(printf '%s' "$LIVE" | jq -r .status)
    if [ -n "$BASE_VERSION" ] && [ "$BASE_VERSION" != "$CURRENT_VERSION" ]; then
        echo "Error: page $PAGE_ID has moved on since version $BASE_VERSION." >&2
        echo "Cause: --base-version was $BASE_VERSION; the page is now at version $CURRENT_VERSION." >&2
        echo "Fix: bring version $CURRENT_VERSION into $FILE too, then publish with --base-version $CURRENT_VERSION. Nothing was sent." >&2
        exit 1
    fi
    if [ -z "$BASE_VERSION" ] && ! last_edit_was_publish "$LIVE"; then
        echo "Error: page $PAGE_ID was edited in Confluence since $FILE was last published." >&2
        echo "Cause: version $CURRENT_VERSION, by $(printf '%s' "$LIVE" | jq -r .author) on $(printf '%s' "$LIVE" | jq -r .when), has the message \"$(printf '%s' "$LIVE" | jq -r .message)\", not \"$MESSAGE\". Publishing would overwrite it." >&2
        echo "Fix: nothing was sent. Read the page (confluence-pages.sh read $PAGE_ID --format markdown), bring that edit into $FILE, then publish with --base-version $CURRENT_VERSION to confirm you have." >&2
        exit 1
    fi

    upload_images "$PAGE_ID"
    build_page "$WORK/map.json"
    if python3 "$ADF_EDIT" same "$WORK/page.json" "$WORK/live.json"; then
        echo "Unchanged: page $PAGE_ID already holds $FILE (version $CURRENT_VERSION). Nothing was sent."
        exit 0
    fi
    VALUE=$(jq -Rs . < "$WORK/page.json")
    cleanup
    put_page "$PAGE_ID" "$TITLE" "$MESSAGE" "$VALUE"
else
    [ -n "$SPACE" ] || {
        echo "Error: no space id." >&2
        echo "Cause: the frontmatter has no confluence.space and --space was not passed." >&2
        echo "Fix: add one, or pass --space <id>. List ids with confluence-search.sh spaces." >&2
        exit 1
    }
    # A local image needs a page to be attached to, so a file that shows one
    # is created with a placeholder in its place, and the images and the
    # real body follow as version 2.
    if [ "$IMAGE_COUNT" -gt 0 ]; then
        sed 's#<figure data-type="media-single"[^>]*><div data-type="media" data-media-type="file" data-id="not-uploaded-yet"[^>]*></div></figure>#<p>(image being uploaded)</p>#g' \
            "$WORK/page.html" > "$WORK/create.html"
    else
        cp "$WORK/page.html" "$WORK/create.html"
    fi
    CREATE_ARGS=(--space "$SPACE" --title "$TITLE" --body-file "$WORK/create.html")
    [ -n "$PARENT" ] && CREATE_ARGS+=(--parent "$PARENT")
    if ! OUT=$("$PAGES" create "${CREATE_ARGS[@]}"); then
        echo >&2
        echo "Publish stopped: $FILE was not sent. Nothing was created. See the error above." >&2
        exit 1
    fi
    echo "$OUT"
    NEW_ID=$(printf '%s' "$OUT" | sed -n 's/^Created page \([0-9]*\):.*/\1/p')
    [ -n "$NEW_ID" ] || { echo "Error: could not read the new page id." >&2; exit 1; }
    # This is the one failure SKILL.md's own "commit that change, or the
    # next run creates a second page" warning is about - a page now exists
    # live, and if its id does not reach the file, nothing else will notice
    # until that second page appears. Guarded, not left to a raw traceback
    # (a read-only directory makes frontmatter.py's set-page-id die with an
    # uncaught PermissionError).
    if ! WRITE_ERR=$(python3 "$SCRIPT_DIR/frontmatter.py" set-page-id "$FILE" "$NEW_ID" 2>&1); then
        echo >&2
        echo "Error: page $NEW_ID was created but its id could not be written to $FILE." >&2
        echo "Cause: $(printf '%s' "$WRITE_ERR" | tail -n1)" >&2
        echo "Fix: add    page_id: \"$NEW_ID\"    under confluence: in $FILE by hand before re-running," >&2
        echo "     or the next run will create a second page." >&2
        exit 1
    fi
    echo "Wrote page_id $NEW_ID into $FILE - commit that change."

    if [ "$IMAGE_COUNT" -gt 0 ]; then
        PAGE_ID="$NEW_ID"
        upload_images "$PAGE_ID"
        build_page "$WORK/map.json"
        CURRENT_VERSION=1
        CURRENT_STATUS=current
        VALUE=$(jq -Rs . < "$WORK/page.json")
        cleanup
        put_page "$PAGE_ID" "$TITLE" "$MESSAGE" "$VALUE"
    fi
fi
