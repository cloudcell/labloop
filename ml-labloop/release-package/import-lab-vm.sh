#!/usr/bin/env bash
# import-lab-vm.sh <image.qcow2> [name] — install the labloop lab VM.
#
# Downloads: you fetched lab-template.qcow2 separately (e.g. from
# HuggingFace). This script copies it into the libvirt image store,
# defines the domain (UEFI, QXL+SPICE, guest-agent channel), boots it,
# applies per-instance credentials, and runs the in-guest readiness
# check + security battery.
#
# Requirements: Linux host with KVM, libvirt (qemu:///system), OVMF,
# python3, ~16G RAM free for the guest. virt-manager recommended for
# the console window.
#
# usage: ./import-lab-vm.sh ~/Downloads/lab-template.qcow2 [name]
set -euo pipefail

IMG="${1:-}"; OWNER="${2:-me}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
die()  { echo "import-lab-vm: $*" >&2; exit 1; }
note() { echo; echo "==> $*"; }

[ -n "$IMG" ] && [ -f "$IMG" ] \
    || die "usage: $0 <lab-template.qcow2> [name]   (name: lowercase alnum/dash)"
case "$OWNER" in *[!a-z0-9-]*) die "bad name '$OWNER'" ;; esac
VM="lab-vm-${OWNER}"

command -v virsh  >/dev/null || die "need virsh (libvirt-clients)"
command -v python3 >/dev/null || die "need python3"
[ -e /dev/kvm ] || die "no /dev/kvm — enable virtualization / install qemu-kvm"
id -nG | grep -qw libvirt || exec sg libvirt -c "$0 $IMG $OWNER"
virsh -c qemu:///system list >/dev/null 2>&1 || die "cannot reach qemu:///system (libvirtd running?)"
virsh -c qemu:///system dominfo "$VM" >/dev/null 2>&1 && die "VM '$VM' already exists"
virsh -c qemu:///system net-info default >/dev/null 2>&1 \
    || die "libvirt 'default' NAT network missing — 'virsh net-start default'"

# ------------------------------------------------------------------
note "installing image -> /var/lib/libvirt/images/$VM.qcow2"
sudo cp --sparse=always "$IMG" "/var/lib/libvirt/images/$VM.qcow2"
sudo chmod 0644 "/var/lib/libvirt/images/$VM.qcow2"

# ------------------------------------------------------------------
note "defining domain $VM (UEFI / QXL / SPICE / guest-agent)"
TPM_XML=''
command -v swtpm >/dev/null && TPM_XML='<tpm model="tpm-crb"><backend type="emulator" version="2.0"/></tpm>'
cat > "$WORK/dom.xml" <<EOF
<domain type='kvm'>
  <name>$VM</name>
  <memory unit='KiB'>16777216</memory>
  <vcpu placement='static'>8</vcpu>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc'>hvm</type>
    <boot dev='hd'/>
  </os>
  <features><acpi/><apic/><vmport state='off'/></features>
  <cpu mode='host-passthrough' check='none' migratable='on'/>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>destroy</on_crash>
  <devices>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' discard='unmap'/>
      <source file='/var/lib/libvirt/images/$VM.qcow2'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <controller type='usb' index='0' model='ich9-ehci1'/>
    <controller type='usb' index='0' model='ich9-uhci1'><master startport='0'/></controller>
    <controller type='usb' index='0' model='ich9-uhci2'><master startport='2'/></controller>
    <controller type='usb' index='0' model='ich9-uhci3'><master startport='4'/></controller>
    <controller type='ide' index='0'/>
    <controller type='virtio-serial' index='0'/>
    <controller type='pci' index='0' model='pci-root'/>
    <interface type='network'>
      <source network='default'/>
      <model type='virtio'/>
    </interface>
    <serial type='pty'><target type='isa-serial' port='0'><model name='isa-serial'/></target></serial>
    <console type='pty'><target type='serial' port='0'/></console>
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0'/>
    </channel>
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <input type='tablet' bus='usb'/>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    $TPM_XML
    <graphics type='spice' autoport='yes'>
      <listen type='address'/>
      <image compression='off'/>
      <clipboard copypaste='no'/>
    </graphics>
    <sound model='ich6'/>
    <audio id='1' type='spice'/>
    <video>
      <model type='qxl' ram='262144' vram='131072' vgamem='32768' heads='1' primary='yes'>
        <resolution x='2560' y='1440'/>
      </model>
    </video>
    <redirdev bus='usb' type='spicevmc'/>
    <redirdev bus='usb' type='spicevmc'/>
    <memballoon model='virtio'/>
  </devices>
</domain>
EOF
virsh -c qemu:///system define "$WORK/dom.xml" >/dev/null

# ------------------------------------------------------------------
# guest-agent exec helper
cat > "$WORK/qga.py" <<'PYEOF'
import base64, json, subprocess, sys, time
dom, mode, args = sys.argv[1], sys.argv[2], sys.argv[3:]
def qga(payload):
    r = subprocess.run(["virsh","-c","qemu:///system","qemu-agent-command",
                        dom, json.dumps(payload)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["return"]
if mode == "ping":
    try: qga({"execute":"guest-ping"}); print("agent-ok")
    except Exception: sys.exit(1)
    sys.exit(0)
if mode == "exec":
    user = None
    if args[:1] == ["--as"]:
        user, args = args[1], args[2:]
    cmd = " ".join(args)
    path, argv = ("/bin/su", ["-", user, "-c", cmd]) if user \
                 else ("/bin/sh", ["-c", cmd])
    pid = qga({"execute":"guest-exec","arguments":
               {"path":path,"arg":argv,"capture-output":True}})["pid"]
    for _ in range(300):
        time.sleep(2)
        st = qga({"execute":"guest-exec-status","arguments":{"pid":pid}})
        if st.get("exited"):
            sys.stdout.write(base64.b64decode(st.get("out-data","")or"").decode("utf-8","replace"))
            sys.stderr.write(base64.b64decode(st.get("err-data","")or"").decode("utf-8","replace"))
            sys.exit(st.get("exitcode",1))
    sys.exit("guest-exec timeout")
if mode == "write":  # write <dst> <b64-file>
    dst, b64 = args
    h = qga({"execute":"guest-file-open","arguments":{"path":dst,"mode":"w+"}})
    qga({"execute":"guest-file-write","arguments":{"handle":h,"buf-b64":b64}})
    qga({"execute":"guest-file-close","arguments":{"handle":h}})
PYEOF
qga()     { python3 "$WORK/qga.py" "$VM" exec "$@"; }
qga_lab() { python3 "$WORK/qga.py" "$VM" exec --as lab "$@"; }
qga_push(){ python3 "$WORK/qga.py" "$VM" write "$2" "$(base64 -w0 "$1")"; }

# ------------------------------------------------------------------
note "booting $VM (first boot after sealing can take a few minutes)"
virsh -c qemu:///system start "$VM" >/dev/null
for _ in $(seq 60); do python3 "$WORK/qga.py" "$VM" ping >/dev/null 2>&1 && break; sleep 5; done
python3 "$WORK/qga.py" "$VM" ping >/dev/null 2>&1 || die "guest agent never came up"
for _ in $(seq 60); do qga true 2>/dev/null && break; sleep 5; done
qga true 2>/dev/null || die "guest-exec not usable in $VM"

# ------------------------------------------------------------------
note "hostname = $VM"
qga "hostnamectl set-hostname '$VM'"

note "credentials: lab/lab + change-password nag at every login"
if qga "passwd -S lab" 2>/dev/null | grep -q ' L '; then
    pwchars='23456789abcdefghjkmnpqrstuvwxyz'
    tmp_pw="lab-$(tr -dc "$pwchars" </dev/urandom | head -c 4 || true)-$(tr -dc "$pwchars" </dev/urandom | head -c 4 || true)"
else
    tmp_pw="lab"
fi
qga "echo 'lab:$tmp_pw' | chpasswd && passwd -l exp >/dev/null"
printf 'one-time password for %s (user lab): %s\n' "$VM" "$tmp_pw" > "$WORK/otp"
sudo install -m 0400 -o "$(id -un)" -g "$(id -gn)" "$WORK/otp" \
    "/var/lib/libvirt/images/$VM.otp"
echo "    otp written: /var/lib/libvirt/images/$VM.otp (mode 400)"

# first-login helper: trust desktop launchers from inside the GUI
# session (XFCE needs metadata::xfce-exe-checksum = sha256), then run
# passwd in a retry loop. Self-removes when the password changes —
# the stamp is the lastchg field captured right now, in UTC.
cat > "$WORK/lab-first-login" <<'EOF'
#!/bin/bash
for f in "$HOME"/Desktop/*.desktop; do [ -e "$f" ] || continue
    gio set "$f" metadata::trusted true 2>/dev/null
    set -- $(sha256sum "$f" 2>/dev/null)
    gio set "$f" metadata::xfce-exe-checksum "$1" 2>/dev/null
done
stamp=$(cat "$HOME/.config/lab-pw-stamp" 2>/dev/null)
set -- $(passwd -S "$USER" 2>/dev/null); lastchg=$3
if [ -n "$stamp" ] && [ "$lastchg" != "$stamp" ]; then
    rm -f "$HOME/.config/autostart/lab-first-login.desktop" "$HOME/.config/lab-pw-stamp"
    exit 0
fi
echo "Please set a new login password (current password is your OTP)."
echo
until passwd; do
    echo
    echo "-- password not changed (wrong current password, or too short)."
    echo "   Try again, or close this window to skip for now."
    echo
done
rm -f "$HOME/.config/autostart/lab-first-login.desktop" "$HOME/.config/lab-pw-stamp"
echo
echo "Password changed — this prompt will not appear again."
sleep 3
EOF
qga_push "$WORK/lab-first-login" /usr/local/bin/lab-first-login
cat > "$WORK/lab-first-login.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Set lab password
Exec=x-terminal-emulator -e /usr/local/bin/lab-first-login
X-GNOME-Autostart-enabled=true
EOF
qga "chmod 0755 /usr/local/bin/lab-first-login && \
     mkdir -p /home/lab/.config/autostart && \
     set -- \$(passwd -S lab) && echo \$3 > /home/lab/.config/lab-pw-stamp && \
     chown lab:lab /home/lab/.config/lab-pw-stamp"
qga_push "$WORK/lab-first-login.desktop" /home/lab/.config/autostart/lab-first-login.desktop
qga "chown lab:lab /home/lab/.config/autostart/lab-first-login.desktop"

note "per-instance agent identity: wipe opencode/vscodium state"
qga "rm -rf /home/lab/.local/share/opencode /home/lab/.cache/opencode \
     /home/lab/.config/VSCodium/machineid /home/lab/.cache/sessions"

# ------------------------------------------------------------------
note "waiting for zone containers"
exp_probe='sudo -n -u exp env HOME=/home/exp XDG_RUNTIME_DIR=/run/user/$(id -u exp) podman ps --format "{{.Names}}"'
for i in $(seq 60); do
    qga_lab "podman ps --format '{{.Names}}'" 2>/dev/null | grep -q lab-cnt-mcp \
        && qga "$exp_probe" 2>/dev/null | grep -q lab-cnt-exp \
        && break
    [ $((i % 6)) -eq 0 ] && echo "    ... still waiting ($((i*5))s)"
    sleep 5
done
qga_lab "podman ps --format '{{.Names}}'" 2>/dev/null | grep -q lab-cnt-mcp \
    && qga "$exp_probe" 2>/dev/null | grep -q lab-cnt-exp \
    || echo "    (containers still starting — the readiness check below will keep waiting)"

note "check-lab-ready.sh inside $VM"
qga_lab "bash /home/lab/Desktop/check-lab-ready.sh" || true

cat <<EOF

==> $VM is up.

    FIRST LOGIN:  user 'lab' password is $tmp_pw
    — a terminal pops up asking you to set a new one
      (type '$tmp_pw' once as the current password, then your new one).
      also stored at /var/lib/libvirt/images/$VM.otp (mode 400)
    (exp is locked — it is a service account, not a login.)

    Open it in virt-manager, or rerun the check inside:
      bash ~/Desktop/check-lab-ready.sh
EOF
