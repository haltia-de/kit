# SPDX-License-Identifier: GPL-2.0
"""Keep the machine from sleeping while something plays.

    awake = Awake("Музыка", "plays music")
    awake.hold()      # again: nothing more
    awake.release()

First the standard way, the running Gtk.Application's inhibit (suspend and
idle): GTK takes it to the Inhibit portal inside Flatpak and to the session
manager on a desktop. Where nobody answers that (the Haltia tablet has no
session manager and no portals), a logind inhibitor (systemd-logind or
elogind), "sleep:idle", held as a file descriptor and let go by closing it.
Without either it does nothing.

app and inhibit may be given (tests): app has inhibit(window, flags, reason)
-> a cookie (0 when refused) and uninhibit(cookie); inhibit(what, who, why,
mode) -> an fd. app=False skips the application way.
"""
import os

from gi.repository import Gio, GLib


def logind_inhibit(what, who, why, mode):
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    res, fds = bus.call_with_unix_fd_list_sync(
        "org.freedesktop.login1", "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager", "Inhibit",
        GLib.Variant("(ssss)", (what, who, why, mode)), GLib.VariantType("(h)"),
        Gio.DBusCallFlags.NONE, 5000, None, None)
    return fds.get(res.unpack()[0])


def _flags():
    """Gtk's SUSPEND | IDLE, without making GTK a must for this module."""
    try:
        from gi.repository import Gtk
        f = Gtk.ApplicationInhibitFlags
        return f.SUSPEND | f.IDLE
    except (ImportError, ValueError, AttributeError):
        return 4 | 8


class Awake:
    def __init__(self, who, why, inhibit=None, app=None):
        self.who, self.why = who, why
        self.inhibit = inhibit or logind_inhibit
        self.app = app
        self.fd = None
        self.cookie = 0
        self.holder = None  # the application that gave the cookie

    @property
    def held(self):
        return self.fd is not None or bool(self.cookie)

    def _application(self):
        if self.app is False:
            return None
        app = self.app or Gio.Application.get_default()
        return app if app is not None and hasattr(app, "inhibit") else None

    def hold(self):
        if self.held:
            return True
        app = self._application()
        if app is not None:
            try:
                self.cookie = app.inhibit(None, _flags(), self.why) or 0
            except (GLib.Error, TypeError):
                self.cookie = 0
            if self.cookie:
                self.holder = app
                return True
        try:
            self.fd = self.inhibit("sleep:idle", self.who, self.why, "block")
        except (GLib.Error, OSError):
            self.fd = None
        return self.fd is not None

    def release(self):
        if self.cookie:
            try:
                self.holder.uninhibit(self.cookie)
            except (GLib.Error, TypeError):
                pass
            self.cookie, self.holder = 0, None
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
