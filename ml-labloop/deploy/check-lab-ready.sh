#!/usr/bin/env bash
# check-lab-ready.sh — is this lab VM ready to work?
# Runs on the driver desktop as `lab`. Double-click from ~/Desktop or
# run in a shell. PASS/FAIL per check, then the full security battery.

# Double-clicked without a terminal? Re-exec inside one so the output
# is visible. Guarded on $DISPLAY so provisioning/qga (headless) still
# runs it in-process.
if [ -n "${DISPLAY:-}" ] && [ ! -t 0 ] && [ -z "${_LABREADY_TTY:-}" ]; then
    export _LABREADY_TTY=1
    exec x-terminal-emulator -e bash "$0" "$@"
fi

set -u
pass=0; fail=0; warn=0
ok()   { printf 'PASS  %-34s %s\n' "$1" "$2"; pass=$((pass+1)); }
warn() { printf 'WARN  %-34s %s\n' "$1" "$2"; warn=$((warn+1)); }
bad()  { printf 'FAIL  %-34s %s\n' "$1" "$2"; fail=$((fail+1)); }

echo "== lab readiness check =="

# Fresh boot? Quadlets take a while — wait for both zones before
# checking, so an early run doesn't report false FAILs.
# lab-cnt-mcp runs under the `mcp` service account — lab's systemctl
# cannot see it; probe the published health port instead.
waited=0
until curl -m3 -sf http://127.0.0.1:38080/health >/dev/null 2>&1 \
   && sudo -n -u exp /usr/local/sbin/labloop-exec true 2>/dev/null; do
    [ "$waited" -ge 300 ] && break
    [ $((waited % 30)) -eq 0 ] && [ "$waited" -gt 0 ] \
        && echo "    ... waiting for zone containers (${waited}s)"
    sleep 5; waited=$((waited+5))
done

# --- zones up ----------------------------------------------------------
if pgrep -u mcp -f 'labloop-mcp' >/dev/null 2>&1; then
    ok trusted-container "lab-cnt-mcp active (under mcp service account)"
else
    bad trusted-container "lab-cnt-mcp down — runs under mcp; check 38080 /health"
fi

if sudo -n -u exp /usr/local/sbin/labloop-exec true 2>/dev/null; then
    ok exec-channel "lab -> lab-cnt-exp via labloop-exec"
else
    bad exec-channel "cannot reach lab-cnt-exp — is exp's unit up?"
fi

# --- MCP surfaces ------------------------------------------------------
for p in 38050 38060 38070 38080 38082 38090; do
    if curl -m3 -sf "http://127.0.0.1:$p/health" >/dev/null 2>&1; then
        ok "port-$p" "/health ok"
    else
        bad "port-$p" "no /health on 127.0.0.1:$p"
    fi
done

# --- driver tooling ----------------------------------------------------
for tool in opencode uv uvx codium git mc; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "tool-$tool" "$(command -v "$tool")"
    else
        bad "tool-$tool" "not on PATH"
    fi
done

# --- agent configs -----------------------------------------------------
OC=~/.config/opencode/opencode.json
if [ -f "$OC" ] && grep -q '"episteme"' "$OC"; then
    ok opencode-config "MCP servers wired in opencode.json"
else
    bad opencode-config "$OC missing or no MCP servers"
fi
OCC=~/.config/opencode/opencode.jsonc
if [ -f "$OCC" ] && grep -q '192\.168\.122\.1:8002' "$OCC"; then
    ok opencode-vllm "vLLM host provider wired in opencode.jsonc"
else
    bad opencode-vllm "$OCC missing or no vLLM baseURL"
fi
# opencode shadow/drift — WARN only. The npm pin lives at
# /usr/local/bin/opencode (root-owned); a self-update instead drops a
# lab-owned standalone binary under ~/.opencode + a shim in ~/.local/bin
# that wins PATH precedence. Not a privilege crossing (still lab code),
# so it warns rather than fails — but the pinned version is no longer
# what runs, and provenance becomes unverified.
OCBIN="$(command -v opencode 2>/dev/null || true)"
if [ -n "$OCBIN" ] && [ "$OCBIN" != "/usr/local/bin/opencode" ]; then
    warn opencode-shadowed "$OCBIN wins PATH — expected /usr/local/bin (self-update shim?)"
elif [ -n "$OCBIN" ]; then
    runv="$(opencode --version 2>/dev/null | grep -o '[0-9][0-9.]*' | head -1)"
    pinv="$(grep -o '"version": *"[0-9.]*"' \
        /usr/local/lib/node_modules/@opencode/cli/package.json 2>/dev/null \
        | grep -o '[0-9][0-9.]*' | head -1)"
    if [ -n "$pinv" ] && [ -n "$runv" ] && [ "$runv" != "$pinv" ]; then
        warn opencode-version "running $runv but npm pin is $pinv"
    fi
fi

ZC=~/.config/VSCodium/User/globalStorage/zoocodeorganization.zoo-code/settings/mcp_settings.json
if [ -f "$ZC" ]; then
    ok zoo-config "zoo-code mcp_settings.json present"
else
    bad zoo-config "zoo-code MCP settings missing"
fi

# --- trust-boundary layout ---------------------------------------------
for d in /srv/lab/workspace /srv/lab/exchange /srv/lab/experiments \
         /srv/lab/mcp-state "$HOME/workspace/experiments"; do
    [ -d "$d" ] && ok "dir:$(basename "$d")" "$d" \
                || bad "dir:$(basename "$d")" "$d missing"
done

# trial interpreter env: episteme's executor uses /opt/trial-env
# (mounted from /srv/lab/trial-env) when populated — without it trials
# run on the slim server venv and numpy imports fail. WARN not FAIL:
# the env is supplementary, old clones degrade gracefully.
if [ -x /srv/lab/trial-env/bin/python ]; then
    ok dir:trial-env "/srv/lab/trial-env -> /opt/trial-env (scientific stack for trials)"
else
    warn dir:trial-env "/srv/lab/trial-env unpopulated — trials lack numpy/scipy stack"
fi

echo
echo "== $pass PASS, $warn WARN, $fail FAIL =="

# --- deep checks: security + functional batteries ----------------------
sec=/srv/lab/workspace/ml-labloop/deploy/security-battery.sh
fun=/srv/lab/workspace/ml-labloop/deploy/functional-battery.sh
if [ "$fail" -eq 0 ] && [ -f "$sec" ]; then
    echo "== security battery =="
    bash "$sec"
fi
if [ "$fail" -eq 0 ] && [ -f "$fun" ]; then
    echo "== functional battery =="
    bash "$fun"
fi

# keep the terminal open when double-clicked
[ -t 0 ] && { echo; read -r -p "press enter to close"; }
exit "$fail"
