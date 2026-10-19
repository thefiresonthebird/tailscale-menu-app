#!/usr/bin/env python3
"""Tray indicator showing Tailscale status."""
import signal
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
try:
    # Ayatana is the maintained fork; libappindicator3 is deprecated
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except ValueError:
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator
from gi.repository import GLib, Gtk

APP_ID = "tailscale-menu-applet"

ICON_DIR = Path(__file__).resolve().parent / "icons"
DISCONNECTED_ICON = str(ICON_DIR / "ts-disconnected.svg")
CONNECTED_ICON = str(ICON_DIR / "ts-connected.svg")
EXITNODE_ICON = str(ICON_DIR / "ts-connected-exitnode.svg")


class TailscaleIndicator:
    def __init__(self):
        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            DISCONNECTED_ICON,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_title("Tailscale")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_menu(self.build_menu())

    def build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Loading...")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", self.on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def set_icon(self, path, description):
        self.indicator.set_icon_full(path, description)

    def on_quit(self, _item=None):
        Gtk.main_quit()


def main():
    app = TailscaleIndicator()
    # Let Ctrl+C / SIGTERM stop the GTK main loop
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, app.on_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
