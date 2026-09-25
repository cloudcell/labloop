#!/usr/bin/env bash
# run-disable-iommu-passthrough.sh — reverse run-enable-iommu-passthrough.sh.
#
# Puts amd_iommu=off back on the GRUB cmdline (removing iommu=pt),
# updates grub. Reboot is left to you — passthrough keeps working until
# then.
#
# Surgically edits the cmdline rather than restoring the backup, so any
# unrelated GRUB changes made since the enable are preserved.
set -euo pipefail

GRUB=/etc/default/grub

grep -q 'amd_iommu=on' "$GRUB" || {
    echo "amd_iommu=on not found in $GRUB — IOMMU already disabled?" >&2
    exit 1
}

# warn if guests still reference passed-through hostdevs
for vm in $(virsh -c qemu:///system list --all --name 2>/dev/null); do
    [ -z "$vm" ] && continue
    virsh -c qemu:///system dumpxml --inactive "$vm" 2>/dev/null \
        | grep -q "hostdev.*pci" && \
        echo "WARN: VM '$vm' still has PCI hostdevs — detach them first" >&2
done

sed -i 's/amd_iommu=on iommu=pt/amd_iommu=off/; s/amd_iommu=on/amd_iommu=off/; s/ iommu=pt//' "$GRUB"
grep -q 'amd_iommu=off' "$GRUB"
update-grub

echo "==> IOMMU disabled (amd_iommu=off restored)."
echo "    Backup from enable still at: $GRUB.bak-labloop-iommu"
echo "    REBOOT to apply."
