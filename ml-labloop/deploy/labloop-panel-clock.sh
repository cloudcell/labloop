#!/usr/bin/env bash
# labloop-panel-clock.sh — set the XFCE panel clock to "%H:%M %Z" UTC.
# Runs once at first graphical login via /etc/xdg/autostart — xfconf
# state only exists once xfconfd is up in the user session, so this
# cannot be done at provisioning time.
#
# The clock plugin id is discovered by VALUE: /plugins/plugin-N holds
# the module name ("clock") — the perchannel XML only carries type
# placeholders, so it cannot be parsed for this.
stamp="$HOME/.config/.labloop-clock-done"
[ -e "$stamp" ] && exit 0
mkdir -p "$HOME/.config"

export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
export DISPLAY="${DISPLAY:-:0}"

# wait for xfconfd to materialize /plugins/* (first-login race)
pid=""
for _ in $(seq 60); do
    pid="$(xfconf-query -c xfce4-panel -lv 2>/dev/null | awk \
        '$1 ~ /^\/plugins\/plugin-[0-9]+$/ && $2 == "clock" {
             sub(".*/", "", $1); print $1; exit}')"
    [ -n "$pid" ] && break
    sleep 2
done
[ -n "$pid" ] || exit 0

for kv in "digital-time-format=%H:%M %Z" "timezone=UTC"; do
    prop="/plugins/$pid/${kv%%=*}"; val="${kv#*=}"
    xfconf-query -c xfce4-panel -p "$prop" -n -t string -s "$val" 2>/dev/null \
        || xfconf-query -c xfce4-panel -p "$prop" -s "$val" 2>/dev/null || true
done
xfce4-panel -r >/dev/null 2>&1 || true
touch "$stamp"
