#!/usr/bin/env bash
# 02-create-vm-from-template.sh <owner> — clone the sealed template
# into lab-vm-<owner>, boot it, set hostname = domain name, and run
# the desktop readiness check inside.
#
# Expects the template to be sealed (01-prepare-template-for-cloning.sh
# — virt-sysprep MUST have run since the last template change).
# Asks for your sudo password for virt-clone — run in a terminal.
#
# sa-02 naming: real users get lab-vm-<owner>; throwaways are
# lab-vm-tmp-N (pass e.g. tmp-1 as <owner>).
set -euo pipefail

VIRSH="virsh -c qemu:///system"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

die()  { echo "02-create-vm: $*" >&2; exit 1; }
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

name="${1:-}"
[ -n "$name" ] || die "usage: 02-create-vm-from-template.sh <owner>  (e.g. x, tmp-1)"
case "$name" in
    *[!a-z0-9-]*) die "name must be lowercase alnum/dash" ;;
esac
vm="lab-vm-${name}"

id -nG | grep -qw libvirt || exec sg libvirt -c "$0 $*"
TEMPLATE="${LABLOOP_TEMPLATE:-$(pick_template)}"
[ -n "$TEMPLATE" ] || die "no lab-template-* domain found — build one with ./00-build-lab-template.sh"
$VIRSH dominfo "$TEMPLATE" >/dev/null 2>&1 || die "template '$TEMPLATE' not found"
note "template: $TEMPLATE"
[ "$($VIRSH domstate "$TEMPLATE" 2>/dev/null)" = "shut off" ] \
    || die "template must be shut off — run ./01-prepare-template-for-cloning.sh"
$VIRSH dominfo "$vm" >/dev/null 2>&1 && die "VM '$vm' already exists"

# prime sudo NOW — virt-cat/virt-clone/virt-customize/otp-install all
# need it, and the last one comes after a ~10min boot wait; ask the
# password up front so the whole clone runs hands-off.
sudo -v
(for _ in $(seq 60); do sudo -n true 2>/dev/null || break; sleep 60; done) &
SUDO_KEEPALIVE=$!
trap 'kill "$SUDO_KEEPALIVE" 2>/dev/null; rm -rf "$WORK"' EXIT

# ------------------------------------------------------------------
# HARD GATE — refuse to clone an unscrubbed template.
# Proof A: /etc/machine-id inside the image reads "uninitialized"
#          (virt-sysprep's machine-id operation). Read offline.
# Proof B (fallback): a sealed snp-* snapshot exists — only
#          01-prepare creates those, after sysprep.
# ------------------------------------------------------------------
note "verifying $TEMPLATE is scrubbed"
mid="$(sudo virt-cat -d "$TEMPLATE" /etc/machine-id 2>/dev/null | tr -d '[:space:]' || true)"
if [ "$mid" = "uninitialized" ]; then
    echo "    machine-id = uninitialized (sysprep'd)"
elif $VIRSH snapshot-list --name "$TEMPLATE" 2>/dev/null | grep -q '^snp-'; then
    echo "    machine-id unreadable — accepting sealed snp-* snapshot as proof"
else
    die "template is NOT scrubbed — run ./01-prepare-template-for-cloning.sh first"
fi

cat > "$WORK/qga.py" <<'PYEOF'
import base64, json, subprocess, sys, time
dom, mode, args = sys.argv[1], sys.argv[2], sys.argv[3:]
def qga(payload):
    r = subprocess.run(["virsh","-c","qemu:///system","qemu-agent-command",
                        dom, json.dumps(payload)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)["return"]
if mode == "ping":
    print("agent-ok"); sys.exit(0)
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
PYEOF
qga()      { python3 "$WORK/qga.py" "$vm" exec "$@"; }
qga_lab()  { python3 "$WORK/qga.py" "$vm" exec --as lab "$@"; }
agent_ready() { python3 "$WORK/qga.py" "$vm" ping >/dev/null 2>&1; }

# ------------------------------------------------------------------
note "virt-clone $TEMPLATE -> $vm"
sudo virt-clone --original "$TEMPLATE" --name "$vm" --auto-clone

# hostname OFFLINE, before first boot (sa-02). Setting it later via
# guest-agent renames the machine while lightdm's X server is already
# running — the Xauthority cookie is keyed to the old name, so the
# user's first graphical session dies ("Cannot open display") and the
# greeter bounces back for a second login.
# --no-network: writing /etc/hostname needs no appliance network, and
# this host's passt backend dies when one is requested.
# NB: --hostname is silently inert here (observed on lab-vm-pub-5 —
# hosts updated but /etc/hostname untouched), so write the file via
# --run-command like everything else. /etc/hosts too — a stale
# 127.0.1.1 entry (or none) makes sudo warn "unable to resolve host".
sudo virt-customize --no-network -d "$vm" \
    --run-command "echo '$vm' > /etc/hostname; \
    if grep -q '^127\.0\.1\.1' /etc/hosts; then \
        sed -i 's|^127\.0\.1\.1.*|127.0.1.1 $vm|' /etc/hosts; \
    else echo '127.0.1.1 $vm' >> /etc/hosts; fi"

note "booting $vm (first post-sysprep boot: agent takes ~60-90s)"
$VIRSH start "$vm" >/dev/null
for _ in $(seq 60); do agent_ready && break; sleep 5; done
agent_ready || die "guest agent never came up in $vm"

# guest-ping answers before guest-exec is usable on first boot —
# warm it up or the first real call races and dies
for _ in $(seq 60); do qga true 2>/dev/null && break; sleep 5; done
qga true 2>/dev/null || die "guest-exec not usable in $vm"

# ------------------------------------------------------------------
# First-login password: local clones ship lab/lab; a first-login
# autostart opens `passwd` in a terminal so the user sets a real one
# (single prompt, no expired-password greeter dance). The entry
# self-removes only on success, so it nags until done.
# A publish-locked image ships '!' (no usable password): falling
# back to lab/lab would make every public clone a known-credential
# box until first login. Locked account -> random unambiguous OTP.
# ------------------------------------------------------------------
note "credentials: temp password now, passwd prompt at first login"
# lab/lab for local clones — forced to change at first login anyway.
# But a publish-locked image ships '!' (no usable password): falling
# back to lab/lab would make every public clone a known-credential
# box until first login. Locked account -> random unambiguous OTP.
if qga "passwd -S lab" 2>/dev/null | grep -q ' L '; then
    pwchars='23456789abcdefghjkmnpqrstuvwxyz'
    tmp_pw="lab-$(tr -dc "$pwchars" </dev/urandom | head -c 4 || true)-$(tr -dc "$pwchars" </dev/urandom | head -c 4 || true)"
else
    tmp_pw="lab"
fi
qga "echo 'lab:$tmp_pw' | chpasswd && passwd -l exp >/dev/null"

# clone/OTP pair: the one-time password lives in a sidecar file next
# to the clone's qcow2 — mode 400, operator-owned, one clone one file
qcow="$($VIRSH domblklist "$vm" | awk '/\.qcow2/ {print $NF; exit}')"
img_dir="$(dirname "$qcow")"
printf 'one-time password for %s (user lab): %s\n' "$vm" "$tmp_pw" > "$WORK/otp"
sudo install -m 0400 -o "$(id -un)" -g "$(id -gn)" "$WORK/otp" "$img_dir/$vm.otp"
echo "    otp written: $img_dir/$vm.otp (mode 400)"
# lab-first-login helper: trusts the opencode desktop launcher from
# inside the GUI session — Nemo honors metadata::trusted, XFCE/exo
# needs metadata::xfce-exe-checksum = sha256 of the file — then runs
# passwd. The autostart entry self-removes only when the password is
# actually changed — either by this passwd run, or by the last-change
# field differing from the stamp captured at provisioning (i.e. the
# user changed it some other way). Otherwise it reappears at every
# login: nag until changed. NOTE: stamp = passwd -S lastchg taken AFTER
# chpasswd — never `date` (TZ mismatch: passwd -S reports UTC days).
qga "printf '%s\n' \
       '#!/bin/bash' \
       'for f in \"\$HOME\"/Desktop/*.desktop; do [ -e \"\$f\" ] || continue' \
       '    gio set \"\$f\" metadata::trusted true 2>/dev/null' \
       '    set -- \$(sha256sum \"\$f\" 2>/dev/null)' \
       '    gio set \"\$f\" metadata::xfce-exe-checksum \"\$1\" 2>/dev/null' \
       'done' \
       'stamp=\$(cat \"\$HOME/.config/lab-pw-stamp\" 2>/dev/null)' \
       'set -- \$(passwd -S \"\$USER\" 2>/dev/null); lastchg=\$3' \
       'if [ -n \"\$stamp\" ] && [ \"\$lastchg\" != \"\$stamp\" ]; then' \
       '    rm -f \"\$HOME/.config/autostart/lab-first-login.desktop\" \"\$HOME/.config/lab-pw-stamp\"' \
       '    exit 0' \
       'fi' \
       'echo \"Please set a new login password (current password is your OTP).\"' \
       'echo' \
       'until passwd; do' \
       '    echo' \
       '    echo \"-- password not changed (wrong current password, or too short).\"' \
       '    echo \"   Try again, or close this window to skip for now.\"' \
       '    echo' \
       'done' \
       'rm -f \"\$HOME/.config/autostart/lab-first-login.desktop\" \"\$HOME/.config/lab-pw-stamp\"' \
       'echo' \
       'echo \"Password changed — this prompt will not appear again.\"' \
       'sleep 3' \
       > /usr/local/bin/lab-first-login && \
     chmod 0755 /usr/local/bin/lab-first-login && \
     mkdir -p /home/lab/.config/autostart && \
     set -- \$(passwd -S lab) && echo \$3 > /home/lab/.config/lab-pw-stamp && \
     printf '%s\n' \
       '[Desktop Entry]' \
       'Type=Application' \
       'Name=Set lab password' \
       'Exec=x-terminal-emulator -e /usr/local/bin/lab-first-login' \
       'X-GNOME-Autostart-enabled=true' \
       > /home/lab/.config/autostart/lab-first-login.desktop && \
     chown -R lab:lab /home/lab/.config/autostart /home/lab/.config/lab-pw-stamp"

note "per-clone agent identity: wipe opencode/vscodium state"
# opencode.db carries session/device identity — every clone must start
# fresh (belt-and-suspenders: 01's sysprep wipes it too, but this also
# covers clones from templates sealed before that change).
qga "rm -rf /home/lab/.local/share/opencode /home/lab/.cache/opencode \
     /home/lab/.config/VSCodium/machineid /home/lab/.cache/sessions"
# .cache/sessions: xfce saves session state there — a stale saved
# session from the template restores ghost apps and can crash the
# first login.

note "waiting for zone containers"
# lab-cnt-mcp runs under the mcp service account — invisible to lab's
# systemctl; probe its published health port instead.
for _ in $(seq 30); do
    qga_lab "curl -m3 -sf http://127.0.0.1:38080/health >/dev/null" 2>/dev/null && break
    sleep 5
done

note "ensuring current tooling (labloop-export, /incoming, batteries)"
# clones from a template built before a tooling change carry the old
# stack — idempotent, so harmless on current templates too
"$(dirname "$0")/30-ensure-lab-tools.sh" "$vm" \
    || echo "warn: ensure step failed — run ./30-ensure-lab-tools.sh $vm manually"

note "check-lab-ready.sh inside $vm"
set +e
qga_lab "bash /home/lab/Desktop/check-lab-ready.sh" </dev/null
rc=$?
set -e

cat <<EOF

==> $vm is up (hostname $vm).

    FIRST LOGIN:  user 'lab' password is $tmp_pw
    — a terminal pops up asking you to set a new one
      (type '$tmp_pw' once as the current password, then your new one).
      also stored at $img_dir/$vm.otp (mode 400)
    (exp is locked — it is a service account, not a login.)

    Open it in virt-manager, or rerun the check inside:
      bash ~/Desktop/check-lab-ready.sh
EOF
exit "$rc"
