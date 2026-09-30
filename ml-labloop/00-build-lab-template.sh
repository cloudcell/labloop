#!/usr/bin/env bash
# 00-build-lab-template.sh — step zero: build a lab template from the
# Mint ISO end-to-end (unattended install, provisioning, security
# battery, snapshot).
#
#   ./00-build-lab-template.sh [template-name]
#
# Uses the Mint ISO in dist/ (or LABLOOP_ISO). Creates
# lab-template-<UTC-ts> by default — a fresh domain, so an existing
# template is never at risk (passing an existing name RESUMES it).
# Seal with 01, clone with 02 — see footer.
set -euo pipefail
cd "$(dirname "$0")"

TS="$(date -u +%Y%m%dT%H%MZ)"
VM="${1:-lab-template-$TS}"
case "$VM" in
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    -*|*[!a-zA-Z0-9_.-]*) echo "00-build-lab-template: bad domain name '$VM'" >&2; exit 1 ;;
esac
# one EXIT trap: sudo keepalive + the transient cache http server
SUDO_KEEPALIVE=""; CACHE_HTTP_PID=""
cleanup() {
    [ -n "$SUDO_KEEPALIVE" ] && kill "$SUDO_KEEPALIVE" 2>/dev/null
    [ -n "$CACHE_HTTP_PID" ] && kill "$CACHE_HTTP_PID" 2>/dev/null
    return 0
}
trap cleanup EXIT
# ---- host prerequisites — offer to install what's missing ----------
# BEFORE the ISO step: mk-auto-iso.sh may need xorriso (sudo) and a long
# download — every prompt and the sudo timestamp must already be done.
echo "==> checking host prerequisites"
declare -A PKG=(
    [virsh]=libvirt-clients
    [virt-install]=virtinst
    [qemu-img]=qemu-utils
    [python3]=python3
    [curl]=curl
    [xorriso]=xorriso
    [virt-viewer]=virt-viewer
    [virt-manager]=virt-manager
    [virt-customize]=libguestfs-tools   # offline ESP fallback fix
)
missing=()
for cmd in "${!PKG[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("${PKG[$cmd]}")
done
[ -e /dev/kvm ] || missing+=(qemu-system-x86)
ls /usr/share/OVMF/OVMF_CODE*.fd /usr/share/ovmf/OVMF*.fd >/dev/null 2>&1 \
    || missing+=(ovmf)
systemctl is-active --quiet libvirtd 2>/dev/null || missing+=(libvirt-daemon-system)

# every interactive question — [Y/n] prompts AND the sudo password —
# happens HERE, before the unattended build starts. sudo -v primes the
# timestamp; the bounded keepalive refreshes it so nothing mid-run
# (apt, usermod, or anything downstream) asks again.
if ((${#missing[@]})) || ! id -nG | grep -qw libvirt; then
    sudo -v
    (for _ in $(seq 240); do sudo -n true 2>/dev/null || break; sleep 60; done) &
    SUDO_KEEPALIVE=$!
fi

if ((${#missing[@]})); then
    mapfile -t missing < <(printf '%s\n' "${missing[@]}" | sort -u)
    echo "    missing: ${missing[*]}"
    read -rp "    install them now? [Y/n] " a
    case "$a" in [Nn]*) echo "aborted — install them and rerun" >&2; exit 1;; esac
    sudo apt-get update -qq && sudo apt-get install -y "${missing[@]}"
    systemctl is-active --quiet libvirtd || sudo systemctl enable --now libvirtd
fi

if ! id -nG | grep -qw libvirt; then
    read -rp "    add $USER to the libvirt group? [Y/n] " a
    case "$a" in [Nn]*) echo "aborted — libvirt group required" >&2; exit 1;; esac
    sudo usermod -aG libvirt "$USER"
    echo "    added — effective immediately for child processes (sg)"
fi

# ---- artifact preflight — every byte the build needs must already be
# in ~/.cache/labloop BEFORE the ISO step. A missing artifact dies here
# at minute 0 with a gap list, not at minute 18 inside the guest.
# LABLOOP_OFFLINE=1 = verify-only, never fetch.
# (docs/e-plans/plan-20260930-1838Z--offline-build-prefetch.md)
./selfbuild/prefetch-cache.sh --ensure

# transient read-only artifact endpoint for the guest: the preseed's
# in-target apt, the image tarballs, wheelhouses and pkgs all pull from
# here instead of the internet. Bound to the stock libvirt NAT gateway
# — reachable only by virbr0 guests, dead the moment this script exits.
# Reuse a running endpoint ONLY if it serves this exact MANIFEST — an
# orphaned http.server from an earlier run answers the probe fine but
# can die mid-provisioning and leave guest curls hung for minutes.
CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/labloop"
if [ -f "$CACHE_ROOT/MANIFEST" ]; then
    if curl -fs -m 2 "http://192.168.122.1:8777/MANIFEST" 2>/dev/null \
       | cmp -s - "$CACHE_ROOT/MANIFEST"; then
        echo "==> cache endpoint already serving this cache on :8777"
    elif (exec 3<>"/dev/tcp/192.168.122.1/8777") 2>/dev/null; then
        echo "00-build-lab-template: :8777 is in use but does not serve" >&2
        echo "    our cache — kill the foreign listener and retry" >&2
        exit 1
    else
        python3 -m http.server 8777 --bind 192.168.122.1 -d "$CACHE_ROOT" \
            >/dev/null 2>&1 &
        CACHE_HTTP_PID=$!
        echo "==> cache endpoint: http://192.168.122.1:8777 (pid $CACHE_HTTP_PID)"
    fi
fi

# ---- ISO — all interaction is done; from here on it's unattended ----
# one entry point: use the unattended ISO; if it hasn't been generated
# yet, mk-auto-iso.sh builds it (downloading the stock ISO itself).
# LABLOOP_ISO=<path> overrides everything.
AUTO_ISO="$PWD/dist/linuxmint-22.3-xfce-64bit-auto.iso"
if [ -n "${LABLOOP_ISO:-}" ]; then
    ISO="$LABLOOP_ISO"
    [ -f "$ISO" ] || { echo "no ISO at $ISO" >&2; exit 1; }
else
    # regenerate the ISO when the generator is newer — the preseed is
    # baked in at repack time, so a stale ISO silently keeps old answers
    if [ ! -f "$AUTO_ISO" ] || [ "$AUTO_ISO" -ot selfbuild/mk-auto-iso.sh ]; then
        ./selfbuild/mk-auto-iso.sh
    fi
    ISO="$AUTO_ISO"
fi

echo "==> building template: $VM (iso: $(basename "$ISO"))"
case "$ISO" in
    *auto*) echo "    unattended install — no interaction needed" ;;
    *) echo "    the OS install step is manual — click through the Mint"
       echo "    installer when virt-viewer pops up, then it goes unattended." ;;
esac
LABLOOP_ISO="$ISO" ./create-lab-template "$VM"

cat <<FOOTER

==> template built: $VM

    next: ./01-prepare-template-for-cloning.sh $VM
          then clone:      LABLOOP_TEMPLATE=$VM ./02-create-vm-from-template.sh <owner>

    destroy when done:
      virsh destroy $VM; virsh undefine $VM --nvram --snapshots-metadata
      sudo rm /var/lib/libvirt/images/$VM.qcow2
FOOTER
