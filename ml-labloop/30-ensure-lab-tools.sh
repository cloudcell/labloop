#!/usr/bin/env bash
# 30-ensure-lab-tools.sh — bring any running lab VM up to the current
# tooling level. Idempotent: pushes the repo's deploy/ files into the
# guest, installs what belongs to root, refreshes the workspace copy,
# and updates the hostile-zone quadlet (restarting lab-cnt-exp ONLY if
# its definition changed).
#
#   ./30-ensure-lab-tools.sh <vm>     one VM
#   ./30-ensure-lab-tools.sh all      every running lab-vm-*
#
# Why this exists: clones made from a template built BEFORE a tooling
# change carry the old stack (no labloop-export, no /incoming mount).
# New templates bake everything in via create-lab-template; this is the
# upgrade path for VMs that already exist.
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(pwd)
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
die()  { echo "30-ensure: $*" >&2; exit 1; }
note() { echo "  ==> $*"; }

TARGET=${1:?"usage: ./30-ensure-lab-tools.sh <vm>|all"}
id -nG | grep -qw libvirt || exec sg libvirt -c "$0 $*"

# ------------------------------------------------------------------
# qemu guest-agent driver (exec + push)
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

# transport hiccups (agent up but exec/file-open briefly refused — seen
# right after boot or when /run was full) get retried; a genuine
# in-guest exit code does not
qga_exec() {
    local vm=$1 rc=0 _; shift
    for _ in 1 2 3 4 5; do
        python3 "$WORK/qga.py" "$vm" exec "$@" && return 0 || rc=$?
        [ "$rc" -eq 2 ] || return "$rc"
        sleep 4
    done
    return 2
}
qga_push() {
    local vm=$1 rc=0 _; shift
    for _ in 1 2 3 4 5; do
        python3 "$WORK/qga.py" "$vm" push "$@" && return 0 || rc=$?
        [ "$rc" -eq 2 ] || return "$rc"
        sleep 4
    done
    return 2
}

ensure_vm() {
    local vm=$1
    echo; echo "### $vm"
    virsh -c qemu:///system list --name | grep -qx "$vm" \
        || { echo "  skip: not running"; return 0; }
    qga_exec "$vm" "id -u" >/dev/null 2>&1 \
        || { echo "  skip: guest-agent not answering"; return 0; }

    note "pushing deploy files"
    for f in labloop-export labloop-update-opencode \
             labloop.sudoers labloop-tmpfiles.conf \
             security-battery.sh functional-battery.sh \
             GENESIS-RESEARCH-PROMPT.md DATA-MOVEMENT-MANUAL.md \
             AGENT-LAB-GUIDE.md SECURITY-MANUAL.md \
             check-lab-ready.sh \
             opencode.json opencode.jsonc; do
        qga_push "$vm" "$REPO/deploy/$f" "/tmp/ensure-$f"
    done
    qga_push "$vm" \
        "$REPO/deploy/quadlets/lab-cnt-exp.container" /tmp/ensure-lab-cnt-exp.container
    qga_push "$vm" \
        "$REPO/deploy/quadlets/lab-cnt-mcp.container" /tmp/ensure-lab-cnt-mcp.container
    qga_push "$vm" \
        "$REPO/containers/mcp/entrypoint.sh" /tmp/ensure-entrypoint.sh

    # diagnostics reports from the sibling ml-scientist checkout ->
    # ~/Desktop/diagnostics (they are *.md — the workspace payload's
    # md-strip would remove them, so they ship via QGA directly)
    local diag_names=() b
    local diag_src="$REPO/../ml-scientist/diagnostics"
    if [ -d "$diag_src" ]; then
        for f in "$diag_src"/*; do
            [ -f "$f" ] || continue
            b="$(basename "$f")"
            qga_push "$vm" "$f" "/tmp/ensure-diag-$b"
            diag_names+=("$b")
        done
    fi

    # content check, not existence — an older installed revision must
    # count as "needs update", or updated scripts would never roll out
    local diag_cmp=""
    if ((${#diag_names[@]})); then
        diag_cmp=" && test -d /home/lab/Desktop/diagnostics"
        for b in "${diag_names[@]}"; do
            diag_cmp="$diag_cmp && cmp -s '/tmp/ensure-diag-$b' \
                '/home/lab/Desktop/diagnostics/$b'"
        done
    fi
    local need=0
    qga_exec "$vm" "
        cmp -s /tmp/ensure-labloop-export /usr/local/sbin/labloop-export &&
        cmp -s /tmp/ensure-labloop-update-opencode \
               /usr/local/sbin/labloop-update-opencode &&
        cmp -s /tmp/ensure-labloop.sudoers /etc/sudoers.d/labloop &&
        cmp -s /tmp/ensure-labloop-tmpfiles.conf /etc/tmpfiles.d/labloop.conf &&
        cmp -s /tmp/ensure-security-battery.sh \
               /srv/lab/workspace/ml-labloop/deploy/security-battery.sh &&
        cmp -s /tmp/ensure-functional-battery.sh \
               /srv/lab/workspace/ml-labloop/deploy/functional-battery.sh &&
        cmp -s /tmp/ensure-GENESIS-RESEARCH-PROMPT.md \
               /srv/lab/workspace/ml-labloop/deploy/GENESIS-RESEARCH-PROMPT.md &&
        cmp -s /tmp/ensure-DATA-MOVEMENT-MANUAL.md \
               /srv/lab/workspace/ml-labloop/deploy/DATA-MOVEMENT-MANUAL.md &&
        cmp -s /tmp/ensure-GENESIS-RESEARCH-PROMPT.md \
               /home/lab/GENESIS-RESEARCH-PROMPT.md &&
        test -L /home/lab/workspace/GENESIS-RESEARCH-PROMPT.md &&
        cmp -s /tmp/ensure-AGENT-LAB-GUIDE.md /home/lab/AGENT-LAB-GUIDE.md &&
        test -L /home/lab/workspace/AGENTS.md &&
        cmp -s /tmp/ensure-SECURITY-MANUAL.md /home/lab/SECURITY-MANUAL.md &&
        test -L /home/lab/workspace/SECURITY-MANUAL.md &&
        cmp -s /tmp/ensure-check-lab-ready.sh \
               /home/lab/Desktop/check-lab-ready.sh &&
        cmp -s /tmp/ensure-opencode.json \
               /home/lab/.config/opencode/opencode.json &&
        cmp -s /tmp/ensure-opencode.jsonc \
               /home/lab/.config/opencode/opencode.jsonc &&
        cmp -s /tmp/ensure-lab-cnt-exp.container \
               /home/exp/.config/containers/systemd/lab-cnt-exp.container &&
        cmp -s /tmp/ensure-lab-cnt-mcp.container \
               /home/mcp/.config/containers/systemd/lab-cnt-mcp.container &&
        cmp -s /tmp/ensure-entrypoint.sh \
               /srv/lab/workspace/ml-labloop/containers/mcp/entrypoint.sh &&
        test -d /var/lib/labloop-export/user &&
        test -d /var/lib/labloop-export/root &&
        test -d /srv/lab/incoming$diag_cmp" >/dev/null 2>&1 || need=1
    [ "$need" = 0 ] && { echo "  already current"; return 0; }

    note "installing (root side)"
    qga_exec "$vm" "
        install -m 0755 -o root -g root /tmp/ensure-labloop-export /usr/local/sbin/labloop-export &&
        install -m 0755 -o root -g root /tmp/ensure-labloop-update-opencode \
            /usr/local/sbin/labloop-update-opencode &&
        install -m 0440 -o root -g root /tmp/ensure-labloop.sudoers /etc/sudoers.d/labloop &&
        install -m 0644 -o root -g root /tmp/ensure-labloop-tmpfiles.conf /etc/tmpfiles.d/labloop.conf &&
        systemd-tmpfiles --create /etc/tmpfiles.d/labloop.conf &&
        rm -rf /run/labloop-export &&
        install -d -m 0755 -o lab -g lab /srv/lab/incoming &&
        visudo -c | grep -q 'labloop: parsed OK'" \
        || { echo "  FAIL: root-side install"; return 1; }

    note "refreshing workspace deploy/ (lab-owned)"
    qga_exec "$vm" "
        install -d -o lab -g lab /srv/lab/workspace/ml-labloop/deploy &&
        install -m 0755 -o lab -g lab /tmp/ensure-labloop-export \
            /srv/lab/workspace/ml-labloop/deploy/labloop-export &&
        install -m 0755 -o lab -g lab /tmp/ensure-labloop-update-opencode \
            /srv/lab/workspace/ml-labloop/deploy/labloop-update-opencode &&
        install -m 0644 -o lab -g lab /tmp/ensure-security-battery.sh \
            /srv/lab/workspace/ml-labloop/deploy/security-battery.sh &&
        install -m 0644 -o lab -g lab /tmp/ensure-functional-battery.sh \
            /srv/lab/workspace/ml-labloop/deploy/functional-battery.sh &&
        install -m 0644 -o lab -g lab /tmp/ensure-GENESIS-RESEARCH-PROMPT.md \
            /srv/lab/workspace/ml-labloop/deploy/GENESIS-RESEARCH-PROMPT.md &&
        install -m 0644 -o lab -g lab /tmp/ensure-DATA-MOVEMENT-MANUAL.md \
            /srv/lab/workspace/ml-labloop/deploy/DATA-MOVEMENT-MANUAL.md &&
        install -m 0644 -o lab -g lab /tmp/ensure-GENESIS-RESEARCH-PROMPT.md \
            /home/lab/GENESIS-RESEARCH-PROMPT.md &&
        ln -sf ../GENESIS-RESEARCH-PROMPT.md \
            /home/lab/workspace/GENESIS-RESEARCH-PROMPT.md &&
        chown -h lab:lab /home/lab/workspace/GENESIS-RESEARCH-PROMPT.md &&
        install -m 0644 -o lab -g lab /tmp/ensure-AGENT-LAB-GUIDE.md \
            /srv/lab/workspace/ml-labloop/deploy/AGENT-LAB-GUIDE.md &&
        install -m 0644 -o lab -g lab /tmp/ensure-AGENT-LAB-GUIDE.md \
            /home/lab/AGENT-LAB-GUIDE.md &&
        ln -sf ../AGENT-LAB-GUIDE.md /home/lab/workspace/AGENTS.md &&
        chown -h lab:lab /home/lab/workspace/AGENTS.md &&
        install -m 0644 -o lab -g lab /tmp/ensure-SECURITY-MANUAL.md \
            /srv/lab/workspace/ml-labloop/deploy/SECURITY-MANUAL.md &&
        install -m 0644 -o lab -g lab /tmp/ensure-SECURITY-MANUAL.md \
            /home/lab/SECURITY-MANUAL.md &&
        ln -sf ../SECURITY-MANUAL.md /home/lab/workspace/SECURITY-MANUAL.md &&
        chown -h lab:lab /home/lab/workspace/SECURITY-MANUAL.md &&
        install -d -m 0755 -o lab -g lab /home/lab/Desktop &&
        install -m 0755 -o lab -g lab /tmp/ensure-check-lab-ready.sh \
            /home/lab/Desktop/check-lab-ready.sh &&
        install -d -m 0755 -o lab -g lab /home/lab/.config/opencode &&
        install -m 0644 -o lab -g lab /tmp/ensure-opencode.json \
            /home/lab/.config/opencode/opencode.json &&
        install -m 0644 -o lab -g lab /tmp/ensure-opencode.jsonc \
            /home/lab/.config/opencode/opencode.jsonc &&
        install -m 0440 -o lab -g lab /tmp/ensure-labloop.sudoers \
            /srv/lab/workspace/ml-labloop/deploy/labloop.sudoers &&
        install -m 0644 -o lab -g lab /tmp/ensure-labloop-tmpfiles.conf \
            /srv/lab/workspace/ml-labloop/deploy/labloop-tmpfiles.conf &&
        install -d -o lab -g lab /srv/lab/workspace/ml-labloop/containers/mcp &&
        install -m 0755 -o lab -g lab /tmp/ensure-entrypoint.sh \
            /srv/lab/workspace/ml-labloop/containers/mcp/entrypoint.sh" \
        || echo "  warn: workspace refresh failed (workspace absent?)"

    if ((${#diag_names[@]})); then
        note "installing diagnostics -> ~/Desktop/diagnostics"
        qga_exec "$vm" \
            "install -d -m 0755 -o lab -g lab /home/lab/Desktop/diagnostics" \
            || echo "  warn: diagnostics dir failed"
        for b in "${diag_names[@]}"; do
            qga_exec "$vm" "install -m 0644 -o lab -g lab '/tmp/ensure-diag-$b' \
                '/home/lab/Desktop/diagnostics/$b' && rm -f '/tmp/ensure-diag-$b'" \
                || echo "  warn: diagnostics file $b failed"
        done
    fi

    # quadlet: update definition; restart lab-cnt-exp only if it changed
    local changed
    changed=$(qga_exec "$vm" "
        if cmp -s /tmp/ensure-lab-cnt-exp.container \
                  /home/exp/.config/containers/systemd/lab-cnt-exp.container 2>/dev/null; then
            echo same
        else echo changed; fi" | tr -d '[:space:]')
    if [ "$changed" = changed ]; then
        note "quadlet changed — updating + restarting lab-cnt-exp (the /incoming mount)"
        qga_exec "$vm" "
            install -m 0644 -o exp -g exp /tmp/ensure-lab-cnt-exp.container \
                /home/exp/.config/containers/systemd/lab-cnt-exp.container &&
            su - exp -c 'XDG_RUNTIME_DIR=/run/user/\$(id -u) \
                systemctl --user daemon-reload && systemctl --user restart lab-cnt-exp'" \
            || echo "  warn: quadlet install/restart failed"
    else
        echo "  quadlet already current"
    fi

    changed=$(qga_exec "$vm" "
        if cmp -s /tmp/ensure-lab-cnt-mcp.container \
                  /home/mcp/.config/containers/systemd/lab-cnt-mcp.container 2>/dev/null; then
            echo same
        else echo changed; fi" | tr -d '[:space:]')
    if [ "$changed" = changed ]; then
        note "mcp quadlet changed — updating + restarting lab-cnt-mcp"
        qga_exec "$vm" "
            install -m 0644 -o mcp -g mcp /tmp/ensure-lab-cnt-mcp.container \
                /home/mcp/.config/containers/systemd/lab-cnt-mcp.container &&
            su - mcp -c 'XDG_RUNTIME_DIR=/run/user/\$(id -u) \
                systemctl --user daemon-reload && systemctl --user restart lab-cnt-mcp'" \
            || echo "  warn: mcp quadlet install/restart failed"
    else
        echo "  mcp quadlet already current"
    fi

    # trial interpreter env: /srv/lab/trial-env is volume-mounted at
    # /opt/trial-env inside lab-cnt-mcp — episteme's executor default
    # interpreter when populated (entrypoint exports
    # ML_EPISTEME_EXECUTOR_PYTHON). Populate once via podman exec —
    # writes go through the mount to the mcp-owned host dir, so the env
    # survives container recreates. Restart after populate so the
    # entrypoint picks it up.
    if ! qga_exec "$vm" "test -x /srv/lab/trial-env/bin/python" >/dev/null 2>&1; then
        note "populating /srv/lab/trial-env (uv venv + scientific stack)"
        qga_exec "$vm" "
            install -d -m 0755 -o mcp -g mcp /srv/lab/trial-env &&
            su - mcp -c 'export XDG_RUNTIME_DIR=/run/user/\$(id -u);
                systemctl --user restart lab-cnt-mcp' &&
            sleep 5 &&
            su - mcp -c 'export XDG_RUNTIME_DIR=/run/user/\$(id -u);
                podman exec lab-cnt-mcp uv venv /opt/trial-env' &&
            su - mcp -c 'export XDG_RUNTIME_DIR=/run/user/\$(id -u);
                podman exec lab-cnt-mcp uv pip install \
                    --python /opt/trial-env/bin/python --no-cache \
                    numpy pandas scipy scikit-learn matplotlib reportlab' &&
            su - mcp -c 'export XDG_RUNTIME_DIR=/run/user/\$(id -u);
                systemctl --user restart lab-cnt-mcp'" \
            || echo "  warn: trial-env population failed (egress? mcp container up?)"
    else
        echo "  trial-env already populated"
    fi
    echo "  done: $vm has extraction (labloop-export) + ingest (/incoming)"
}

if [ "$TARGET" = all ]; then
    for vm in $(virsh -c qemu:///system list --name | grep '^lab-vm-' || true); do
        ensure_vm "$vm"
    done
else
    ensure_vm "$TARGET"
fi
