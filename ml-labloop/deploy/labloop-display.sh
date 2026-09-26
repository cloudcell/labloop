#!/usr/bin/env bash
# labloop-display.sh — apply the configured display mode + refresh at
# graphical login (autostart via /etc/xdg/autostart/labloop-display.desktop).
#
# The virtio-gpu synthetic EDID advertises a fixed rate per resolution
# (2560x1440 only at 75.00). Any other configured rate is injected as
# a reduced-blanking modeline. Runs every login — idempotent and cheap,
# and it repairs the mode if SPICE negotiated something else. This is a
# lab, not a game: 30 Hz is a perfectly good refresh for a dashboard.
set -u

[ -r /etc/labloop/display.conf ] && . /etc/labloop/display.conf
X="${RES_X:-2560}"; Y="${RES_Y:-1440}"; HZ="${REFRESH:-75}"

export DISPLAY="${DISPLAY:-:0}"

# wait for a connected output (same first-login race as the panel clock)
out=""
for _ in $(seq 30); do
    out="$(xrandr 2>/dev/null | awk '/ connected/{print $1; exit}')"
    [ -n "$out" ] && break
    sleep 1
done
[ -n "$out" ] || exit 0

mode="${X}x${Y}"

# is a matching rate already offered for this mode? (native EDID list)
match=""
for r in $(xrandr 2>/dev/null | awk -v m="$mode" \
        '$1==m {for (i=2;i<=NF;i++){gsub(/[*+]/,"",$i); print $i}}'); do
    if awk -v a="$r" -v b="$HZ" 'BEGIN{exit !(a-b<0.5 && b-a<0.5)}'; then
        match="$r"; break
    fi
done

if [ -n "$match" ]; then
    xrandr --output "$out" --mode "$mode" --rate "$match"
    exit $?
fi

# not offered — synthesize a modeline and add it
name="${mode}_${HZ}"
params="$(cvt -r "$X" "$Y" "$HZ" 2>/dev/null \
    | sed -n 's/.*Modeline "[^"]*" *//p')"
[ -n "$params" ] || params="$(cvt "$X" "$Y" "$HZ" 2>/dev/null \
    | sed -n 's/.*Modeline "[^"]*" *//p')"
[ -n "$params" ] || params="$(gtf "$X" "$Y" "$HZ" 2>/dev/null \
    | sed -n 's/.*Modeline "[^"]*" *//p')"
[ -n "$params" ] || exit 1
xrandr --newmode "$name" $params 2>/dev/null
xrandr --addmode "$out" "$name"
xrandr --output "$out" --mode "$name"
