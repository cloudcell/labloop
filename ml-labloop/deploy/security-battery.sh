#!/usr/bin/env bash
# labloop Phase-8 security battery — fail-closed probes for the zone
# model. Run INSIDE the VM as `lab`.
#
# Semantics: every "attack" below is EXPECTED TO FAIL. PASS means the
# boundary held; FAIL means something got through; WARN marks a known
# gap that is documented but not yet closed (e.g. bearer auth).
#
# Exit status: 0 if no FAILs, otherwise the number of FAILs.

set -u
# the ONLY sanctioned channel into the hostile zone — NOPASSWD via
# /etc/sudoers.d/labloop; -n proves no password is needed (scripts and
# agents have no tty). `sudo -iu exp` deliberately does NOT work: -i
# validates the login shell, bypassing the rule.
EXP_EXEC="sudo -n -u exp /usr/local/sbin/labloop-exec"

pass=0; fail=0; warn=0
ok()   { printf 'PASS  %-32s %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()  { printf 'FAIL  %-32s %s\n' "$1" "$2"; fail=$((fail+1)); }
note() { printf 'WARN  %-32s %s\n' "$1" "$2"; warn=$((warn+1)); }

echo "== labloop security battery =="

# lab-side permission checks are only meaningful AS lab — running the
# battery as root (e.g. via qemu-guest-agent) makes mode-700 checks
# falsely FAIL, since root bypasses permission bits.
if [ "$(id -u)" = "0" ]; then
    note runs-as-root "running as root — lab-side permission checks will be wrong; run as lab"
fi

# --- positive controls -------------------------------------------------
if curl -m5 -sf http://127.0.0.1:38080/health >/dev/null; then
    ok mcp-reachable-from-driver "127.0.0.1:38080 /health ok"
else
    bad mcp-reachable-from-driver "episteme /health did not answer — MCP zone down?"
fi

if $EXP_EXEC true 2>/dev/null; then
    ok exec-channel-works "lab -> exp podman exec works"
else
    bad exec-channel-works "cannot exec into lab-cnt-exp"
fi

# --- hostile zone cannot reach the trusted zone ------------------------
# in-container reachability probe — python3, since the slim image has
# no curl (a missing binary would fake a "boundary held" PASS)
reach() { $EXP_EXEC python3 -c \
    "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('$1',timeout=3) else 1)" \
    >/dev/null 2>&1; }

# lab-cnt-mcp runs under the mcp service account — lab's podman cannot
# inspect it to resolve the IP, so the quadlet pins it (172.31.0.0/24).
MCP_IP=172.31.0.2
if reach "http://$MCP_IP:38080/health"; then
    bad hostile-to-mcp-container "reached $MCP_IP:38080 from lab-cnt-exp"
else
    ok hostile-to-mcp-container "$MCP_IP:38080 unreachable from lab-net-exp"
fi

# bridge gateway = the VM's netns as seen from inside the hostile
# container (slim image has no iproute2 — read /proc/net/route)
GW=$($EXP_EXEC python3 -c \
    'import socket,struct
for l in open("/proc/net/route").readlines()[1:]:
    f=l.split()
    if f[1]=="00000000":
        print(socket.inet_ntoa(struct.pack("<L",int(f[2],16)))); break' 2>/dev/null)
if [ -n "$GW" ]; then
    if reach "http://$GW:38080/health"; then
        bad hostile-to-vm-gw-mcp "reached $GW:38080 — loopback publish is leaking"
    else
        ok hostile-to-vm-gw-mcp "$GW:38080 unreachable (loopback publish holds)"
    fi
else
    note hostile-to-vm-gw-mcp "no default route inside lab-cnt-exp — probe skipped"
fi

# the VM's NAT gateway = the host side (192.168.122.1 on default libvirt
# net). Rootless podman = slirp4netns user-mode NAT, so hostile traffic
# is exp-uid OUTPUT — fenced by deploy/labloop.nft owner-match rules.
# Probe a CLOSED port: ECONNREFUSED means the packet arrived (reachable),
# only a timeout proves the drop. Port 53 is exempt (guest DNS needs it).
if $EXP_EXEC python3 -c "import socket,sys
s=socket.socket(); s.settimeout(4)
try:
    s.connect(('192.168.122.1',22)); sys.exit(0)
except ConnectionRefusedError:
    sys.exit(0)
except Exception:
    sys.exit(1)" >/dev/null 2>&1; then
    bad hostile-to-host-gw "reached host NAT gateway — install deploy/labloop.nft"
else
    ok hostile-to-host-gw "host gateway unreachable from hostile zone"
fi

# the fence must be INSTALLED for boot persistence — lab can't list the
# kernel ruleset (needs CAP_NET_ADMIN), so check the file + rely on the
# behavioral probes above for enforcement
if [ -f /etc/nftables.d/labloop.nft ] \
   && grep -q nftables.d /etc/nftables.conf 2>/dev/null; then
    ok nft-labloop-installed "ruleset installed + included in nftables.conf"
else
    bad nft-labloop-installed "ruleset not persisted — install deploy/labloop.nft to /etc/nftables.d"
fi

# --- hostile zone filesystem confinement -------------------------------
if $EXP_EXEC touch /workspace/.probe 2>/dev/null; then
    bad workspace-is-ro "write to /workspace succeeded"
    $EXP_EXEC rm -f /workspace/.probe 2>/dev/null
else
    ok workspace-is-ro "/workspace rejects writes"
fi

if $EXP_EXEC touch /.probe 2>/dev/null; then
    bad rootfs-is-ro "write to container rootfs succeeded"
    $EXP_EXEC rm -f /.probe 2>/dev/null
else
    ok rootfs-is-ro "root filesystem is read-only"
fi

if $EXP_EXEC ls /state >/dev/null 2>&1; then
    bad state-not-mounted "/state visible inside hostile container"
else
    ok state-not-mounted "no /state in hostile container"
fi

if $EXP_EXEC sh -c 'test -d /incoming' 2>/dev/null; then
    if $EXP_EXEC touch /incoming/.probe 2>/dev/null; then
        bad incoming-is-ro "write to /incoming succeeded — datasets tamperable"
        $EXP_EXEC rm -f /incoming/.probe 2>/dev/null
    else
        ok incoming-is-ro "/incoming rejects writes (ingest channel RO)"
    fi
else
    note incoming-is-ro "no /incoming mount — quadlet predates ingest channel"
fi

# --- hostile zone privilege + resource caps ----------------------------
NNP=$($EXP_EXEC grep NoNewPrivs /proc/self/status 2>/dev/null | awk '{print $2}')
[ "$NNP" = "1" ] && ok no-new-privileges "NoNewPrivs=1" \
                || bad no-new-privileges "NoNewPrivs=$NNP"

CAPS=$($EXP_EXEC grep CapEff /proc/self/status 2>/dev/null | awk '{print $2}')
[ "$CAPS" = "0000000000000000" ] && ok caps-dropped "CapEff=0" \
                                 || bad caps-dropped "CapEff=$CAPS"

PIDS=$($EXP_EXEC cat /sys/fs/cgroup/pids.max 2>/dev/null)
if [ -n "$PIDS" ] && [ "$PIDS" != "max" ] && [ "$PIDS" -le 512 ]; then
    ok pids-capped "pids.max=$PIDS"
else
    bad pids-capped "pids.max=$PIDS"
fi

MEM=$($EXP_EXEC cat /sys/fs/cgroup/memory.max 2>/dev/null)
if [ -n "$MEM" ] && [ "$MEM" != "max" ] && [ "$MEM" -le 17179869184 ]; then
    ok mem-capped "memory.max=$MEM"
else
    bad mem-capped "memory.max=$MEM"
fi

# fork-bomb containment: 700 sleeps must NOT all spawn. Do NOT try to
# read pids.current during the burst — once the cgroup is full, conmon
# cannot fork its exec-session bookkeeping and podman exec returns 254
# with all output lost (observed on tmp-20). Measure AFTER the fact:
# pids.peak proves the ceiling held, pids.events 'max' proves the cap
# was actually hit (i.e. the burst exceeded it), and a fresh exec
# proves the container survived. catatonit (RunInit) reaps the orphans.
EV0=$($EXP_EXEC awk '/^max/{print $2}' /sys/fs/cgroup/pids.events 2>/dev/null)
$EXP_EXEC timeout 20 bash -c \
    'for i in $(seq 700); do sleep 8 & done 2>/dev/null' >/dev/null 2>&1
sleep 3
PEAK=$($EXP_EXEC cat /sys/fs/cgroup/pids.peak 2>/dev/null)
EV1=$($EXP_EXEC awk '/^max/{print $2}' /sys/fs/cgroup/pids.events 2>/dev/null)
LIM=$($EXP_EXEC cat /sys/fs/cgroup/pids.max 2>/dev/null)
# kernel quirk: pids.peak can overshoot pids.max by a few — the
# charge is recorded before the limit check rejects the fork
if [ -n "$PEAK" ] && [ -n "$LIM" ] && [ "$PEAK" -le $((LIM + 16)) ] 2>/dev/null \
   && [ -n "$EV1" ] && [ "$EV1" -gt "${EV0:-0}" ] 2>/dev/null \
   && $EXP_EXEC true 2>/dev/null; then
    ok fork-bomb-contained "pids.peak=$PEAK (cap $LIM, hit ${EV1}x); container alive"
else
    bad fork-bomb-contained "peak=$PEAK lim=$LIM events=$EV0->$EV1"
fi

# --- no container-runtime sockets, no host mounts ----------------------
if $EXP_EXEC sh -c 'ls /var/run/docker.sock /run/podman/podman.sock 2>/dev/null' | grep -q .; then
    bad no-runtime-sockets "a container socket is visible"
else
    ok no-runtime-sockets "no docker/podman socket in hostile zone"
fi

# toolchain policy: uv is the only package manager. pip is shimmed to
# a redirect, python -m pip must fail (module removed), uv must work.
if $EXP_EXEC uv --version >/dev/null 2>&1 \
   && ! $EXP_EXEC python3 -c 'import pip' >/dev/null 2>&1 \
   && ! $EXP_EXEC pip --version >/dev/null 2>&1; then
    ok uv-only-packaging "uv present; pip module + shim inert"
else
    bad uv-only-packaging "pip still functional or uv missing"
fi

# exec/build channels must be the ONLY sudo surface lab holds into
# the zone accounts — anything broader would void the mcp fence.
# (Mint ships its own root NOPASSWD rules for update tooling; those
# target fixed vendor binaries and are out of scope.)
NP=$(sudo -n -l 2>/dev/null | grep -cE '\((exp|mcp)\).*NOPASSWD')
if [ "$NP" -eq 2 ]; then
    ok sudo-surface-scoped "zone NOPASSWD = 2 (labloop-exec + labloop-build)"
elif [ "$NP" -eq 0 ]; then
    note sudo-surface-scoped "sudo -n -l empty — cannot enumerate rules"
else
    bad sudo-surface-scoped "lab has $NP exp/mcp NOPASSWD rules — expected 2"
fi

# --- host-side zone separation -----------------------------------------
# deterministic permission checks (no exp shell needed): verify the
# invariant, not just a sampled attempt.
MS_MODE=$(stat -c %a /srv/lab/mcp-state 2>/dev/null)
MS_OWNER=$(stat -c %U /srv/lab/mcp-state 2>/dev/null)
if [ "$MS_MODE" = "700" ] && [ "$MS_OWNER" = "mcp" ]; then
    ok exp-cannot-read-mcp-state "mcp-state is mcp:mcp mode 700"
else
    bad exp-cannot-read-mcp-state "mcp-state is $MS_OWNER:$MS_MODE"
fi

# the DRIVER is fenced from trusted state too: lab (and therefore the
# agent) must not read the raw DBs or admin the trusted container —
# scientific state changes go through MCP tools only.
if ls /srv/lab/mcp-state/ >/dev/null 2>&1; then
    bad lab-reads-mcp-state "lab can list mcp-state — trusted zone not isolated"
else
    ok lab-cannot-read-mcp-state "mcp-state unreadable from lab shell"
fi
if podman container exists lab-cnt-mcp 2>/dev/null; then
    bad lab-admins-mcp-zone "lab-cnt-mcp visible in lab's podman"
else
    ok lab-cannot-admin-mcp-zone "lab-cnt-mcp not in lab's podman store"
fi

WS_ACL=$(getfacl -p /srv/lab/workspace 2>/dev/null | grep 'user:exp:' | awk -F: '{print $3}')
if [ "$WS_ACL" = "r-x" ] || [ "$WS_ACL" = "r--" ]; then
    ok exp-cannot-write-workspace "exp ACL on workspace = $WS_ACL (no w)"
else
    bad exp-cannot-write-workspace "exp ACL on workspace = $WS_ACL"
fi

# exchange flow through the real channel: exec writes into the
# container's /exchange mount (= host /srv/lab/exchange), lab reads it
if $EXP_EXEC sh -c 'echo probe > /exchange/.probe' 2>/dev/null \
   && [ "$(cat /srv/lab/exchange/.probe 2>/dev/null)" = "probe" ]; then
    ok exchange-handoff "hostile wrote, lab read"
    rm -f /srv/lab/exchange/.probe
else
    bad exchange-handoff "artifact did not cross /srv/lab/exchange"
fi

# same, one level deep: the default ACL must be o::rX (capital X =
# traverse dirs). With o::r only, subdirectories are unreadable to
# everyone but exp — observed breaking capture_bundle on nested paths.
if $EXP_EXEC sh -c 'mkdir -p /exchange/.probe.d && echo ok > /exchange/.probe.d/f' 2>/dev/null \
   && [ "$(cat /srv/lab/exchange/.probe.d/f 2>/dev/null)" = "ok" ]; then
    ok exchange-subdir-handoff "hostile wrote nested dir, lab traversed"
    # cleanup via the exec channel: the exp-created subdir is not
    # lab-deletable (only /srv/lab/exchange itself is lab:rwx)
    $EXP_EXEC rm -rf /exchange/.probe.d 2>/dev/null
else
    bad exchange-subdir-handoff "exchange subdirs not traversable (ACL o::rX missing)"
fi

# --- MCP published to loopback only ------------------------------------
VM_IP=$(ip -4 -o addr show scope global | awk '{split($4,a,"/"); print a[1]; exit}')
if [ -n "$VM_IP" ] && curl -m3 -sf "http://$VM_IP:38080/health" >/dev/null 2>&1; then
    bad mcp-loopback-only "MCP answered on $VM_IP — published beyond loopback"
else
    ok mcp-loopback-only "38080 not bound on external iface"
fi

# --- extraction channel (labloop-export) --------------------------------
# fail-closed probes for the pull-only export — every call below is
# EXPECTED TO BE REFUSED. Staging dirs are provisioned by tmpfiles
# (0700 per-owner): verify hostile can't poison them either.
EXP_SBIN=/usr/local/sbin/labloop-export

if $EXP_SBIN /etc/shadow >/dev/null 2>&1; then
    bad export-path-gate "exported /etc/shadow — realpath gate broken"
else
    ok export-path-gate "/etc/shadow refused (outside /exchange)"
fi

ln -sf /srv/lab/mcp-state /srv/lab/exchange/.esc-link 2>/dev/null
if $EXP_SBIN /srv/lab/exchange/.esc-link >/dev/null 2>&1; then
    bad export-symlink-escape "followed symlink into mcp-state"
else
    ok export-symlink-escape "symlink -> mcp-state refused"
fi
rm -f /srv/lab/exchange/.esc-link

if sudo -n $EXP_SBIN --all /etc/shadow >/dev/null 2>&1; then
    bad export-arg-pin "sudo accepted --all + extra arg — arg-pin broken"
else
    ok export-arg-pin "sudoers arg-pin rejects extra args to --all"
fi

if $EXP_SBIN /srv/lab/mcp-state >/dev/null 2>&1; then
    bad export-trusted-zone "path mode reached mcp-state"
else
    ok export-trusted-zone "mcp-state refused via path mode"
fi

U_ST=$(stat -c '%U:%a' /var/lib/labloop-export/user 2>/dev/null)
R_ST=$(stat -c '%U:%a' /var/lib/labloop-export/root 2>/dev/null)
if [ "$U_ST" = "lab:700" ] && [ "$R_ST" = "root:700" ]; then
    ok export-staging-perms "staging tree lab:700 / root:700"
else
    bad export-staging-perms "staging perms user=$U_ST root=$R_ST"
fi
if touch /var/lib/labloop-export/root/.poison 2>/dev/null; then
    rm -f /var/lib/labloop-export/root/.poison
    bad export-staging-write "lab wrote into root staging — tamperable"
else
    ok export-staging-write "lab cannot write root staging"
fi
# note: the host-side manifest lexical gate (probe 5 in the plan) and
# the guest->host nft fence (probe 6, covered above) are not testable
# from inside the guest — they are verified host-side.

# --- documented gaps ----------------------------------------------------
if curl -m5 -s http://127.0.0.1:38080/mcp >/dev/null 2>&1; then
    note bearer-auth-gap "MCP answers without token — auth unimplemented (known gap)"
fi

echo
echo "== result: $pass PASS, $warn WARN, $fail FAIL =="
exit "$fail"
