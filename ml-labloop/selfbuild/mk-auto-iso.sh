#!/usr/bin/env bash
# mk-auto-iso.sh — repack the Mint ISO into a fully unattended
# "automatic-ubiquity" installer: no questions, user 'lab', timezone
# UTC, qemu-guest-agent + spice-vdagent installed during setup,
# auto-reboot at the end.
#
#   ./selfbuild/mk-auto-iso.sh
#
# in : ../dist/linuxmint-22.3-xfce-64bit.iso (hardlink to the ISO cache)
# out: ../dist/linuxmint-22.3-xfce-64bit-auto.iso
#
# The stock ISO is cached under ~/.cache/labloop/iso/ so wiping dist/ (or
# re-exporting the repo) doesn't force another 2.7 GB download.
# The output ISO is picked up automatically by 00-build-lab-template.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

ISO_NAME="linuxmint-22.3-xfce-64bit.iso"
SRC="dist/$ISO_NAME"
DST="dist/linuxmint-22.3-xfce-64bit-auto.iso"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

ISO_URL="https://pub.linuxmint.io/stable/22.3/$ISO_NAME"
SUMS_URL="https://pub.linuxmint.io/stable/22.3/sha256sum.txt"
ISO_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/labloop/iso"

# dist/ doesn't exist in a fresh export — create on demand
mkdir -p dist "$ISO_CACHE"

# verify_iso <path> — check against Mint's published sha256 when the sums
# file is fetchable/cached. A definitive mismatch is fatal (return 1);
# unreachable sums only warn, they don't block the build.
verify_iso() {
    if [ ! -f "$ISO_CACHE/sha256sum.txt" ]; then
        curl -fsSL -o "$ISO_CACHE/sha256sum.txt" "$SUMS_URL" 2>/dev/null || {
            echo "    WARN: $SUMS_URL unreachable — checksum not verified" >&2
            return 0
        }
    fi
    grep -F "$ISO_NAME" "$ISO_CACHE/sha256sum.txt" >/dev/null || {
        echo "    WARN: $ISO_NAME not listed in sha256sum.txt — not verified" >&2
        return 0
    }
    ( cd "$(dirname "$1")" \
        && grep -F "$ISO_NAME" "$ISO_CACHE/sha256sum.txt" | sha256sum -c - )
}

command -v xorriso >/dev/null || {
    read -rp "xorriso missing — install it? [Y/n] " a
    case "$a" in [Nn]*) exit 1;; esac
    sudo apt-get install -y xorriso
}
if [ ! -f "$SRC" ]; then
    if [ -f "$ISO_CACHE/$ISO_NAME" ]; then
        if verify_iso "$ISO_CACHE/$ISO_NAME"; then
            echo "==> using cached ISO ($ISO_CACHE/$ISO_NAME)"
        else
            echo "    cached ISO failed checksum — discarding" >&2
            rm -f "$ISO_CACHE/$ISO_NAME"
        fi
    fi
    if [ ! -f "$ISO_CACHE/$ISO_NAME" ]; then
        echo "==> downloading Mint ISO -> $ISO_CACHE"
        curl -fL --progress-bar -o "$ISO_CACHE/$ISO_NAME.part" "$ISO_URL"
        mv "$ISO_CACHE/$ISO_NAME.part" "$ISO_CACHE/$ISO_NAME"
        verify_iso "$ISO_CACHE/$ISO_NAME" || {
            rm -f "$ISO_CACHE/$ISO_NAME"; exit 1
        }
    fi
    # hardlink keeps a single on-disk copy; fall back to copy across fs
    ln "$ISO_CACHE/$ISO_NAME" "$SRC" 2>/dev/null \
        || cp --reflink=auto "$ISO_CACHE/$ISO_NAME" "$SRC"
fi

echo "==> extracting $SRC"
xorriso -osirrox on -indev "$SRC" -extract / "$WORK/iso" >/dev/null
chmod -R u+w "$WORK/iso"

echo "==> adding preseed"
cat > "$WORK/iso/preseed.cfg" <<'SEED'
### identity — the installer must create user 'lab'
d-i passwd/user-fullname string lab
d-i passwd/username string lab
d-i passwd/user-password password lab
d-i passwd/user-password-again password lab
d-i user-setup/encrypt-home boolean false

### locale / keyboard / clock (UTC — lab convention)
d-i debian-installer/locale string en_US.UTF-8
d-i console-setup/ask_detect boolean false
d-i console-setup/layoutcode string us
d-i keyboard-configuration/layout select English (US)
d-i keyboard-configuration/layoutcode string us
d-i keyboard-configuration/variantcode string
d-i keyboard-configuration/modelcode string pc105
d-i keyboard-configuration/xkb-l10n/layoutcode string us
d-i keyboard-configuration/store_defaults_in_debconf_db boolean true
d-i time/zone string Etc/UTC
d-i clock-setup/utc boolean true
d-i clock-setup/ntp boolean true

### unattended: auto partitioning (blank disk), no reboot prompt
ubiquity ubiquity/reboot boolean true

### partitioning: erase disk, guided atomic recipe, answer every
### confirmation the partitioner would otherwise show
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman/default_filesystem string ext4
d-i partman-partitioning/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true
d-i partman-lvm/device_remove_lvm boolean true
d-i partman-lvm/confirm boolean true
d-i partman-md/device_remove_md boolean true

### "Install multimedia codecs" checkbox -> yes
ubiquity ubiquity/use_nonfree boolean true
ubiquity ubiquity/install_nonfree boolean true

### never let a package-phase debconf question block the install —
### and pre-answer the MS core fonts EULA (classic unattended-install
### freezer pulled in by the codecs bundle)
d-i debconf/priority select critical
ttf-mscorefonts-installer msttcorefonts/accepted-mscorefonts-eula boolean true
ttf-mscorefonts-installer msttcorefonts/present-mscorefonts-eula note

### no langpack downloads mid-install: en_US is already complete and
### a stalled fetch deadlocks the package queue the same way
d-i pkgsel/language-packs string
d-i pkgsel/install-language-support boolean false

### no in-install full upgrade — create-lab-template provisions with
### apt itself; a mid-install dist-upgrade is a huge fetch+hang surface
### (observed: build wedged 30+min, zero disk/net I/O, input dead)
d-i pkgsel/upgrade select none

### install the guest agent during setup so the build can proceed
### hands-free on first boot
ubiquity ubiquity/success_command string \
    in-target apt-get install -y qemu-guest-agent spice-vdagent
SEED

echo "==> patching boot configs for unattended boot"
# grub.cfg (UEFI): Mint's kernel lines end in ' --' — inject the
# unattended flags BEFORE it. The stock file has NO timeout line, so
# the menu waits forever; add one.
for cfg in "$WORK/iso/boot/grub/grub.cfg" "$WORK/iso/boot/grub/loopback.cfg"; do
    [ -f "$cfg" ] || continue
    sed -i '1i set default=0\nset timeout=2' "$cfg"
    sed -i '/^[[:space:]]*linux[[:space:]]/ s| --[[:space:]]*$| automatic-ubiquity noprompt file=/cdrom/preseed.cfg keyboard-configuration/layoutcode=us console-setup/layoutcode=us debian-installer/locale=en_US.UTF-8 debconf/priority=critical console=ttyS0,115200n8 --|' "$cfg"
done
# isolinux.cfg (BIOS fallback): same injection on APPEND lines,
# timeout is in deciseconds
for cfg in "$WORK"/iso/isolinux/*.cfg "$WORK"/iso/syslinux/*.cfg; do
    [ -f "$cfg" ] || continue
    sed -i 's/^timeout .*/timeout 20/' "$cfg"
    sed -i '/^[[:space:]]*append[[:space:]]/ s| --[[:space:]]*$| automatic-ubiquity noprompt file=/cdrom/preseed.cfg keyboard-configuration/layoutcode=us console-setup/layoutcode=us debian-installer/locale=en_US.UTF-8 debconf/priority=critical console=ttyS0,115200n8 --|I' "$cfg"
done
grep -l "automatic-ubiquity" "$WORK/iso/boot/grub/grub.cfg" >/dev/null \
    || { echo "ERROR: grub.cfg patch did not apply" >&2; exit 1; }

echo "==> repacking -> $DST"
# reuse the original ISO's exact boot record parameters — the report
# emits ready-to-use mkisofs args (with quoting), eval them verbatim
MKISO="$(xorriso -indev "$SRC" -report_el_torito as_mkisofs 2>/dev/null | grep -v '^__' | tr '\n' ' ')"
eval "set -- $MKISO"
xorriso -as mkisofs "$@" -o "$DST" "$WORK/iso" >/dev/null

ls -lh "$DST"
echo "==> done. 00-build-lab-template.sh will prefer this ISO automatically."
