#!/usr/bin/env bash
# gpu-handoff.sh — move NVIDIA GPUs between the host OS and a KVM guest
# WITHOUT rebooting. Exclusive handoff, never sharing: a GPU belongs to
# the host OR the guest at any moment.
#
#   ./gpu-handoff.sh status                  # who owns what
#   ./gpu-handoff.sh to-vm   <vm> [gpu ...]  # host -> guest (live hotplug)
#   ./gpu-handoff.sh to-host <vm> [gpu ...]  # guest -> host
#
#   <gpu> = nvidia-smi index (0..3) or PCI address (21:00.0).
#   Omitting [gpu ...] means ALL NVIDIA VGA devices.
#
# Mechanism: libvirt <hostdev managed='yes'> handles the driver rebind
# (nvidia -> vfio-pci on attach, back on detach) including the IOMMU
# group. We only refuse when a host process holds the card open.
#
# Requires: amd_iommu=on iommu=pt active (see run-enable-iommu-passthrough.sh),
#           vfio-pci module loadable, run as root.
set -euo pipefail

VIRSH="virsh -c qemu:///system"

die() { echo "gpu-handoff: $*" >&2; exit 1; }
usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

# --- helpers ---------------------------------------------------------

nvidia_map() {   # prints "index bdf" per NVIDIA GPU, e.g. "0 0000:21:00.0"
    nvidia-smi --query-gpu=index,pci.bus_id --format=csv,noheader |
        sed 's/00000000:/0000:/; s/, / /'
}

gpu_bdfs() {     # all NVIDIA VGA functions on the host, e.g. 0000:21:00.0
    lspci -Dn | awk '$2 ~ /^0300:/ && $3 ~ /^10de:/ {print "0000:"$1}'
}

resolve() {      # arg -> BDF (accepts index or short/long PCI addr)
    local g="$1"
    if [[ "$g" =~ ^[0-9]+$ ]]; then
        nvidia_map | awk -v i="$g" '$1==i {print $2; found=1} END{exit !found}' \
            || die "no NVIDIA GPU at index $g"
    else
        [[ "$g" =~ ^0000: ]] || g="0000:$g"
        lspci -Dn | grep -q "^${g#0000:} " || die "no PCI device $g"
        echo "$g"
    fi
}

hostdev_xml() {  # <hostdev> for a BDF covering .0 AND its .1 function
    local bdf="$1" bus slot
    bus=$((16#${bdf:5:2})); slot=$((16#${bdf:8:2}))
    for fn in 0 1; do
        [ -d "/sys/bus/pci/devices/0000:$(printf '%02x:%02x.%d' "$bus" "$slot" "$fn")" ] || continue
        printf '<hostdev mode="subsystem" type="pci" managed="yes">\n'
        printf '  <source><address domain="0x0000" bus="0x%02x" slot="0x%02x" function="0x%d"/></source>\n' \
            "$bus" "$slot" "$fn"
        printf '</hostdev>\n'
    done
}

busy_check() {   # refuse if a process holds this card's /dev nodes open
    local bdf="$1" idx
    idx=$(nvidia_map | awk -v b="$bdf" '$2==b {print $1}')
    [ -n "$idx" ] || return 0          # not nvidia-bound -> nothing to hold
    local holders
    holders=$(lsof -t /dev/nvidia"$idx" 2>/dev/null || true)
    [ -z "$holders" ] || die "GPU $idx ($bdf) is in use by pid(s): $(echo $holders | tr '\n' ' ')
       stop them first (e.g. LM Studio, CUDA jobs) — card must be idle to hand off"
}

driver_of() { basename "$(readlink /sys/bus/pci/devices/"$1"/driver 2>/dev/null || echo none)"; }

# --- commands --------------------------------------------------------

cmd_status() {
    printf '%-14s %-10s %s\n' "PCI" "DRIVER" "GUEST"
    for d in /sys/bus/pci/devices/*; do
        local bdf=${d##*/}
        lspci -nns "${bdf#0000:}" 2>/dev/null | grep -q '10de:' || continue
        local guest="host"
        for vm in $($VIRSH list --all --name); do
            [ -z "$vm" ] && continue
            $VIRSH dumpxml --inactive "$vm" 2>/dev/null \
                | grep -q "bus='0x${bdf:5:2}' slot='0x${bdf:8:2}'" && guest="$vm"
        done
        printf '%-14s %-10s %s\n' "${bdf#0000:}" "$(driver_of "$bdf")" "$guest"
    done
}

cmd_to_vm() {
    local vm="$1"; shift
    $VIRSH dominfo "$vm" >/dev/null 2>&1 || die "no such domain: $vm"
    local running=0
    [ "$($VIRSH domstate "$vm")" = "running" ] && running=1

    local gpu
    for gpu in "$@"; do
        local bdf; bdf=$(resolve "$gpu")
        local short=${bdf#0000:}
        [ "$(driver_of "$bdf")" = "nvidia" ] || [ "$running" = 0 ] || \
            echo "note: $short driver is '$(driver_of "$bdf")' (attaching anyway)"
        busy_check "$bdf"
        hostdev_xml "$bdf" > /tmp/gpu-handoff-$$.xml
        if [ "$running" = 1 ]; then
            $VIRSH attach-device "$vm" /tmp/gpu-handoff-$$.xml --live --config
        else
            $VIRSH attach-device "$vm" /tmp/gpu-handoff-$$.xml --config
        fi
        echo "==> $short (+audio fn) -> $vm"
    done
    rm -f /tmp/gpu-handoff-$$.xml
    [ "$running" = 1 ] && echo "    guest may need the NVIDIA driver to notice hot-add; check nvidia-smi inside"
    return 0
}

cmd_to_host() {
    local vm="$1"; shift
    $VIRSH dominfo "$vm" >/dev/null 2>&1 || die "no such domain: $vm"
    local running=0
    [ "$($VIRSH domstate "$vm")" = "running" ] && running=1

    local gpu
    for gpu in "$@"; do
        local bdf; bdf=$(resolve "$gpu")
        hostdev_xml "$bdf" > /tmp/gpu-handoff-$$.xml
        if [ "$running" = 1 ]; then
            $VIRSH detach-device "$vm" /tmp/gpu-handoff-$$.xml --live --config
        else
            $VIRSH detach-device "$vm" /tmp/gpu-handoff-$$.xml --config
        fi
        echo "==> ${bdf#0000:} detached from $vm; libvirt rebinds host driver (managed=yes)"
        echo "    verify: driver_of -> $(driver_of "$bdf")   nvidia-smi should list it"
    done
    rm -f /tmp/gpu-handoff-$$.xml
}

# --- main ------------------------------------------------------------

cmd="${1:-}"
case "$cmd" in
    -h|--help|help) usage; exit 0 ;;
esac
[ "$(id -u)" = 0 ] || die "must run as root (try --help)"
shift || true
case "$cmd" in
    status)  cmd_status ;;
    to-vm)   [ $# -ge 1 ] || die "to-vm needs <vm>"; vm="$1"; shift
             [ $# -ge 1 ] || set -- $(gpu_bdfs | sed 's/0000://')
             cmd_to_vm "$vm" "$@" ;;
    to-host) [ $# -ge 1 ] || die "to-host needs <vm>"; vm="$1"; shift
             [ $# -ge 1 ] || set -- $(gpu_bdfs | sed 's/0000://')
             cmd_to_host "$vm" "$@" ;;
    *) sed -n '2,16p' "$0"; exit 1 ;;
esac
