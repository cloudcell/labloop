#!/usr/bin/env bash
# 11-release-template.sh — release a template image: move one
# *.qcow2 + its .sha256 sidecar from the private trials bucket to the
# public release bucket.
#
#   ./11-release-template.sh [image-name] [--keep]
#
# With no image name, lists the *.qcow2 objects in the source bucket
# and shows a numbered menu. "Move" = copy both objects to the
# destination, verify they landed, then remove them from the source
# (--keep copies without deleting).
#
# Requires: hf CLI authenticated with read+write on both buckets.
#
# env:   SRC_BUCKET=...   default hf://buckets/LabLoopCommunity/lab-trials
#        DST_BUCKET=...   default hf://buckets/cloudcell/LabLoop
#        HF_CLI=...       path to hf binary if not on PATH
set -euo pipefail
cd "$(dirname "$0")"

SRC="${SRC_BUCKET:-hf://buckets/LabLoopCommunity/lab-trials}"
DST="${DST_BUCKET:-hf://buckets/cloudcell/LabLoop}"
KEEP=0
NAME=""

die()  { echo "11-release: $*" >&2; exit 1; }
note() { echo; echo "==> $*"; }

for a in "$@"; do
    case "$a" in
        --keep|-k) KEEP=1 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *.qcow2)   NAME="$a" ;;
        *) die "unknown argument: $a" ;;
    esac
done

# ------------------------------------------------------------------
# hf CLI — PATH, HF_CLI override, or the private venv script 10 uses
HF="${HF_CLI:-}"
[ -z "$HF" ] && HF="$(command -v hf || true)"
if [ -z "$HF" ]; then
    HFENV="$HOME/.cache/labloop-hfenv"
    [ -x "$HFENV/bin/hf" ] || {
        note "bootstrapping hf CLI into $HFENV"
        python3 -m venv "$HFENV"
        "$HFENV/bin/pip" -q install -U huggingface_hub
    }
    HF="$HFENV/bin/hf"
fi
"$HF" auth whoami >/dev/null 2>&1 \
    || die "not logged in — run: $HF auth login"
WHO="$("$HF" auth whoami 2>/dev/null | sed -n 's/.*user:[[:space:]]*//p' | head -n1)"
[ -n "$WHO" ] || WHO="the authenticated hf account"

bucket_files() {  # bare object names in a bucket, one per line
    "$HF" buckets ls "$1" 2>/dev/null \
        | grep -oE '[^[:space:]]+\.(qcow2|sha256)' | sort -u
}

# ------------------------------------------------------------------
# pick the image — argument wins; otherwise a numbered menu
note "listing $SRC"
mapfile -t images < <(bucket_files "$SRC" | grep '\.qcow2$' || true)
[ "${#images[@]}" -gt 0 ] || die "no *.qcow2 objects in $SRC"

if [ -z "$NAME" ]; then
    echo
    i=1
    for f in "${images[@]}"; do printf "    %d) %s\n" "$i" "$f"; i=$((i+1)); done
    read -rp "    release which image? [1-${#images[@]}] " sel
    [[ "$sel" =~ ^[0-9]+$ ]] && [ "$sel" -ge 1 ] && [ "$sel" -le "${#images[@]}" ] \
        || die "bad selection"
    NAME="${images[$((sel-1))]}"
fi
grep -qxF "$NAME" <(printf '%s\n' "${images[@]}") \
    || die "$NAME not found in $SRC"

SHA="$NAME.sha256"
have_sha=0
bucket_files "$SRC" | grep -qxF "$SHA" && have_sha=1
[ "$have_sha" -eq 1 ] \
    || echo "    WARNING: no $SHA sidecar in source — image will ship unchecksummed"

# ------------------------------------------------------------------
# release is opt-in, every time — default answer is NO
note "image:     $NAME$([ "$have_sha" -eq 1 ] && echo " (+ .sha256)" || echo " (no checksum!)")"
note "account:   $WHO"
note "from:      $SRC"
note "to:        $DST"
[ "$KEEP" -eq 1 ] && note "mode:      copy (source kept)" || note "mode:      move (source deleted after copy)"
read -rp "    release $NAME to $DST? [y/N] " a
case "$a" in [Yy]*) ;; *) die "aborted — nothing released";; esac

# destination bucket must exist — create it if it doesn't
if ! "$HF" buckets info "${DST#hf://buckets/}" >/dev/null 2>&1; then
    read -rp "    bucket $DST does not exist — create it? [y/N] " a
    case "$a" in [Yy]*) ;; *) die "aborted";; esac
    "$HF" buckets create "${DST#hf://buckets/}"
fi

# ------------------------------------------------------------------
# copy — try bucket->bucket first; fall back to a temp round-trip
copy_obj() {  # $1=object name
    if "$HF" buckets cp "$SRC/$1" "$DST/$1" 2>/dev/null; then
        return 0
    fi
    echo "    direct bucket->bucket copy failed for $1 — falling back to download+upload" >&2
    local tmp; tmp="$(mktemp -d)"
    "$HF" buckets cp "$SRC/$1" "$tmp/$1" || { rm -rf "$tmp"; return 1; }
    "$HF" buckets cp "$tmp/$1" "$DST/$1" || { rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"
}

note "copying -> $DST"
copy_obj "$NAME"                        || die "copy failed: $NAME"
[ "$have_sha" -eq 1 ] && copy_obj "$SHA" || true

# verify both objects are present in the destination
note "verifying"
dst_files="$(bucket_files "$DST")"
grep -qxF "$NAME" <<<"$dst_files" || die "$NAME did not land in $DST"
if [ "$have_sha" -eq 1 ]; then
    grep -qxF "$SHA" <<<"$dst_files" || die "$SHA did not land in $DST"
fi
echo "    present in $DST: $NAME$([ "$have_sha" -eq 1 ] && echo " + $SHA")"

# ------------------------------------------------------------------
if [ "$KEEP" -eq 0 ]; then
    note "removing from $SRC"
    "$HF" buckets rm "$SRC/$NAME" || echo "    WARN: could not remove $NAME from source"
    if [ "$have_sha" -eq 1 ]; then
        "$HF" buckets rm "$SRC/$SHA" || echo "    WARN: could not remove $SHA from source"
    fi
fi

cat <<EOF

==> released: $DST/$NAME

    recipients:
      hf buckets cp $DST/$NAME .
      sha256sum -c $NAME.sha256
      ./import-lab-vm.sh $NAME <name>
EOF
