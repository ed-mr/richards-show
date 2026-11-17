#!/bin/sh
# Fetches the slideshow pictures from Nextcloud onto the stick (issue #25):
# the .jpg/.jpeg files of $SRC become an exact copy in $DEST (new ones are
# fetched, deleted ones removed; other files on the stick stay untouched).
#
# Deletion guard: if a run would delete more than $MAX_DELETE pictures (e.g.
# the share was emptied or removed), it aborts BEFORE changing anything.
# --max-delete is passed to rclone as well, as a second safety net.
#
# Run by diashow-sync.service (timer: 2 min after boot, then hourly); offline
# it fails before touching the stick, the next run tries again.
set -euf  # -f: the filter patterns are not file names

SRC=${DIASHOW_SRC:-nc:bilder}
DEST=${DIASHOW_DEST:-/mnt/stick/bilder}
MAX_DELETE=${DIASHOW_MAX_DELETE:-20}
RCLONE=${RCLONE:-rclone}

# only pictures in the folder itself, any case (.jpg .JPG .jpeg ...)
FILTER="--include *.{jpg,jpeg} --ignore-case --max-depth 1"
NET="--contimeout 30s --timeout 2m --retries 3 --low-level-retries 5"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# shellcheck disable=SC2086  # options are meant to be split
if ! $RCLONE lsf "$SRC" --files-only $FILTER $NET >"$tmp/src"; then
    echo "error: cannot list $SRC (offline?) - stick unchanged" >&2
    exit 1
fi
mkdir -p "$DEST"
find "$DEST" -maxdepth 1 -type f \( -iname '*.jpg' -o -iname '*.jpeg' \) \
    -exec basename {} \; >"$tmp/dest"
sort -o "$tmp/src" "$tmp/src"
sort -o "$tmp/dest" "$tmp/dest"
gone=$(comm -13 "$tmp/src" "$tmp/dest" | wc -l)
new=$(comm -23 "$tmp/src" "$tmp/dest" | wc -l)
if [ "$gone" -gt "$MAX_DELETE" ]; then
    echo "error: would delete $gone of $(wc -l <"$tmp/dest") pictures" \
         "(more than $MAX_DELETE; $SRC has $(wc -l <"$tmp/src")) - aborted," \
         "stick unchanged" >&2
    exit 1
fi
# shellcheck disable=SC2086
if ! $RCLONE sync "$SRC" "$DEST" $FILTER $NET --max-delete "$MAX_DELETE" \
        --stats 0 -v; then
    echo "error: sync incomplete ($SRC -> $DEST), next run tries again" >&2
    exit 1
fi
echo "sync ok: $(wc -l <"$tmp/src") pictures ($new new, $gone deleted)"
