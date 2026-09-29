#!/usr/bin/env bash
# test-watchdog-ports — runtime check that ports flow from ports.env,
# not literals. rc-11 follow-up: the watchdog once carried
# PORTS=(38050 …) and the quadlet eleven hardcoded PublishPort lines;
# a ports.env move would have probed dead ports and reported a phantom
# wedge.
#
# What this exercises (observable runtime behavior, not greps):
#   1. The REAL watchdog script is executed with XDG_CONFIG_HOME aimed
#      at a sandboxed ports.env whose every port is shifted — the probe
#      lines it prints on stderr must carry the DERIVED ports.
#   2. With no ports.env at all it must log the loud NOTE and fall back
#      to the built-in defaults — never probe silently wrong ports.
#   3. The quadlet's @MCP_PORTS_PUBLISH@ placeholder is rendered through
#      the same sed pipeline create-lab-template/30-ensure use, and the
#      result must carry exactly one PublishPort per ML_*_PORT var —
#      a whole-line anchor regression check (an unanchored match once
#      duplicated the block inside the token's own comment).
#   4. Static guards: the template holds no literal PublishPort and
#      HealthCmd expands ${ML_EPISTEME_PORT}, never a baked number.
#
# Safety: HOME + XDG_* are sandboxed; a stub `systemctl` shadows the
# real one on PATH, so even if the failure threshold were somehow
# reached the restart would hit the stub and the test would see it.
# One run per case = failure counter 1/3 — the act branch is never
# reached.
#
# Exit 0 = pass, exit 1 = fail. Skips loudly (exit 0) when the host is
# inside the watchdog's own boot-grace window, where probing is
# legitimately suppressed.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WATCHDOG="$HERE/labloop-mcp-watchdog"
QUADLET="$HERE/quadlets/lab-cnt-mcp.container"

fail() { echo "FAIL: $*" >&2; exit 1; }
note() { echo "ok: $*"; }

[ -x "$WATCHDOG" ] || fail "watchdog not executable: $WATCHDOG"
[ -f "$QUADLET" ]  || fail "quadlet template missing: $QUADLET"
command -v curl >/dev/null || fail "curl missing (watchdog dep)"
command -v ss   >/dev/null || fail "ss missing (watchdog dep)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- stubbed PATH: systemctl can never touch a real unit ------------
STUBS="$WORK/stubs"
mkdir -p "$STUBS"
cat > "$STUBS/systemctl" <<'EOF'
#!/usr/bin/env bash
echo "STUB-INVOKED: systemctl $*" >&2
exit 1
EOF
chmod +x "$STUBS/systemctl"

run_watchdog() {                      # $1 = sandbox config dir (may lack ports.env)
    local cfg="$1" out
    out="$(
        env -i PATH="$STUBS:/usr/bin:/bin" \
            HOME="$WORK/home" \
            XDG_CONFIG_HOME="$cfg" \
            XDG_STATE_HOME="$WORK/state" \
            bash "$WATCHDOG" 2>&1
    )"
    printf '%s\n' "$out"
    if printf '%s\n' "$out" | grep -q "STUB-INVOKED"; then
        fail "watchdog attempted a real action (systemctl reached) — runaway logic"
    fi
    if printf '%s\n' "$out" | grep -q "within boot grace"; then
        echo "SKIP: host uptime inside watchdog boot grace — probe suppressed by design"
        exit 0
    fi
}

probed_ports() {  # stdin: watchdog output → sorted list of probed ports
    grep -oE '^[0-9]+ +(ok|FAIL)' | awk '{print $1}' | sort -n
}

# --- 1. derived ports ------------------------------------------------
CFG="$WORK/config"
mkdir -p "$CFG/labloop" "$WORK/home"

cat > "$CFG/labloop/ports.env" <<'EOF'
ML_AGORA_PORT=48050
ML_ARETE_PORT=48060
ML_ZETESIS_PORT=48070
ML_EPISTEME_PORT=48080
ML_EPISTEME_INGEST_PORT=48082
ML_ANAMNESIS_PORT=48090
EOF

out="$(run_watchdog "$CFG")"
printf '%s\n' "$out" | grep -q "ports from $CFG/labloop/ports.env" \
    || fail "watchdog did not log sourcing $CFG/labloop/ports.env"

got="$(printf '%s\n' "$out" | probed_ports | tr '\n' ' ')"
want="48050 48060 48070 48080 48082 48090 "
[ "$got" = "$want" ] \
    || fail "probed ports '$got' != ports.env-derived '$want'"
printf '%s\n' "$out" | grep -qE '^38[0-9]{2} ' \
    && fail "a baked default port was still probed: $(printf '%s\n' "$out" | grep -E '^38[0-9]{2} ' | head -1)"
note "watchdog probed the ports.env-derived set: $got"

# --- 2. missing-env fallback ------------------------------------------
EMPTY_CFG="$WORK/config-empty"
mkdir -p "$EMPTY_CFG"
out="$(run_watchdog "$EMPTY_CFG")"
printf '%s\n' "$out" | grep -q "absent — using built-in defaults" \
    || fail "missing ports.env produced no loud fallback NOTE"
got="$(printf '%s\n' "$out" | probed_ports | tr '\n' ' ')"
want="38050 38060 38070 38080 38082 38090 "
[ "$got" = "$want" ] \
    || fail "fallback probed '$got' != defaults '$want'"
note "missing ports.env degrades loudly to defaults: $got"

# --- 3. quadlet render (same pipeline as create-lab-template:494-501) --
cat > "$WORK/doctored.env" <<'EOF'
ML_AGORA_PORT=49050
ML_ARETE_PORT=49060
ML_ZETESIS_PORT=49070
ML_EPISTEME_PORT=49080
ML_EPISTEME_INGEST_PORT=49082
ML_ANAMNESIS_PORT=49090
EOF

( set -a; . "$WORK/doctored.env"; set +a
  grep -oE '^ML_[A-Z_]+_PORT' "$WORK/doctored.env" \
      | while read -r v; do
            printf 'PublishPort=127.0.0.1:%s:%s\n' "${!v}" "${!v}"
        done ) > "$WORK/mcp-ports.publish"

sed -e "/^@MCP_PORTS_PUBLISH@\$/r $WORK/mcp-ports.publish" \
    -e "/^@MCP_PORTS_PUBLISH@\$/d" \
    "$QUADLET" > "$WORK/lab-cnt-mcp.rendered"

grep -qE '^@MCP_PORTS_PUBLISH@$' "$WORK/lab-cnt-mcp.rendered" \
    && fail "placeholder survived the render"
# The token appears mid-line in the header comment — it must survive
# (whole-line anchor; the unanchored variant once duplicated the block).
grep -q '@MCP_PORTS_PUBLISH@' "$WORK/lab-cnt-mcp.rendered" \
    || fail "render ate the header comment — anchor regressed?"

npub=$(grep -c '^PublishPort=' "$WORK/lab-cnt-mcp.rendered")
nvars=$(grep -cE '^ML_[A-Z_]+_PORT' "$WORK/doctored.env")
[ "$npub" -eq "$nvars" ] \
    || fail "rendered $npub PublishPort lines for $nvars port vars"

sed -n 's/^PublishPort=127\.0\.0\.1:\([0-9][0-9]*\):[0-9][0-9]*$/\1/p' \
    "$WORK/lab-cnt-mcp.rendered" | sort -n | uniq > "$WORK/rendered.ports"
printf '49050\n49060\n49070\n49080\n49082\n49090\n' > "$WORK/want.ports"
cmp -s "$WORK/rendered.ports" "$WORK/want.ports" \
    || fail "rendered ports differ from doctored ports.env: $(cat "$WORK/rendered.ports" | tr '\n' ' ')"
note "quadlet renders exactly $npub PublishPort lines from ports.env"

# --- 4. static guards --------------------------------------------------
grep -qE '^PublishPort=' "$QUADLET" \
    && fail "literal PublishPort remains in the quadlet template"
grep 'HealthCmd=' "$QUADLET" | grep -q '38080' \
    && fail "HealthCmd carries a baked port literal"
grep 'HealthCmd=' "$QUADLET" | grep -q '${ML_EPISTEME_PORT}' \
    || fail "HealthCmd does not expand \${ML_EPISTEME_PORT}"
grep -nE '^\s*"[0-9]{4,5}"' "$WATCHDOG" \
    && fail "bare port literal in watchdog PORTS array"
note "no literal ports in template PublishPort/HealthCmd or watchdog array"

echo "PASS: ports flow from ports.env at runtime (watchdog probe list, quadlet render, fallback path)"
