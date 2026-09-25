#!/usr/bin/env bash
# 80-ingest-lab-data.sh — push a data batch INTO a lab VM.
# Mirror of 90-extract-lab-data.sh (which is pull-only; this one is
# the sanctioned push channel — host-initiated, via qemu-guest-agent,
# no SSH/virtiofs/guest listeners).
#
#   ./80-ingest-lab-data.sh <vm> <src-path> [batch-name]
#
# src is a file or directory on this host — convention: drop things
# into ./incoming/ first. The batch lands in the guest at
#
#   /srv/lab/incoming/<batch-name>-<YYYYMMDDTHHMMZ>/
#
# lab-owned, and mounted READ-ONLY into the hostile zone at /incoming:
# experiments can read datasets but cannot tamper with them — inputs
# keep their provenance. The batch dir name always carries the UTC
# ingest timestamp; batches are immutable once landed (re-ingesting
# creates a new timestamped batch, it never overwrites).
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
die()  { echo "80-ingest: $*" >&2; exit 1; }
note() { echo; echo "==> $*"; }

FORCE=0
args=()
for a in "$@"; do
    [ "$a" = "--force" ] && FORCE=1 || args+=("$a")
done
set -- "${args[@]:-}"

VM=${1:?"usage: ./80-ingest-lab-data.sh <vm> <src-path> [batch-name] [--force]"}
SRC=${2:?"usage: ./80-ingest-lab-data.sh <vm> <src-path> [batch-name] [--force]"}
NAME=${3:-}
case "$VM" in *[!a-zA-Z0-9._-]*) die "bad vm name '$VM'";; esac
[ -e "$SRC" ] || die "no such source: $SRC"

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

# batch name: slug + UTC ingest timestamp (Z suffix, per convention)
slug=${NAME:-$(basename "$SRC")}
slug=$(echo "$slug" | tr -c 'a-zA-Z0-9._-' '-' | sed 's/^-*//;s/-*$//')
[ -n "$slug" ] || die "could not derive a batch name — pass one explicitly"
echo "$slug" | tr -d '.-_' | grep -q '[a-zA-Z0-9]' \
    || die "batch name '$slug' is dots/dashes only — pass a real name"

# secrets heuristic: /incoming is READABLE BY THE HOSTILE ZONE. Refuse
# likely-credential filenames unless the operator passes --force.
if [ "$FORCE" != 1 ]; then
    hits=$(find "$SRC" -type f \( -iname '*.pem' -o -iname '*.key' \
        -o -iname 'id_rsa*' -o -iname 'id_ed25519*' -o -iname 'id_dsa*' \
        -o -iname 'id_ecdsa*' -o -iname '.env' -o -iname '.env.*' \
        -o -iname '*.kdbx' -o -iname '*.p12' -o -iname '*.pfx' \
        -o -iname '*.ppk' -o -iname '*secret*' -o -iname '*credential*' \
        \) -print 2>/dev/null | head -10)
    [ -z "$hits" ] || die "source contains likely-secret filenames (hostile zone can READ /incoming):
$hits
re-run with --force if these are intentional dataset files"
fi
TS=$(date -u +%Y%m%dT%H%MZ)
BATCH="${slug}-${TS}"
GDEST="/srv/lab/incoming/$BATCH"

# ------------------------------------------------------------------
# pack on the host (deterministic; the tar is ours — member names are
# safe by construction) and push over the agent channel
# ------------------------------------------------------------------
note "packing $(basename "$SRC") -> $BATCH"
tar --sort=name --owner=0 --group=0 --numeric-owner \
    -cf "$WORK/batch.tar" -C "$(dirname "$SRC")" "$(basename "$SRC")"

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
if mode == "push":
    local, remote = args
    h = qga({"execute":"guest-file-open","arguments":{"path":remote,"mode":"w+"}})
    try:
        with open(local,"rb") as f:
            while chunk := f.read(48*1024):
                qga({"execute":"guest-file-write","arguments":
                     {"handle":h,"buf-b64":base64.b64encode(chunk).decode()}})
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

# first poll exits immediately on a running VM; a cold boot needs
# ~60-90s before guest-exec answers — up to ~3 min here
for _ in $(seq 90); do qga true >/dev/null 2>&1 && break; sleep 2; done
qga true >/dev/null 2>&1 || die "guest-agent not answering in $VM (woke but agent not ready)"

# self-heal: older clones predate the ingest stack (the quadlet
# /incoming mount especially) — deploy it. NOTE: a first-time deploy
# restarts lab-cnt-exp if its quadlet changed.
if ! qga "test -x /usr/local/sbin/labloop-export" 2>/dev/null; then
    note "$VM lacks lab tooling — running 30-ensure-lab-tools.sh"
    ./30-ensure-lab-tools.sh "$VM" || die "tooling deploy failed"
fi

# refuse to overwrite an existing batch (same name+minute collision)
qga "test ! -e '$GDEST'" || die "batch $BATCH already exists in $VM — refusing to overwrite"

note "pushing $(du -h "$WORK/batch.tar" | cut -f1) to $VM"
python3 "$WORK/qga.py" "$VM" push "$WORK/batch.tar" /tmp/labloop-ingest.tar

# ------------------------------------------------------------------
# guest-side: land at /srv/lab/incoming/<batch>/ owned by lab.
# /srv/lab/incoming is created self-healing (older VMs predate it).
# ------------------------------------------------------------------
qga "install -d -m 0755 -o lab -g lab /srv/lab/incoming && \
     install -d -m 0755 -o lab -g lab '$GDEST' && \
     tar --no-same-owner -xf /tmp/labloop-ingest.tar -C '$GDEST' && \
     chown -R lab:lab '$GDEST' && \
     rm -f /tmp/labloop-ingest.tar" \
    || die "guest-side landing failed"

echo
echo "==> landed: $VM:$GDEST"
echo "    visible to experiments (read-only) at /incoming/$BATCH/"
echo "    contains: $(tar -tf "$WORK/batch.tar" | head -1)$(tar -tf "$WORK/batch.tar" | sed -n 2p | sed 's/.*/ .../')"
