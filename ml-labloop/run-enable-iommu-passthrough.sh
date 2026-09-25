#!/usr/bin/env bash
# run-once-and-delete.sh — enable IOMMU on the host for GPU passthrough.
#
# Swaps amd_iommu=off -> amd_iommu=on iommu=pt in GRUB, updates grub,
# then deletes itself. Reboot is left to you (GRUB keeps the previous
# entry selectable as a fallback if IOMMU-on breaks something).
#
# iommu=pt = passthrough mode: non-VM devices bypass translation
# (identity DMA), so most IOMMU-related device quirks do not apply.
set -euo pipefail

GRUB=/etc/default/grub

grep -q 'amd_iommu=off' "$GRUB" || {
    echo "amd_iommu=off not found in $GRUB — already changed?" >&2
    exit 1
}

cp -a "$GRUB" "$GRUB.bak-labloop-iommu"
sed -i 's/amd_iommu=off/amd_iommu=on iommu=pt/' "$GRUB"
grep -q 'amd_iommu=on iommu=pt' "$GRUB"
update-grub

echo "==> IOMMU enabled (amd_iommu=on iommu=pt)."
echo "    Backup: $GRUB.bak-labloop-iommu"
echo "    REBOOT to apply. Post-boot verify:"
echo "      dmesg | grep -iE 'amd-vi|iommu'"
echo "      ls /sys/kernel/iommu_groups/ | wc -l   # want > 0"
# rm -- "$0"
