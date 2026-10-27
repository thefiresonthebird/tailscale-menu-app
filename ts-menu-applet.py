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
class ExitNodeOption:
    name: str
    ip: str
    online: bool
    active: bool


@dataclass(frozen=True)
class Status:
    connected: bool
    exit_node: str | None = None
    detail: str | None = None  # why we're disconnected, if known
    exit_nodes: tuple[ExitNodeOption, ...] = ()

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


def strip_prefix(ip):
    return ip.split("/")[0]


def parse_exit_nodes(peers, exit_status):
    """Peers that advertise themselves as exit nodes, sorted by name."""
    active_id = (exit_status or {}).get("ID")
    options = []
    for peer in peers:
        ips = peer.get("TailscaleIPs")
        if not peer.get("ExitNodeOption") or not ips:
            continue
        options.append(ExitNodeOption(
            name=short_name(peer),
            ip=strip_prefix(ips[0]),
            online=bool(peer.get("Online")),
            active=bool(peer.get("ExitNode") or (active_id and peer.get("ID") == active_id)),
        ))
    return tuple(sorted(options, key=lambda o: o.name.lower()))


def parse_status(data):
    peers = list((data.get("Peer") or {}).values())
    exit_status = data.get("ExitNodeStatus")
    exit_nodes = parse_exit_nodes(peers, exit_status)

    state = data.get("BackendState")
    if state != "Running":
        detail = "needs login" if state == "NeedsLogin" else None
        return Status(connected=False, detail=detail, exit_nodes=exit_nodes)

    if not exit_status:
        return Status(connected=True, exit_nodes=exit_nodes)

    for option in exit_nodes:
        if option.active:
            return Status(connected=True, exit_node=option.name, exit_nodes=exit_nodes)
    # Exit node is set but not in the peer list; fall back to its IP
    ips = exit_status.get("TailscaleIPs") or ["unknown"]
    return Status(connected=True, exit_node=strip_prefix(ips[0]), exit_nodes=exit_nodes)


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
        self.command_in_flight = False
        self.refresh()
        GLib.timeout_add_seconds(POLL_SECONDS, self.refresh)

    def build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Loading...")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        self.connect_item = Gtk.CheckMenuItem(label="Connect")
        self.connect_handler = self.connect_item.connect("toggled", self.on_connect_toggled)
        menu.append(self.connect_item)

        self.exit_node_item = Gtk.MenuItem(label="Exit Node")
        self.exit_node_item.set_submenu(Gtk.Menu())
        menu.append(self.exit_node_item)

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
        self.sync_toggle()
        self.rebuild_exit_node_menu()

    def rebuild_exit_node_menu(self):
        """Recreate the submenu; items are set up before handlers connect,
        so marking the active node doesn't trigger a command."""
        submenu = Gtk.Menu()
        nodes = self.status.exit_nodes if self.status else ()
        active = None

        none_item = Gtk.RadioMenuItem(label="None")
        none_item.set_active(not any(n.active for n in nodes))
        none_item.connect("activate", self.on_exit_node_chosen, "")
        submenu.append(none_item)
        if nodes:
            submenu.append(Gtk.SeparatorMenuItem())

        for node in nodes:
            label = node.name if node.online else f"{node.name} (offline)"
            item = Gtk.RadioMenuItem(label=label, group=none_item)
            item.set_active(node.active)
            item.set_sensitive(node.online or node.active)
            item.connect("activate", self.on_exit_node_chosen, node.ip)
            submenu.append(item)

        submenu.show_all()
        self.exit_node_item.set_submenu(submenu)
        self.exit_node_item.set_sensitive(not self.command_in_flight)

    def on_exit_node_chosen(self, item, ip):
        # Radio items fire "activate" for both the item turning on and the
        # one turning off; only act on the newly selected one.
        if item.get_active():
            self.run_tailscale(["set", f"--exit-node={ip}"])

    def sync_toggle(self):
        """Reflect the real connection state without re-triggering the handler."""
        connected = bool(self.status and self.status.connected)
        with self.connect_item.handler_block(self.connect_handler):
            self.connect_item.set_active(connected)
        self.connect_item.set_sensitive(not self.command_in_flight)

    def on_connect_toggled(self, item):
        self.run_tailscale(["up" if item.get_active() else "down"])

    def set_controls_sensitive(self, sensitive):
        self.connect_item.set_sensitive(sensitive)
        self.exit_node_item.set_sensitive(sensitive)

    def run_tailscale(self, args):
        """Run a state-changing tailscale command asynchronously."""
        if self.command_in_flight:
            return
        cmd = ["tailscale", *args]
        try:
            proc = Gio.Subprocess.new(
                cmd,
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_PIPE,
            )
        except GLib.Error as e:
            self.on_command_failed(cmd, e.message)
            return
        self.command_in_flight = True
        self.set_controls_sensitive(False)
        proc.communicate_utf8_async(None, None, self.on_command_done, cmd)

    def on_command_done(self, proc, result, cmd):
        self.command_in_flight = False
        try:
            _ok, _stdout, stderr = proc.communicate_utf8_finish(result)
        except GLib.Error as e:
            self.on_command_failed(cmd, e.message)
            return
        if proc.get_exit_status() != 0:
            self.on_command_failed(cmd, stderr.strip() or "unknown error")
            return
        self.set_controls_sensitive(True)
        self.refresh()

    def on_command_failed(self, cmd, message):
        print(f"{' '.join(cmd)} failed: {message}", flush=True)
        # Snap the controls back to whatever the daemon actually reports
        self.sync_toggle()
        self.rebuild_exit_node_menu()
        self.refresh()

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
