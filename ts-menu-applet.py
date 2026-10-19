#!/usr/bin/env python3
"""Tray indicator showing Tailscale status."""
import json
import signal
from dataclasses import dataclass
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
from gi.repository import Gio, GLib, Gtk

APP_ID = "tailscale-menu-applet"
POLL_SECONDS = 5

ICON_DIR = Path(__file__).resolve().parent / "icons"
DISCONNECTED_ICON = str(ICON_DIR / "ts-disconnected.svg")
CONNECTED_ICON = str(ICON_DIR / "ts-connected.svg")
EXITNODE_ICON = str(ICON_DIR / "ts-connected-exitnode.svg")


@dataclass(frozen=True)
class Status:
    connected: bool
    exit_node: str | None = None
    detail: str | None = None  # why we're disconnected, if known

    @property
    def label(self):
        if not self.connected:
            return f"Disconnected ({self.detail})" if self.detail else "Disconnected"
        if self.exit_node:
            return f"Connected – exit node: {self.exit_node}"
        return "Connected"

    @property
    def icon(self):
        if not self.connected:
            return DISCONNECTED_ICON
        return EXITNODE_ICON if self.exit_node else CONNECTED_ICON


def short_name(peer):
    """MagicDNS short name (what `tailscale status` shows), else HostName."""
    dns = peer.get("DNSName", "").split(".", 1)[0]
    return dns or peer.get("HostName") or "unknown"


def parse_status(data):
    state = data.get("BackendState")
    if state != "Running":
        detail = "needs login" if state == "NeedsLogin" else None
        return Status(connected=False, detail=detail)

    exit_status = data.get("ExitNodeStatus")
    if not exit_status:
        return Status(connected=True)

    for peer in (data.get("Peer") or {}).values():
        if peer.get("ExitNode") or peer.get("ID") == exit_status.get("ID"):
            return Status(connected=True, exit_node=short_name(peer))
    # Exit node is set but not in the peer list; fall back to its IP
    ips = exit_status.get("TailscaleIPs") or ["unknown"]
    return Status(connected=True, exit_node=ips[0].split("/")[0])


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

        self.status = None
        self.check_in_flight = False
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self.refresh)

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

    def refresh(self):
        """Start an async `tailscale status --json`; never blocks the UI."""
        if not self.check_in_flight:
            try:
                proc = Gio.Subprocess.new(
                    ["tailscale", "status", "--json"],
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE,
                )
            except GLib.Error:
                self.apply_status(Status(connected=False, detail="tailscale not found"))
            else:
                self.check_in_flight = True
                proc.communicate_utf8_async(None, None, self.on_status_output)
        return GLib.SOURCE_CONTINUE

    def on_status_output(self, proc, result):
        self.check_in_flight = False
        try:
            _ok, stdout, _stderr = proc.communicate_utf8_finish(result)
            # Exit code is non-zero in some states (e.g. stopped) but the
            # JSON is still valid, so parse rather than check the code.
            status = parse_status(json.loads(stdout))
        except (GLib.Error, ValueError, AttributeError):
            status = Status(connected=False, detail="tailscaled not responding")
        self.apply_status(status)

    def apply_status(self, status):
        if status == self.status:
            return
        self.status = status
        self.status_item.set_label(status.label)
        self.set_icon(status.icon, status.label)

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
