# SPDX-License-Identifier: GPL-2.0
"""Keep the machine from sleeping while something plays: a logind
(systemd-logind or elogind) inhibitor, "sleep:idle", held as a file
descriptor and let go by closing it. Without logind it does nothing.

    awake = Awake("Музыка", "plays music")
    awake.hold()      # again: nothing more
    awake.release()

inhibit may be given (tests): inhibit(what, who, why, mode) -> an fd.
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


class Awake:
    def __init__(self, who, why, inhibit=None):
        self.who, self.why = who, why
        self.inhibit = inhibit or logind_inhibit
        self.fd = None

    @property
    def held(self):
        return self.fd is not None

    def hold(self):
        if self.fd is not None:
            return True
        try:
            self.fd = self.inhibit("sleep:idle", self.who, self.why, "block")
        except (GLib.Error, OSError):
            self.fd = None
        return self.fd is not None

    def release(self):
        if self.fd is None:
            return
        try:
            os.close(self.fd)
        except OSError:
            pass
        self.fd = None
