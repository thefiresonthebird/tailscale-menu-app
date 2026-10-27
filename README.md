# Tailscale Menu App
 A lightweight Linux system tray indicator that shows your Tailscale connection
 status at a glance, including which exit node you're using, if any. Written for, and only tested on Ubuntu (26.04).

 ## Installation

### Requirements
```sh
# Debian / Ubuntu
sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1
```

### Run on startup
- Create `~/.config/autostart/tailscale-menu-applet.desktop`:
```ini
[Desktop Entry]
Type=Application
Name=Tailscale Menu
Comment=Tray indicator showing Tailscale status
Exec=/usr/bin/python3 /path/to/tailscale-menu-app/ts-menu-applet.py
Icon=/path/to/tailscale-menu-app/icons/ts-connected.svg
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=3
NoDisplay=false
```
- Log out/in to launch
- Run `gio launch ~/.config/autostart/tailscale-menu-applet.desktop` to test

