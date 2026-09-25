#!/usr/bin/env bash
# labloop functional battery — can the lab actually DO science?
# Complements security-battery.sh (which checks the walls) with
# end-to-end workflow checks (can the LLM write, install, compile,
# exchange). Run INSIDE the VM as `lab`:
#
#   bash /srv/lab/workspace/ml-labloop/deploy/functional-battery.sh
#
# Exit status: number of FAILs.

set -u
EXP_EXEC="sudo -n -u exp /usr/local/sbin/labloop-exec"
PROBE=/experiments/.func-probe     # scratch dir inside the exp zone

pass=0; fail=0; warn=0
ok()   { printf 'PASS  %-34s %s\n' "$1" "$2"; pass=$((pass+1)); }
bad()  { printf 'FAIL  %-34s %s\n' "$1" "$2"; fail=$((fail+1)); }
note() { printf 'WARN  %-34s %s\n' "$1" "$2"; warn=$((warn+1)); }

echo "== labloop functional battery =="
if [ "$(id -u)" = "0" ]; then
    note runs-as-root "run as lab — sudo/qga execs make some checks meaningless"
fi

# --- execution channel ---------------------------------------------------
if $EXP_EXEC true 2>/dev/null; then
    ok exec-channel "labloop-exec works"
else
    bad exec-channel "cannot exec into lab-cnt-exp — everything below is moot"
    echo; echo "== result: $pass PASS, $warn WARN, $fail FAIL =="; exit "$fail"
fi

# --- write + run code in the hostile zone --------------------------------
$EXP_EXEC rm -rf "$PROBE" 2>/dev/null
if $EXP_EXEC sh -c "mkdir -p $PROBE && printf 'print(6*7)\n' > $PROBE/a.py \
                    && python3 $PROBE/a.py" 2>/dev/null | grep -q 42; then
    ok exp-write-run "wrote + ran python under /experiments"
else
    bad exp-write-run "cannot create/run code in /experiments"
fi

# --- writable HOME (caches, tools) ----------------------------------------
if $EXP_EXEC sh -c 'touch "$HOME/.probe" && rm "$HOME/.probe"' 2>/dev/null; then
    ok exp-home-writable "HOME=$($EXP_EXEC sh -c 'echo $HOME' 2>/dev/null) writable"
else
    bad exp-home-writable "HOME not writable inside lab-cnt-exp"
fi

# --- baked scientific stack ------------------------------------------------
if $EXP_EXEC python3 -c 'import numpy,pandas,scipy,sklearn,matplotlib,reportlab' \
     >/dev/null 2>&1; then
    ok baked-py-stack "numpy/pandas/scipy/sklearn/matplotlib/reportlab"
else
    bad baked-py-stack "a baked python dep is missing"
fi

# --- uv: the paved road -----------------------------------------------------
# real install, real egress — six is tiny and pure-python
if $EXP_EXEC sh -c "cd $PROBE && uv venv --system-site-packages .venv >/dev/null 2>&1 \
     && uv pip install --python .venv/bin/python six >/dev/null 2>&1 \
     && .venv/bin/python -c 'import six'" >/dev/null 2>&1; then
    ok uv-venv-install "uv venv + uv pip install six works"
else
    bad uv-venv-install "uv venv/install failed — check egress + UV_CACHE_DIR"
fi

# --- PDF pipeline (the thing that broke in pub-6) ---------------------------
if $EXP_EXEC sh -c "cd $PROBE && \
     printf '\\\\documentclass{article}\\\\begin{document}hi\\\\end{document}\n' > t.tex \
     && pdflatex -interaction=nonstopmode t.tex >/dev/null 2>&1 && test -s t.pdf" \
     2>/dev/null; then
    ok pdflatex-compiles "minimal .tex -> .pdf"
else
    bad pdflatex-compiles "pdflatex failed on minimal document"
fi

# --- artifact handoff end-to-end ---------------------------------------------
if $EXP_EXEC sh -c "mkdir -p /exchange/.func-probe/out \
     && cp $PROBE/t.pdf /exchange/.func-probe/out/" 2>/dev/null \
   && head -c4 /srv/lab/exchange/.func-probe/out/t.pdf 2>/dev/null | grep -q '%PDF'; then
    ok pdf-crosses-exchange "PDF readable by lab via /exchange"
else
    bad pdf-crosses-exchange "artifact did not reach /srv/lab/exchange"
fi
# cleanup goes through the exp channel — exp-created subdirs are
# intentionally not lab-writable (the handoff is read-only for lab)
$EXP_EXEC rm -rf /exchange/.func-probe 2>/dev/null

# --- pandoc -------------------------------------------------------------------
if $EXP_EXEC sh -c "cd $PROBE && echo '# t' | pandoc -f markdown -t html | grep -q '<h1'" \
     2>/dev/null; then
    ok pandoc-works "pandoc md->html"
else
    bad pandoc-works "pandoc failed"
fi

# --- install lanes: micromamba + /opt/local + labloop-build --------------------
if $EXP_EXEC micromamba --version >/dev/null 2>&1; then
    ok micromamba-present "$($EXP_EXEC micromamba --version 2>/dev/null | head -1)"
else
    bad micromamba-present "micromamba missing — conda-forge lane closed"
fi

if $EXP_EXEC sh -c 'touch /opt/local/bin/.probe && rm /opt/local/bin/.probe' \
     2>/dev/null; then
    ok optlocal-writable "/opt/local writable + on PATH"
else
    bad optlocal-writable "/opt/local not writable — install lane closed"
fi

# the sudoers rule allows labloop-build with NO arguments — calling it
# would trigger a rebuild, so just verify the channel is wired up
if [ -x /usr/local/sbin/labloop-build ] \
   && sudo -n -l 2>/dev/null | grep -q labloop-build; then
    ok build-channel "labloop-build installed + in lab's sudoers"
else
    bad build-channel "labloop-build missing — no image-rebuild path"
fi

# --- export channel: path-mode round trip ----------------------------------
# real export of a known file through labloop-export, then verify the
# staged manifest + tarball integrity. (--all is NOT run here — it
# quiesces both containers; its wiring is checked via sudoers only.)
if [ -x /usr/local/sbin/labloop-export ] \
   && sudo -n -l 2>/dev/null | grep -q 'labloop-export --all'; then
    ok export-channel "labloop-export installed + --all in lab's sudoers"
else
    bad export-channel "labloop-export missing — no data-extraction path"
fi

echo "roundtrip-$RANDOM" > /srv/lab/exchange/.exp-probe 2>/dev/null
if labloop-export /srv/lab/exchange/.exp-probe >/dev/null 2>&1 \
   && python3 - <<'PY' 2>/dev/null
import hashlib, json
m = json.load(open("/var/lib/labloop-export/user/manifest.json"))
want = hashlib.sha256(open("/srv/lab/exchange/.exp-probe","rb").read()).hexdigest()
hit = [f for f in m["files"] if f["sha256"] == want]
assert hit and m["tarball"]["name"] == "export.tar.gz"
import os; assert os.path.exists("/var/lib/labloop-export/user/export.tar.gz")
PY
then
    ok export-roundtrip "staged manifest+tarball, sha256 verified"
else
    bad export-roundtrip "path-mode export or manifest check failed"
fi
rm -f /srv/lab/exchange/.exp-probe

# --- driver-side: agent tooling -----------------------------------------------
for t in opencode uv codium git mc; do
    command -v "$t" >/dev/null 2>&1 && ok "tool-$t" "$(command -v "$t")" \
                                   || bad "tool-$t" "not on PATH"
done

# --- driver can reach the MCP layer --------------------------------------------
if curl -m5 -sf http://127.0.0.1:38080/health >/dev/null 2>&1; then
    ok mcp-episteme-health "episteme /health ok"
else
    bad mcp-episteme-health "episteme not answering"
fi

$EXP_EXEC rm -rf "$PROBE" 2>/dev/null
echo
echo "== result: $pass PASS, $warn WARN, $fail FAIL =="
exit "$fail"
