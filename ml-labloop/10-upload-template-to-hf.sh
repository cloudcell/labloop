#!/usr/bin/env bash
# 10-upload-template-to-hf.sh — seal the template, flatten+compress it,
# and push it to the private Hugging Face bucket.
#
#   1. confirm publication ([y/N]) — shows the hf account + bucket
#   2. ./01-prepare-template-for-cloning.sh — publish-mode seal:
#      locks lab+exp passwords, then sysprep + snp-vN snapshot
#      (skip with SKIP_SEAL=1 only if you already sealed that way)
#   3. qemu-img convert -c — drop internal snapshots, zlib-compress
#   4. upload <name>-<ISO-UTC>.qcow2 + .sha256 to the bucket
#
# Requires: hf CLI authenticated (`hf auth login`) with write access
# to the bucket, sudo for sealing + reading the sealed image.
#
# usage: ./10-upload-template-to-hf.sh [template-domain] [bucket]
# env:   SKIP_SEAL=1  skip shutdown+seal (image must already be sealed)
#        DISTDIR=...  where the artifact lands (default: ./dist)
#        HF_CLI=...   path to hf binary if not on PATH
set -euo pipefail
cd "$(dirname "$0")"

BUCKET="${2:-hf://buckets/LabLoopCommunity/lab-trials}"
DISTDIR="${DISTDIR:-$PWD/dist}"
VIRSH="virsh -c qemu:///system"
die()  { echo "10-upload: $*" >&2; exit 1; }
note() { echo; echo "==> $*"; }

# default template: newest lab-template-<UTC-ts>; fall back to any
# lab-template-* (e.g. lab-template-v1) when no dated build exists
pick_template() {
    local names ts
    names="$($VIRSH list --all --name 2>/dev/null | grep '^lab-template-' || true)"
    ts="$(grep -E '^lab-template-[0-9]{8}T[0-9]{4}Z$' <<<"$names" | sort | tail -n1)"
    [ -n "$ts" ] && { echo "$ts"; return; }
    tail -n1 <<<"$names"
}

id -nG | grep -qw libvirt || exec sg libvirt -c "$0 $*"
VM="${1:-$(pick_template)}"
[ -n "$VM" ] || die "no lab-template-* domain found — build one with ./00-build-lab-template.sh"
$VIRSH dominfo "$VM" >/dev/null 2>&1 || die "domain '$VM' not found"
IMG="/var/lib/libvirt/images/$VM.qcow2"

# ------------------------------------------------------------------
# hf CLI — PATH, HF_CLI override, or a private venv we bootstrap once
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
    || die "not logged in — run: $HF auth login (token needs write on $BUCKET)"
WHO="$("$HF" auth whoami 2>/dev/null | sed -n 's/.*user:[[:space:]]*//p' | head -n1)"
[ -n "$WHO" ] || WHO="the authenticated hf account"

# ------------------------------------------------------------------
# publication is opt-in, every time — default answer is NO
note "template:  $VM"
note "account:   $WHO"
note "bucket:    $BUCKET"
read -rp "    publish a sealed image of $VM to $BUCKET? [y/N] " a
case "$a" in [Yy]*) ;; *) die "aborted — nothing published";; esac

# ------------------------------------------------------------------
# publishing always locks lab+exp passwords inside the image first —
# the hash is offline-crackable by anyone who downloads it
if [ "${SKIP_SEAL:-0}" != "1" ]; then
    note "sealing $VM (publish mode: password lock + sysprep + snp-* snapshot)"
    LABLOOP_PUBLISH=1 ./01-prepare-template-for-cloning.sh "$VM"
else
    echo "    SKIP_SEAL=1 — hoping '$VM' was sealed with passwords locked"
fi
[ "$($VIRSH domstate "$VM")" = "shut off" ] || die "$VM must be shut off"

# ------------------------------------------------------------------
TS="$(date -u +%Y%m%dT%H%MZ)"
OUT="$DISTDIR/$VM-$TS.qcow2"
mkdir -p "$DISTDIR"

note "flatten + compress -> $OUT"
sudo qemu-img convert -c -O qcow2 "$IMG" "$OUT"
sudo chown "$(id -u):$(id -g)" "$OUT"

note "checksum"
(cd "$DISTDIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

note "upload -> $BUCKET"
"$HF" buckets cp "$OUT"        "$BUCKET/$(basename "$OUT")"
"$HF" buckets cp "$OUT.sha256" "$BUCKET/$(basename "$OUT").sha256"

cat <<EOF

==> published: $BUCKET/$(basename "$OUT")

    size:   $(du -h "$OUT" | cut -f1) (compressed qcow2, $(qemu-img info --output=json "$OUT" | python3 -c 'import json,sys;print(json.load(sys.stdin)["virtual-size"]//2**30)') GiB virtual)
    sha256: $(cut -d' ' -f1 "$OUT.sha256")

    recipients:
      hf buckets cp $BUCKET/$(basename "$OUT") .
      sha256sum -c $(basename "$OUT").sha256
      ./import-lab-vm.sh $(basename "$OUT") <name>
EOF
