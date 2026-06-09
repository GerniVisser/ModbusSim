#!/usr/bin/env bash
# One-time setup for the ModbusSim VM environment.
# Run once as root after cloning the repo, then take your base snapshot.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="modbus-sim-gui"
LOGIN_USER="png"

echo "=== ModbusSim Install ==="

# ---- 1. Virtualenv -------------------------------------------------------
if [ ! -f "$REPO/.venv/bin/python" ]; then
    echo "Creating virtualenv…"
    python3 -m venv "$REPO/.venv"
    "$REPO/.venv/bin/pip" install -q -r "$REPO/requirements.txt"
else
    echo "Virtualenv already exists, skipping."
fi

# ---- 2. Systemd service --------------------------------------------------
echo "Installing systemd service…"
cp "$REPO/systemd/$SERVICE_NAME.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

# ---- 3. Auto-login (GDM3) ------------------------------------------------
GDM_CONF=/etc/gdm3/custom.conf
if [ -f "$GDM_CONF" ]; then
    echo "Configuring auto-login in $GDM_CONF…"
    # Uncomment AutomaticLoginEnable and set AutomaticLogin user.
    sed -i "s/^#\?\s*AutomaticLoginEnable\s*=.*/AutomaticLoginEnable = true/" "$GDM_CONF"
    sed -i "s/^#\?\s*AutomaticLogin\s*=.*/AutomaticLogin = $LOGIN_USER/" "$GDM_CONF"
else
    echo "WARNING: $GDM_CONF not found — auto-login not configured."
    echo "  If using LightDM: set autologin-user=$LOGIN_USER in /etc/lightdm/lightdm.conf"
fi

# ---- 4. Disable screen lock / sleep (requires the user session to be running)
# These settings are applied when the service starts and the user is logged in.
# If DBUS is not available yet, this silently skips.
USER_ID="$(id -u "$LOGIN_USER" 2>/dev/null || echo "")"
if [ -n "$USER_ID" ] && [ -S "/run/user/$USER_ID/bus" ]; then
    echo "Disabling screen lock and sleep…"
    sudo -u "$LOGIN_USER" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_ID/bus" \
        gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null || true
    sudo -u "$LOGIN_USER" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_ID/bus" \
        gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0 2>/dev/null || true
    sudo -u "$LOGIN_USER" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$USER_ID/bus" \
        gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-battery-timeout 0 2>/dev/null || true
else
    echo "NOTE: User session not active — screen lock/sleep settings not applied."
    echo "  Log in as $LOGIN_USER and run:"
    echo "    gsettings set org.gnome.desktop.screensaver lock-enabled false"
    echo "    gsettings set org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout 0"
fi

# ---- 5. Start the service now --------------------------------------------
echo "Starting $SERVICE_NAME…"
systemctl start "$SERVICE_NAME"

echo ""
echo "Install complete."
echo "  UI available at: http://192.168.99.2/"
echo "  Service status : systemctl status $SERVICE_NAME"
echo "  Live logs      : journalctl -u $SERVICE_NAME -f"
