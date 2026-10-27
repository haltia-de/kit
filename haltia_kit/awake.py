# SPDX-License-Identifier: GPL-2.0
"""Keep the machine from sleeping while something plays.

    awake = Awake("Музыка", "plays music")
    awake.hold()      # again: nothing more
    awake.release()

    awake.hold_async(done)   # the same without blocking: done(held) later

First the standard way, the running Gtk.Application's inhibit (suspend and
idle): GTK takes it to the Inhibit portal inside Flatpak and to the session
manager on a desktop. Where nobody answers that (the Haltia tablet has no
session manager and no portals), a logind inhibitor (systemd-logind or
elogind), "sleep:idle", held as a file descriptor and let go by closing it.
Without either it does nothing.

hold() blocks: logind is asked on the system bus, synchronously, for up to
five seconds when the bus does not answer. On the GTK thread use
hold_async(done=None): the bus and Manager.Inhibit are asked with Gio's
asynchronous calls, and done(held) comes on the GLib main context that was
the thread's default when hold_async was called, never from inside
hold_async itself. held says whether this Awake holds its inhibitor when
done runs. Calls while one is under way join it (one request, every done
told). release() while it is under way cancels it: an fd that comes
afterwards is closed at once, and done gets False. A hold() that took the
inhibitor meanwhile wins the same way. A failure is not retried; the next
hold or hold_async asks again. The application way (Gtk.Application.inhibit)
has no asynchronous form in GTK 3 and is tried first, as by hold(); with
app=False only logind is asked.

app, inhibit and inhibit_async may be given (tests): app has
inhibit(window, flags, reason) -> a cookie (0 when refused) and
uninhibit(cookie); inhibit(what, who, why, mode) -> an fd;
inhibit_async(what, who, why, mode, done, cancellable) -> calls
done(fd or None, error or None) later, on the caller's context. A given
inhibit without inhibit_async is used by hold_async too (its answer
reported later). app=False skips the application way.
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


def _system_bus_async(cancellable, done):
    def got(_source, res):
        try:
            bus = Gio.bus_get_finish(res)
        except GLib.Error as exc:
            done(None, exc)
            return
        done(bus, None)
    Gio.bus_get(Gio.BusType.SYSTEM, cancellable, got)


def logind_inhibit_async(what, who, why, mode, done, cancellable=None, bus=None):
    """logind_inhibit without blocking: done(fd, None) or done(None, error)
    on the thread-default main context of the caller. bus: a connection to
    ask instead of the system bus (tests). An fd that arrives once
    cancellable is cancelled is closed, and done gets the error instead."""
    def answered(conn, res):
        try:
            reply, fds = conn.call_with_unix_fd_list_finish(res)
        except GLib.Error as exc:
            done(None, exc)
            return
        try:
            index = reply.unpack()[0]
            got = fds.steal_fds() if fds is not None else []
        except (GLib.Error, TypeError, ValueError) as exc:
            done(None, exc)
            return
        fd = got[index] if 0 <= index < len(got) else None
        for other in got:  # anything else the reply carried is not ours to keep
            if other != fd:
                _close(other)
        if fd is None:
            done(None, OSError("logind gave no fd"))
        elif cancellable is not None and cancellable.is_cancelled():
            _close(fd)
            done(None, GLib.Error("cancelled"))
        else:
            done(fd, None)

    def call(conn, error):
        if conn is None:
            done(None, error)
            return
        conn.call_with_unix_fd_list(
            "org.freedesktop.login1", "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager", "Inhibit",
            GLib.Variant("(ssss)", (what, who, why, mode)), GLib.VariantType("(h)"),
            Gio.DBusCallFlags.NONE, 5000, None, cancellable, answered)

    if bus is not None:
        call(bus, None)
    else:
        _system_bus_async(cancellable, call)


def _close(fd):
    try:
        os.close(fd)
    except OSError:
        pass


def _later(context, fn, *args):
    """fn(*args) from context's main loop, not from here."""
    source = GLib.Idle()
    source.set_callback(lambda *_a: (fn(*args), False)[1])
    source.attach(context)


class _Request:
    """One logind request under way, and the done callbacks waiting on it."""
    def __init__(self, generation, context):
        self.generation = generation
        self.context = context
        self.waiters = []
        self.cancellable = Gio.Cancellable()


def _flags():
    """Gtk's SUSPEND | IDLE, without making GTK a must for this module."""
    try:
        from gi.repository import Gtk
        f = Gtk.ApplicationInhibitFlags
        return f.SUSPEND | f.IDLE
    except (ImportError, ValueError, AttributeError):
        return 4 | 8


class Awake:
    def __init__(self, who, why, inhibit=None, app=None, inhibit_async=None):
        self.who, self.why = who, why
        self.inhibit = inhibit or logind_inhibit
        if inhibit_async is None and inhibit is None:
            inhibit_async = logind_inhibit_async
        self.inhibit_async = inhibit_async
        self.generation = 0  # a release or a newer holder makes older requests stale
        self.pending = None  # the _Request under way
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

    def _hold_app(self):
        app = self._application()
        if app is not None:
            try:
                self.cookie = app.inhibit(None, _flags(), self.why) or 0
            except (GLib.Error, TypeError):
                self.cookie = 0
            if self.cookie:
                self.holder = app
                return True
        return False

    def hold(self):
        if self.held:
            return True
        if not self._hold_app():
            try:
                self.fd = self.inhibit("sleep:idle", self.who, self.why, "block")
            except (GLib.Error, OSError):
                self.fd = None
        if self.held:
            self._cancel()  # a request under way: its fd is closed when it comes
        return self.held

    def hold_async(self, done=None):
        """Take the inhibitor without blocking on the system bus; done(held)
        later, on the calling thread's default main context."""
        context = GLib.MainContext.ref_thread_default()
        if self.pending is not None:
            if done is not None:
                self.pending.waiters.append(done)
            return
        if self.held or self._hold_app():
            if done is not None:
                _later(context, done, True)
            return
        request = self.pending = _Request(self.generation, context)
        if done is not None:
            request.waiters.append(done)
        if self.inhibit_async is None:  # a caller's own synchronous inhibit
            def ask():
                try:
                    fd = self.inhibit("sleep:idle", self.who, self.why, "block")
                except (GLib.Error, OSError):
                    fd = None
                self._answered(request, fd)
            _later(context, ask)
            return
        try:
            self.inhibit_async("sleep:idle", self.who, self.why, "block",
                               lambda fd, _error: self._answered(request, fd),
                               request.cancellable)
        except (GLib.Error, OSError, TypeError):
            _later(context, self._answered, request, None)

    def _answered(self, request, fd):
        if self.pending is request:
            self.pending = None
        if fd is not None:
            if request.generation != self.generation or self.held:
                _close(fd)  # released or replaced meanwhile: never held again
            else:
                self.fd = fd
        held = self.held
        for done in request.waiters:
            done(held)

    def _cancel(self):
        self.generation += 1
        request, self.pending = self.pending, None
        if request is not None:
            request.cancellable.cancel()

    def release(self):
        self._cancel()
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
