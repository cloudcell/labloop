#!/usr/bin/env bash
# 90-extract-lab-data.sh — pull a labloop-export artifact out of a VM.
# Host half of docs/se-plans/plan-20260923-2038Z--secure-data-extraction.md.
#
#   ./90-extract-lab-data.sh <vm> [dest-dir]     (dest defaults ./extracted)
#
# Pull-only over the existing qemu-guest-agent channel — no SSH, no
# virtiofs, no guest->host sockets. Reads exactly two fixed paths under
# /var/lib/labloop-export/{root,user}/ (manifest.json + export.tar.gz),
# verifies the tarball sha256 against the manifest, and lands:
#
#   <vm>-extraction-<YYYYMMDDTHHMMZ>.tar.gz        (0600)
#   <vm>-extraction-<YYYYMMDDTHHMMZ>.tar.gz.sha256 (sha256sum -c format)
#   _UNTRUSTED-SENSITIVE.txt                        (dropped once)
#
# The tarball is NEVER opened here — it is both untrusted (hostile
# bytes) and sensitive (agent state contains credentials). Extract it
# deliberately, somewhere appropriate, yourself.
set -euo pipefail
cd "$(dirname "$0")"
WORK=$(mktemp -d)
RESTORE=""
restore_vm() {   # put the VM back the way we found it (see wake block)
    case "$RESTORE" in
        "")        return 0 ;;
        suspend)   note "restoring $VM to paused"
                   virsh -c qemu:///system suspend "$VM" >/dev/null 2>&1 ;;
        shutdown)  note "restoring $VM to off (acpi shutdown)"
                   virsh -c qemu:///system shutdown "$VM" >/dev/null 2>&1 ;;
        pmsuspend) note "restoring $VM to suspend-to-ram"
                   virsh -c qemu:///system dompmsuspend "$VM" --target mem >/dev/null 2>&1 ;;
    esac
}
trap 'rm -rf "$WORK"; restore_vm' EXIT
die() { echo "90-extract: $*" >&2; exit 1; }
note() { echo; echo "==> $*"; }

VM=${1:?"usage: ./90-extract-lab-data.sh <vm> [dest-dir]"}
DEST=${2:-./extracted}
case "$VM" in *[!a-zA-Z0-9._-]*) die "bad vm name '$VM'";; esac

id -nG | grep -qw libvirt || exec sg libvirt -c "$0 $*"

# ------------------------------------------------------------------
# wake the VM if dormant — paused -> resume, off -> start, suspended
# -> dompmwakeup — and record what to restore on exit (the EXIT trap
# above runs on success, failure, and Ctrl-C alike). The agent-wait
# below absorbs the boot/resume lag.
# ------------------------------------------------------------------
virsh -c qemu:///system dominfo "$VM" >/dev/null 2>&1 \
    || die "no such VM: $VM"
state=$(virsh -c qemu:///system domstate "$VM" 2>/dev/null | tr 'A-Z' 'a-z')
case "$state" in
    running|idle) ;;
    paused)       note "$VM is paused — resuming"
                  virsh -c qemu:///system resume "$VM" >/dev/null
                  RESTORE=suspend ;;
    "shut off")   note "$VM is off — starting"
                  virsh -c qemu:///system start "$VM" >/dev/null
                  RESTORE=shutdown ;;
    pmsuspended)  note "$VM is suspended — waking"
                  virsh -c qemu:///system dompmwakeup "$VM" >/dev/null
                  RESTORE=pmsuspend ;;
    "in shutdown") note "$VM is shutting down — waiting, then starting"
                  for _ in $(seq 60); do
                      [ "$(virsh -c qemu:///system domstate "$VM" 2>/dev/null)" = "shut off" ] && break
                      sleep 2
                  done
                  virsh -c qemu:///system start "$VM" >/dev/null
                  RESTORE=shutdown ;;
    *)            die "VM '$VM' is in unhandled state '$state'" ;;
esac

# ------------------------------------------------------------------
# qemu guest-agent driver (exec for resolve, pull for file reads)
# ------------------------------------------------------------------
cat > "$WORK/qga.py" <<'PYEOF'
import base64, json, subprocess, sys, time
dom, mode, args = sys.argv[1], sys.argv[2], sys.argv[3:]
def qga(payload):
    try:
        r = subprocess.run(["virsh","-c","qemu:///system","qemu-agent-command",
                            dom, json.dumps(payload)],
                           capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as e:
        sys.stderr.write((e.stderr or "") + (e.stdout or ""))
        sys.exit(2)   # transport-level failure — caller may retry
    return json.loads(r.stdout)["return"]
if mode == "ping":
    print("agent-ok"); sys.exit(0)
if mode == "exec":
    pid = qga({"execute":"guest-exec","arguments":
               {"path":"/bin/sh","arg":["-c"," ".join(args)],
                "capture-output":True}})["pid"]
    for _ in range(600):
        time.sleep(0.5)
        st = qga({"execute":"guest-exec-status","arguments":{"pid":pid}})
        if st.get("exited"):
            sys.stdout.write(base64.b64decode(
                st.get("out-data","") or "").decode("utf-8","replace"))
            sys.stderr.write(base64.b64decode(
                st.get("err-data","") or "").decode("utf-8","replace"))
            sys.exit(st.get("exitcode",1))
    sys.exit("guest-exec timeout")
if mode == "pull":
    remote, local = args
    h = qga({"execute":"guest-file-open","arguments":{"path":remote,"mode":"r"}})
    try:
        with open(local,"wb") as f:
            while True:
                r = qga({"execute":"guest-file-read","arguments":
                         {"handle":h,"count":48*1024}})
                data = base64.b64decode(r.get("buf-b64","") or "")
                if not data: break
                f.write(data)
                if r.get("eof"): break
    finally:
        qga({"execute":"guest-file-close","arguments":{"handle":h}})
PYEOF

# transport hiccups (agent up but exec briefly refused — common right
# after boot) get retried; a genuine in-guest exit code does not
qga() {
    local rc=0 _
    for _ in 1 2 3 4 5; do
        python3 "$WORK/qga.py" "$VM" exec "$@" && return 0 || rc=$?
        [ "$rc" -eq 2 ] || return "$rc"
        sleep 4
    done
    return 2
}
qga_pull() {
    local rc=0 _
    for _ in 1 2 3 4 5; do
        python3 "$WORK/qga.py" "$VM" pull "$1" "$2" && return 0 || rc=$?
        [ "$rc" -eq 2 ] || return "$rc"
        sleep 4
    done
    return 2
}

# first poll exits immediately on a running VM; a cold boot needs
# ~60-90s before guest-exec answers — up to ~3 min here
for _ in $(seq 90); do qga true >/dev/null 2>&1 && break; sleep 2; done
qga true >/dev/null 2>&1 || die "guest-agent not answering in $VM (woke but agent not ready)"

# self-heal: older clones predate the export stack — deploy it
# (idempotent; installs labloop-export, sudoers, tmpfiles, quadlet)
if ! qga "test -x /usr/local/sbin/labloop-export" 2>/dev/null; then
    note "$VM lacks lab tooling — running 30-ensure-lab-tools.sh"
    ./30-ensure-lab-tools.sh "$VM" || die "tooling deploy failed"
fi

# ------------------------------------------------------------------
# pick the newest complete staging tree — lexical gate: only these
# two dirs are EVER acceptable answers from the guest
# ------------------------------------------------------------------
mode_dir=$(qga "ls -td /var/lib/labloop-export/*/manifest.json 2>/dev/null | head -1" \
           | tr -d '[:space:]')
mode_dir=${mode_dir%/manifest.json}
case "$mode_dir" in
    /var/lib/labloop-export/root | /var/lib/labloop-export/user) ;;
    "") if [ -n "$RESTORE" ]; then
            # we woke this VM ourselves — nothing was mid-flight, so
            # staging a full export can't interrupt live work
            note "no export staged — running labloop-export --all in guest"
            qga "/usr/local/sbin/labloop-export --all" \
                || die "guest-side export failed (see output above)"
            mode_dir=/var/lib/labloop-export/root
        else
            die "no export staged in $VM — run labloop-export in the guest first"
        fi ;;
    *)  die "guest returned unexpected staging path '$mode_dir' — refusing" ;;
esac

note "pulling manifest from $mode_dir"
qga_pull "$mode_dir/manifest.json" "$WORK/manifest.json" \
    || die "could not pull manifest.json"

# ------------------------------------------------------------------
# validate the manifest ON THE HOST before trusting a single byte
# ------------------------------------------------------------------
meta=$(python3 - "$VM" "$WORK/manifest.json" <<'PY'
import json, re, sys
from datetime import datetime
vm, path = sys.argv[1], sys.argv[2]
m = json.load(open(path))
tb = m["tarball"]
assert m.get("format") == 1, "unknown manifest format"
assert tb["name"] == "export.tar.gz", "unexpected tarball name"
assert re.fullmatch(r"[0-9a-f]{64}", tb["sha256"]), "bad tarball sha256"
assert 0 < tb["size"] <= 8*1024**3, "tarball size out of cap"
assert isinstance(m.get("files"), list), "files list missing"
ts = datetime.strptime(m["created"], "%Y-%m-%dT%H:%M:%SZ")
print(f"{vm}-extraction-{ts:%Y%m%dT%H%M}Z.tar.gz {tb['sha256']} {tb['size']}")
PY
) || die "manifest failed validation"
read -r OUTNAME TSHA TSIZE <<<"$meta"

mkdir -p "$DEST"
chmod 700 "$DEST" 2>/dev/null || true
OUT="$DEST/$OUTNAME"
[ -e "$OUT" ] && die "refusing to overwrite $OUT"

note "pulling $OUTNAME ($TSIZE bytes, sha256 ${TSHA:0:12}…)"
qga_pull "$mode_dir/export.tar.gz" "$OUT.partial" || die "tarball pull failed"

# ------------------------------------------------------------------
# verify before it earns its real name
# ------------------------------------------------------------------
actual=$(sha256sum "$OUT.partial" | cut -d' ' -f1)
[ "$actual" = "$TSHA" ] || { rm -f "$OUT.partial"; die "sha256 mismatch — pull corrupted or tampered"; }
mv "$OUT.partial" "$OUT"
chmod 0600 "$OUT"
printf '%s  %s\n' "$TSHA" "$OUTNAME" > "$OUT.sha256"
chmod 0600 "$OUT.sha256"

[ -e "$DEST/_UNTRUSTED-SENSITIVE.txt" ] || cat > "$DEST/_UNTRUSTED-SENSITIVE.txt" <<'EOF'
Contents of this directory are UNTRUSTED and SENSITIVE.

UNTRUSTED: artifacts were produced inside a VM whose experiment zone
           runs unvetted/agent-generated code. Do not execute, index,
           or open contents casually; treat embedded paths, scripts,
           and configs as adversarial.
SENSITIVE: full exports include agent state (opencode sessions,
           VSCodium storage) which may contain credentials, tokens,
           prompts, and private data. Keep permissions 0600; do not
           publish or share without a secrets review.

Integrity: verify with `sha256sum -c <file>.sha256`. The checksum
proves integrity of the transfer, not authenticity of the content.
EOF
chmod 0600 "$DEST/_UNTRUSTED-SENSITIVE.txt"

printf '%s  vm=%s mode_dir=%s out=%s size=%s sha256=%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$VM" "$mode_dir" "$OUTNAME" "$TSIZE" "$TSHA" \
    >> "$DEST/extraction.log"
chmod 0600 "$DEST/extraction.log" 2>/dev/null || true

echo
echo "==> landed: $OUT"
echo "    sha256: $TSHA"
echo "    verify: cd $DEST && sha256sum -c $OUTNAME.sha256"
echo "    NOTE: tarball left packed — it is untrusted + sensitive (see _UNTRUSTED-SENSITIVE.txt)"
