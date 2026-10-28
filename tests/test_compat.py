#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""haltia-kit through its interface: settings, awake, the fake player,
the player's awake rule, kinds, thumbnails, pictures, the vault, the camera,
formats, sharing, crash records. Run: python3 tests/test_compat.py, or python3 -m unittest from the
tests folder (PyGObject with GdkPixbuf, Pillow; no display, no libmpv, no
sound card, no keyring, no camera needed). The tests run in the order of
their numbers: later ones use the files of earlier ones."""
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zlib

top = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = os.path.join(top, "cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(top, "config")
os.environ["XDG_STATE_HOME"] = os.path.join(top, "state")  # the crash records
# The shell's rotation file, never the real one: its folder here, no file yet.
os.environ["XDG_RUNTIME_DIR"] = os.path.join(top, "xdg-run")
os.makedirs(os.path.join(top, "xdg-run", "haltia"), mode=0o700)
# The programs share sends to: fake .desktop files, made before GLib first
# reads the folders (the system's stay after them, for the MIME database).
os.environ["XDG_DATA_HOME"] = os.path.join(top, "data")
os.environ["XDG_DATA_DIRS"] = os.path.join(top, "share") + ":" + \
    (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share")
received = os.path.join(top, "received")
receiver = os.path.join(top, "receiver")
with open(receiver, "w") as fh:
    fh.write('#!/bin/sh\nprintf "%%s\\n" "$@" > %s.part && mv %s.part %s\n'
             % (received, received, received))
os.chmod(receiver, 0o755)
os.makedirs(os.path.join(top, "share", "applications"))
for _id, _name, _types, _share in (("Photos", "Фото", "image/*;", True),
                                   ("Notes", "Заметки", "text/plain;image/png;", True),
                                   ("Plain", "Просмотр", "image/*;", False),
                                   ("Self", "Сам", "image/*;", True)):
    with open(os.path.join(top, "share", "applications",
                           "test.share.%s.desktop" % _id), "w") as fh:
        fh.write("[Desktop Entry]\nType=Application\nName=%s\nExec=%s %%F\n"
                 "MimeType=%s\n%s" % (_name, receiver, _types,
                                      "X-Haltia-Share=true\n" if _share else ""))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402
from PIL import Image  # noqa: E402

from haltia_kit import formats, photos, settings, sound, testing  # noqa: E402
from haltia_kit.awake import Awake  # noqa: E402

# The files the thumbnails make and the pictures read again.
pic = os.path.join(top, "Кот 1.png")
song = os.path.join(top, "song.mp3")
huge = os.path.join(top, "huge.png")  # a PNG claiming 20000 x 20000 pixels


def broken(*_a):
    raise OSError("no logind")


class FakeApp:
    """Gtk.Application's inhibit: a cookie, or 0 when nobody answers."""
    def __init__(self, answer):
        self.answer, self.cookies = answer, set()

    def inhibit(self, window, flags, reason):
        if not self.answer:
            return 0
        self.cookies.add(len(self.cookies) + 1)
        return len(self.cookies)

    def uninhibit(self, cookie):
        self.cookies.discard(cookie)


class FakeMpv:
    def __init__(self):
        self.pause = False
        self.played = []

    def play(self, what):
        self.played.append(what)

    def stop(self):
        pass


class End:
    class data:
        reason = "eof"


class DeadReader:
    calls = 0

    def call(self, _req):
        DeadReader.calls += 1
        return None


class FakeLogind:
    """logind's Manager.Inhibit on a peer-to-peer D-Bus connection of its
    own (no bus daemon), served from a thread with its own main context,
    returning real fds: the write end of a pipe whose read end it keeps,
    so released(i) sees every copy of the i-th fd closed. delay: seconds
    before an answer; refuse: answer with an error."""
    def __init__(self, delay=0.0):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.delay, self.refuse = delay, False
        self.readers, self.answered = [], 0
        self.address = "unix:path=" + os.path.join(tempfile.mkdtemp(dir=top), "bus")
        ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, args=(ready,), daemon=True)
        self.thread.start()
        assert ready.wait(5)
        self.client = Gio.DBusConnection.new_for_address_sync(
            self.address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT, None, None)

    def _serve(self, ready):
        Gio, GLib = self.Gio, self.GLib
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.loop = GLib.MainLoop.new(self.context, False)
        info = Gio.DBusNodeInfo.new_for_xml(
            '<node><interface name="org.freedesktop.login1.Manager">'
            '<method name="Inhibit"><arg type="s" direction="in"/>'
            '<arg type="s" direction="in"/><arg type="s" direction="in"/>'
            '<arg type="s" direction="in"/><arg type="h" direction="out"/>'
            '</method></interface></node>').interfaces[0]
        self.conns = []

        def connected(_server, conn):
            self.conns.append(conn)
            conn.register_object("/org/freedesktop/login1", info, self._inhibit, None, None)
            return True
        self.server = Gio.DBusServer.new_sync(self.address, Gio.DBusServerFlags.NONE,
                                              Gio.dbus_generate_guid(), None, None)
        self.server.connect("new-connection", connected)
        self.server.start()
        ready.set()
        self.loop.run()
        self.server.stop()
        for conn in self.conns:
            try:
                conn.close_sync(None)
            except GLib.Error:
                pass  # the client closed it first
        self.context.pop_thread_default()

    def _inhibit(self, _conn, _sender, _path, _iface, _method, _params, invocation):
        def answer():
            if self.refuse:
                invocation.return_dbus_error("org.freedesktop.login1.Error.Failed", "test")
            else:
                r, w = os.pipe2(os.O_NONBLOCK | os.O_CLOEXEC)
                self.readers.append(r)
                fds = self.Gio.UnixFDList.new()
                index = fds.append(w)
                os.close(w)
                invocation.return_value_with_unix_fd_list(
                    self.GLib.Variant("(h)", (index,)), fds)
            self.answered += 1
            return False
        if self.delay:
            source = self.GLib.Timeout(int(self.delay * 1000))
            source.set_callback(lambda *_a: answer())
            source.attach(self.context)
        else:
            answer()

    def inhibit_async(self, *args):
        from haltia_kit import awake
        awake.logind_inhibit_async(*args, bus=self.client)

    def released(self, index=-1):
        try:
            return os.read(self.readers[index], 1) == b""
        except BlockingIOError:
            return False

    def leaked(self):
        """fds of this process on a pipe handed out, other than the readers."""
        pipes = {"pipe:[%d]" % os.fstat(r).st_ino for r in self.readers}
        mine = set(self.readers)
        out = []
        for name in os.listdir("/proc/self/fd"):
            try:
                if int(name) not in mine and os.readlink("/proc/self/fd/" + name) in pipes:
                    out.append(int(name))
            except OSError:
                pass  # the listing's own fd
        return out

    def close(self):
        self.client.close_sync(None)
        self.loop.quit()
        self.thread.join(5)
        for fd in self.readers:
            os.close(fd)




def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + \
        struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


APP = "io.github.haltia_de.Messenger"
vdir, vrun = os.path.join(top, "config", "haltia-messenger"), os.path.join(top, "run")


def vault(ring, **kw):
    from haltia_kit.vault import Vault
    return Vault(APP, vdir, key="account", keyring=ring, runtime_dir=vrun, **kw)


def pump(until, wait=10):
    from gi.repository import GLib
    end = time.time() + wait
    while not until() and time.time() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


def alive(pid):
    """A process running, not a zombie waiting to be reaped."""
    try:
        with open("/proc/%d/stat" % pid) as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def live_source(Gst):
    """make() of a camera that is videotestsrc."""
    def make():
        src = Gst.ElementFactory.make("videotestsrc", None)
        src.set_property("is-live", True)
        return src
    return make


def gst_or_skip(case):
    """The camera module and Gst, or the test skipped."""
    from haltia_kit import camera
    Gst = camera.Gst
    if Gst is None or Gst.ElementFactory.find("videotestsrc") is None:
        case.skipTest("no GStreamer or no videotestsrc")
    return camera, Gst


class Compat(unittest.TestCase):
    def test_01_settings_moved(self):
        """A file from before the rename moves to ~/.config/haltia."""
        os.makedirs(os.path.join(top, "config", "b8k"))
        with open(os.path.join(top, "config", "b8k", "news.conf"), "w") as fh:
            fh.write("[news]\nbuzz = no\n")
        n = settings.Settings("news")
        assert n.path == os.path.join(top, "config", "haltia", "news.conf")
        assert n.get_bool("buzz", True) is False
        assert not os.path.exists(os.path.join(top, "config", "b8k", "news.conf"))

    def test_02_old_name_and_folders(self):
        import b8k_compat.settings as old_settings
        from b8k_compat import formats as old_formats
        from haltia_kit import paths
        assert old_settings is settings and old_formats is formats
        os.makedirs(os.path.join(top, "data", "b8k-max", "photos"))
        moved = paths.migrate_dirs("b8k-max", "haltia-max", bases=[os.path.join(top, "data")])
        assert moved == [os.path.join(top, "data", "haltia-max")]
        assert os.path.isdir(os.path.join(top, "data", "haltia-max", "photos"))
        assert os.path.islink(os.path.join(top, "data", "b8k-max"))
        assert os.path.isdir(os.path.join(top, "data", "b8k-max", "photos"))
        assert paths.migrate_dirs("b8k-max", "haltia-max",
                                  bases=[os.path.join(top, "data")]) == []

    def test_03_settings(self):
        """The old yes/no files read as they are."""
        s = settings.Settings("max")
        assert s.path == os.path.join(top, "config", "haltia", "max.conf")
        assert s.get_bool("telemetry", True) is True and s.get("folder", "x") == "x"
        os.makedirs(os.path.dirname(s.path), exist_ok=True)
        with open(s.path, "w") as fh:
            fh.write("[max]\ntelemetry = no\nbuzz = yes\nfolder = /home/user/Загрузки/MAX\n"
                     "odd = maybe\n")
        assert s.get_bool("telemetry", True) is False and s.get_bool("buzz", False) is True
        assert s.get_bool("odd", True) is True  # unreadable: the default
        assert s.get("folder") == "/home/user/Загрузки/MAX"
        assert s.set("buzz", False) and s.set("quality", "high")
        with open(s.path) as fh:
            assert "buzz = no" in fh.read()
        assert settings.Settings("max").get("quality") == "high"
        with open(s.path, "w") as fh:
            fh.write("not an ini file at all")
        assert s.get_bool("buzz", True) is True  # a broken file gives defaults

    def test_04_awake(self):
        """An inhibitor fd held once, let go."""
        inh = testing.FakeInhibit()
        a = Awake("Музыка", "plays", inhibit=inh)
        assert a.hold() and a.hold() and inh.held == 1 and a.held
        a.release()
        assert inh.held == 0 and not a.held
        assert Awake("x", "y", inhibit=broken).hold() is False  # without logind: nothing
        # The standard way first: the application's inhibit (portal, session manager).
        app = FakeApp(True)
        inh = testing.FakeInhibit()
        a = Awake("Музыка", "plays", inhibit=inh, app=app)
        assert a.hold() and a.hold() and len(app.cookies) == 1 and inh.held == 0 and a.held
        a.release()
        assert not app.cookies and not a.held
        # Nobody answers it (the tablet): logind.
        a = Awake("Музыка", "plays", inhibit=inh, app=FakeApp(False))
        assert a.hold() and inh.held == 1
        a.release()
        assert inh.held == 0 and not a.held

    def test_04b_awake_async(self):
        """hold_async: logind asked without blocking the main loop, real fds
        over D-Bus; requests joined, cancelled by release, never held late."""
        import gc
        from gi.repository import GLib
        fake = FakeLogind(delay=0.3)
        try:
            def awake(**kw):
                return Awake("Звонок", "call", app=False, inhibit_async=fake.inhibit_async, **kw)
            # A slow answer: the main loop keeps running, every waiter is told.
            a, seen, ticks = awake(), [], []
            tick = GLib.timeout_add(20, lambda: (ticks.append(1), True)[1])
            start = time.monotonic()
            a.hold_async(seen.append)
            a.hold_async(seen.append)  # joins the request under way
            assert time.monotonic() - start < 0.1 and not seen and not a.held
            pump(lambda: seen)
            GLib.source_remove(tick)
            assert seen == [True, True] and a.held and fake.answered == 1, seen
            assert fake.leaked() == [a.fd]  # the one copy, held
            assert len(ticks) >= 5, ticks  # the loop ran while logind thought
            a.hold_async(seen.append)  # held already: True, later, not from inside
            assert seen == [True, True]
            pump(lambda: len(seen) == 3)
            assert seen[2] is True and fake.answered == 1
            a.release()
            a.release()  # twice: nothing more
            assert fake.released(0) and not a.held
            # Released before the answer: the late fd is closed, never held.
            seen = []
            a.hold_async(seen.append)
            a.release()
            pump(lambda: seen)
            assert seen == [False] and not a.held
            pump(lambda: fake.answered == 2)
            pump(lambda: fake.released(1), wait=2)
            assert fake.released(1) and not a.held
            # A newer request after a release: the old answer never overwrites it.
            seen, newer = [], []
            a.hold_async(seen.append)
            a.release()
            a.hold_async(newer.append)
            pump(lambda: seen and newer)
            pump(lambda: fake.answered == 4)
            gc.collect()
            assert seen == [False] and newer == [True] and a.held
            assert fake.released(2) and not fake.released(3)
            a.release()
            assert fake.released(3)
            # A synchronous hold that won meanwhile: the late fd is closed.
            inh = testing.FakeInhibit()
            b, seen = awake(inhibit=inh), []
            b.inhibit_async = fake.inhibit_async
            b.hold_async(seen.append)
            assert b.hold() and inh.held == 1
            pump(lambda: seen)
            pump(lambda: fake.answered == 5)
            gc.collect()
            assert seen == [True] and b.fd == inh.fds[0][0] and fake.released(4)
            b.release()
            # Refused: False, no retry by itself; a later hold asks again.
            fake.refuse, seen = True, []
            c = awake()
            c.hold_async(seen.append)
            pump(lambda: seen)
            time.sleep(0.4)
            assert seen == [False] and not c.held and fake.answered == 6
            fake.refuse = False
            c.hold_async(seen.append)
            pump(lambda: len(seen) == 2)
            assert seen == [False, True] and c.held and fake.answered == 7
            c.release()
            assert fake.released(5)
            # A caller's own synchronous inhibit serves hold_async too, later.
            inh, seen = testing.FakeInhibit(), []
            d = Awake("x", "y", inhibit=inh, app=False)
            d.hold_async(seen.append)
            assert not seen and inh.held == 0
            pump(lambda: seen)
            assert seen == [True] and inh.held == 1
            d.release()
            assert inh.held == 0
            # The application way first, as hold() does.
            app, seen = FakeApp(True), []
            e = Awake("x", "y", app=app, inhibit_async=fake.inhibit_async)
            e.hold_async(seen.append)
            pump(lambda: seen)
            assert seen == [True] and app.cookies and fake.answered == 7
            e.release()
            assert not app.cookies
            gc.collect()
            assert fake.answered == len(fake.readers) + 1  # one refusal
            assert all(fake.released(i) for i in range(len(fake.readers)))
            assert not fake.leaked()  # no fd left behind
        finally:
            fake.close()

    def test_05_player(self):
        """Awake only while playing and not paused (no libmpv: fake mpv)."""
        inh = testing.FakeInhibit()
        ended = []
        p = sound.Player("Музыка", on_end=lambda: ended.append(1), post=lambda fn: fn(),
                         awake=Awake("Музыка", "plays", inhibit=inh))
        p.mpv = FakeMpv()  # in place of libmpv; the callbacks below are what libmpv calls
        p.play("/m/a.mp3")
        assert p.mpv.played == ["/m/a.mp3"] and inh.held == 1
        p._pause("pause", True)
        assert p.paused and inh.held == 0
        p._pause("pause", False)
        assert inh.held == 1
        p._end(End())
        assert ended == [1] and not p.playing and inh.held == 0
        p.play("/m/b.mp3")
        p.stop()
        assert inh.held == 0
        opt = sound.Player("x", options={"user_agent": "UA/1", "load_unsafe_playlists": True,
                                         "video": True})
        assert opt.options == {"user_agent": "UA/1"}  # the safe settings cannot be undone
        sys.modules["mpv"] = None  # libmpv missing: play() says so
        try:
            sound.Player("x", post=lambda fn: fn(), awake=Awake("x", "y", inhibit=inh)).play("/m/c")
            raise AssertionError("play without libmpv must say so")
        except RuntimeError:
            pass
        finally:
            del sys.modules["mpv"]

    def test_06_fake_player(self):
        """The fake player programs test with."""
        seen = []
        f = testing.FakePlayer(on_end=lambda: seen.append("end"), on_time=seen.append,
                               on_pause=lambda v: seen.append(("pause", v)),
                               on_error=lambda t: seen.append(("error", t)))
        f.play("/m/a.mp3", 5)
        assert f.what == "/m/a.mp3" and f.position == 5 and f.playing
        f.toggle()
        f.tick(7)
        f.finish()
        f.play("/m/b.mp3")
        f.fail()
        assert seen == [("pause", True), 7, "end", ("error", "не удалось проиграть")]
        assert [c for c, _ in f.calls] == ["play", "toggle", "play"]

    def test_07_thumbnails(self):
        """Kinds and thumbnails (freedesktop, shared with file managers)."""
        assert photos.kind_of("a.JPG") == "photo" and photos.kind_of("b.mkv") == "video"
        assert photos.kind_of("c.opus") == "audio" and photos.kind_of("d.txt") is None
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 1000, 500)
        pix.fill(0x3584e4ff)
        pix.savev(pic, "png", [], [])
        t = photos.thumbnail(pic)
        assert t and t.startswith(os.path.join(top, "cache", "thumbnails", "large"))
        tp = GdkPixbuf.Pixbuf.new_from_file(t)
        assert (tp.get_width(), tp.get_height()) == (256, 128)  # fitted, not cropped
        assert tp.get_option("tEXt::Thumb::URI").startswith("file://") and "%20" in \
            tp.get_option("tEXt::Thumb::URI")
        assert tp.get_option("tEXt::Thumb::MTime") == str(int(os.stat(pic).st_mtime))
        assert photos.thumbnail(pic) == t
        old = os.stat(t).st_mtime_ns
        os.utime(pic, (time.time() + 60, time.time() + 60))  # changed: made again
        assert photos.thumbnail(pic) == t and os.stat(t).st_mtime_ns != old
        with open(song, "wb") as fh:
            fh.write(b"not a real song")
        assert photos.thumbnail(song) is None
        assert os.listdir(os.path.join(top, "cache", "thumbnails", "fail", photos.FAIL))
        assert photos.thumbnail(song) is None  # the failure is remembered
        with open(huge, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 20000, 20000,
                                                                          8, 2, 0, 0, 0)) +
                     chunk(b"IEND", b""))
        try:
            photos.pixbuf_of(huge, 256)
            raise AssertionError("a huge picture must not be decoded")
        except ValueError:
            pass
        assert photos.thumbnail(huge) is None
        # the reader process: a file it dies or hangs on is given up on for good
        assert photos.media_info(song, "audio") == {"duration": None}  # parsed there, no tags
        real = photos._reader[:]
        photos._reader[:] = [DeadReader()]
        bomb = os.path.join(top, "bomb.jpg")
        with open(bomb, "wb") as fh:
            fh.write(b"\xff\xd8 pretend this kills the parser")
        assert photos.thumbnail(bomb) is None and DeadReader.calls == 1
        assert photos.thumbnail(bomb) is None and DeadReader.calls == 1  # not tried again
        assert photos.media_info(bomb.replace(".jpg", ".mp4")) == {"duration": None}
        photos._reader[:] = real
        from haltia_kit.worker import Worker

        w = Worker(timeout=5)
        assert w.call({"op": "nonsense"}) == {"error": "unknown op"}
        w.proc.kill()
        w.proc.wait()
        assert w.call({"op": "info", "path": song, "kind": "audio"}) == \
            {"info": {"duration": None}}
        w.close()
        sq = photos.square(tp, 64)
        assert (sq.get_width(), sq.get_height()) == (64, 64)

    def test_08_pictures(self):
        """Pillow in the reader process, never larger than needed."""
        from haltia_kit import pictures

        jpg = os.path.join(top, "Кот.jpg")
        Image.new("RGB", (3000, 2000), (200, 30, 30)).save(jpg)
        assert pictures.load(jpg, box=(800, 600)).size == (800, 533)
        assert pictures.load(jpg, size=5000).size == (3000, 2000)  # not scaled up
        turned = os.path.join(top, "turned.jpg")
        exif = Image.Exif()
        exif[0x0112] = 6  # taken on its side
        Image.new("RGB", (300, 200)).save(turned, exif=exif)
        assert pictures.load(turned, box=(100, 100)).size == (67, 100)  # upright
        see = os.path.join(top, "see.png")
        Image.new("RGBA", (40, 20), (0, 0, 0, 0)).save(see)
        os.environ["WAYLAND_DISPLAY"] = os.environ["DISPLAY"] = "nowhere"
        photos._reader[0].close()  # started again, with these
        pix = photos.pixbuf_of(jpg, box=(800, 600))
        assert (pix.get_width(), pix.get_height(), pix.get_has_alpha()) == (800, 533, False)
        assert abs(pix.get_pixels()[0] - 200) < 8  # its own colour, not noise
        pix = photos.pixbuf_of(pic, 256)
        assert (pix.get_width(), pix.get_height()) == (512, 256)
        assert photos.pixbuf_of(see, 256).get_has_alpha()
        with open("/proc/%d/environ" % photos._reader[0].proc.pid, "rb") as fh:
            env = fh.read().split(b"\0")
        assert not [e for e in env if e.startswith((b"WAYLAND_DISPLAY=", b"DISPLAY="))]
        for bad in (song, huge):
            try:
                photos.pixbuf_of(bad, 256)
                raise AssertionError("a bad picture must not be decoded")
            except ValueError:
                pass
        assert subprocess.run(  # what the reader imports loads no Gtk
            [sys.executable, "-c", "import sys; import haltia_kit.photos, haltia_kit.worker; "
             "sys.exit('gi.repository.Gtk' in sys.modules)"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).returncode == 0

    def test_09_vault(self):
        """The keyring when one answers, else a 0600 file; locked is said."""
        from haltia_kit.vault import FILE, KEYRING, LOCKED, Locked

        os.makedirs(vrun, 0o700)
        for ring in (None, testing.FakeKeyring(answers=False)):  # the tablet
            v = vault(ring)
            v.put("max", "secret")
            assert v.state == FILE and v.get("max") == "secret"
            assert stat.S_IMODE(os.stat(os.path.join(vdir, "max")).st_mode) == 0o600
            assert v.as_file("max") == os.path.join(vdir, "max")
            v.drop("max")
            assert v.get("max") is None and v.as_file("max") is None

        ring = testing.FakeKeyring()
        v = vault(ring)
        assert v.state is None  # not asked before the first use
        v.put("telegram", "T")
        assert v.state == KEYRING and ring.kept == {"telegram": "T"}
        assert not os.path.exists(os.path.join(vdir, "telegram"))
        v.drop("telegram")
        assert ring.kept == {} and v.get("telegram") is None

        ring = testing.FakeKeyring()  # a file of before the keyring moves in once
        with open(os.path.join(vdir, "max.json"), "w") as fh:
            fh.write("old")
        assert vault(ring, file="%s.json").get("max") == "old" and ring.kept == {"max": "old"}
        assert not os.path.exists(os.path.join(vdir, "max.json"))
        assert vault(ring, file="%s.json").get("max") == "old"

        ring = testing.FakeKeyring(locked=True)  # refused: not the file, LOCKED
        with open(os.path.join(vdir, "max"), "w") as fh:
            fh.write("old")
        v = vault(ring)
        for call in (lambda: v.get("max"), lambda: v.put("max", "new"), lambda: v.take("max"),
                     lambda: v.as_file("max")):
            try:
                call()
                raise AssertionError("a locked keyring gave way")
            except Locked:
                assert v.state == LOCKED
        assert os.path.exists(os.path.join(vdir, "max")), "the file waits for the keyring"
        ring.locked = False  # unlocked on the second asking
        assert v.get("max") == "old" and v.state == KEYRING and ring.kept == {"max": "old"}
        assert not os.path.exists(os.path.join(vdir, "max"))

        ring = testing.FakeKeyring()  # a copy for yt-dlp and mpv, written back at the end
        ring.kept["c.txt"] = "cookies"
        v = vault(ring)
        path = v.as_file("c.txt")
        assert path.startswith(vrun) and v.as_file("c.txt") == path
        assert stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        with open(path, "w") as fh:
            fh.write("cookies, renewed")
        v.close()
        assert ring.kept["c.txt"] == "cookies, renewed" and not os.path.exists(path)
        path = v.as_file("c.txt")
        v.drop("c.txt")
        assert not os.path.exists(path) and v.as_file("c.txt") is None
        with open(os.path.join(vdir, "c.txt"), "w") as fh:
            fh.write("new cookies")
        assert v.take("c.txt") and ring.kept["c.txt"] == "new cookies"
        assert not os.path.exists(os.path.join(vdir, "c.txt")) and not v.take("c.txt")

    def test_10_camera(self):
        """videotestsrc in place of a camera."""
        camera, Gst = gst_or_skip(self)

        def test_source():
            src = Gst.ElementFactory.make("videotestsrc", None)
            src.set_property("is-live", True)
            return src

        def gone_source():
            src = Gst.ElementFactory.make("v4l2src", None)
            src.set_property("device", os.path.join(top, "no-video0"))
            return src

        test_cam = camera.Camera("Тест", test_source)
        shots = os.path.join(top, "shots")
        got = camera.capture(test_cam, where=shots, orientation=6, settle=2)
        assert os.path.dirname(got) == shots
        assert os.path.basename(got).startswith("IMG_") and got.endswith(".jpg")
        with Image.open(got) as im:
            assert im.size == (320, 240) and im.getexif()[0x0112] == 6
        again = camera.capture(test_cam, where=shots, settle=0)
        assert again != got  # the same second: another name, the first one kept
        assert sorted(os.listdir(shots)) == sorted(os.path.basename(p) for p in (got, again))
        assert camera.folder().endswith(os.sep + "Камера")

        class Err:  # a GStreamer error as GLib.Error gives it
            domain, code, message = "gst-resource-error-quark", int(Gst.ResourceError.BUSY), "x"

        assert camera._state_of(Err) == camera.BUSY
        Err.code, Err.message = int(Gst.ResourceError.FAILED), "Camera 'x' is already in use."
        assert camera._state_of(Err) == camera.BUSY
        Err.message = "Cannot identify device"
        assert camera._state_of(Err) == camera.FAILED

        frames, states = [], []
        pv = camera.Preview(test_cam, lambda w, h, d: frames.append((w, h, len(d))),
                            states.append)
        pv.start()
        pump(lambda: frames)
        pv.stop()  # the camera off
        assert frames and frames[0] == (320, 240, 320 * 240 * 4), frames
        assert states == [camera.ON, camera.OFF] and pv.pipe is None, states

        if Gst.ElementFactory.find("v4l2src") is not None:
            gone = camera.Camera("Нет", gone_source)
            try:
                camera.capture(gone, where=shots, timeout=3)
                raise AssertionError("a camera that is not there gave a photo")
            except camera.CameraError as e:
                assert e.state == camera.FAILED
            states = []
            pv = camera.Preview(gone, lambda *_a: None, states.append)
            pv.start()  # said as a state, never raised
            pump(lambda: states)
            assert states == [camera.FAILED] and pv.pipe is None, states

    def test_11_camera_frames(self):
        """Rows of a buffer with a stride wider than the picture come packed;
        a webcam of MJPEG only is decoded."""
        camera, Gst = gst_or_skip(self)
        caps = Gst.Caps.from_string("video/x-raw,format=RGBA,width=3,height=2")
        packed = bytes(range(24))
        sample = Gst.Sample.new(Gst.Buffer.new_wrapped(packed), caps, None, None)
        assert camera._layout(sample, 3) == (0, 12)
        assert camera._frame(sample) == (3, 2, packed)
        padded = packed[:12] + b"\xee" * 4 + packed[12:]  # a stride of 16
        sample = Gst.Sample.new(Gst.Buffer.new_wrapped(padded), caps, None, None)
        real = camera._layout
        camera._layout = lambda _s, _w: (0, 16)
        try:
            assert camera._frame(sample) == (3, 2, packed)
        finally:
            camera._layout = real

        if not all(Gst.ElementFactory.find(n) for n in ("jpegenc", "jpegdec")):
            return
        src = Gst.parse_bin_from_description("videotestsrc is-live=true ! jpegenc", True)
        pipe, sink = camera._pipeline(src, camera.JPEG, camera.RAW + ",format=RGBx")
        pipe.set_state(Gst.State.PLAYING)
        try:
            got = sink.emit("try-pull-sample", 5 * Gst.SECOND)
        finally:
            pipe.set_state(Gst.State.NULL)
        assert got is not None and camera._frame(got)[:2] == (320, 240)

    def test_12_camera_app(self):
        """take_photo's talk with the Camera app: the absolute path on the
        last line and exit 0 is the photo kept; exit 0 without one is a
        cancel; an error is the dialog here."""
        from haltia_kit import camera

        kept = os.path.join(top, "IMG_kept.jpg")
        Image.new("RGB", (4, 4)).save(kept)

        def app(code):
            return [sys.executable, "-c", code]

        assert camera._ask(app("print('starting'); print(%r)" % kept)) == kept
        assert camera._ask(app("pass")) is None
        assert camera._ask(app("print('IMG_kept.jpg')")) is None  # not an absolute path
        assert camera._ask(app("print(%r)" % os.path.join(top, "none.jpg"))) is None
        assert camera._ask(app("import sys; print(%r); sys.exit(3)" % kept)) is False
        assert camera._ask([os.path.join(top, "no-camera-app")]) is False

        calls = []
        real = camera.cameras
        camera.cameras = lambda: calls.append(1) or []
        camera._seen[:] = []
        try:
            assert camera.available() is False and camera.available() is False
            assert calls == [1]  # asked once, the answer kept
        finally:
            camera.cameras = real
            camera._seen[:] = []

    def test_12b_camera_front(self):
        """Which way a camera faces, in both spellings of libcamera's
        property: a front or external one mirrors, a back one not."""
        from haltia_kit import camera

        class Device:
            def __init__(self, props):
                self.props = props

            def get_properties(self):
                return self

            def get_string(self, key):
                return self.props.get(key)

        def faces(props):
            return camera._front(Device(props))

        key = "api.libcamera.Location"
        assert faces({key: "CameraLocationFront"}) is True
        assert faces({key: "CameraLocationExternal"}) is True
        assert faces({key: "CameraLocationBack"}) is False
        assert faces({"api.libcamera.location": "front"}) is True
        assert faces({"api.libcamera.location": "back"}) is False
        assert faces({"device.bus": "usb"}) is True
        assert faces({}) is False

    def test_12c_camera_contrast(self):
        """libcamera's Contrast is set on a libcamerasrc that has it as a
        property, never on one without it or on another source."""
        from haltia_kit import camera

        class Source:
            def __init__(self, kind, has):
                self.kind, self.has, self.props = kind, has, {}

            def get_factory(self):
                return self

            def get_name(self):
                return self.kind

            def find_property(self, name):
                return object() if self.has and name == "contrast" else None

            def set_property(self, name, value):
                self.props[name] = value

        def made(kind, has, **kw):
            return camera.Camera("Тест", lambda: Source(kind, has), **kw).make().props

        assert made("libcamerasrc", True) == {"contrast": camera.CONTRAST}
        assert made("libcamerasrc", True, contrast=1.5) == {"contrast": 1.5}
        assert made("libcamerasrc", True, contrast=None) == {}
        assert made("libcamerasrc", False) == {}  # an older libcamera: left as it is
        assert made("v4l2src", True) == {}
        assert made("videotestsrc", True) == {}
        assert camera.Camera("Нет", lambda: None).make() is None

    def test_12d_camera_turn(self):
        """The picture turns with the screen as the shell writes it, and
        with the sensor's mounting; it follows the file while it plays;
        without the file there is no turn."""
        camera, Gst = gst_or_skip(self)
        from gi.repository import Gio
        rotation = camera.ROTATION_FILE
        assert rotation == os.path.join(top, "xdg-run", "haltia", "rotation")

        def write(text):  # as the shell would: whole, by a rename
            with open(rotation + ".part", "w") as fh:
                fh.write(text + "\n")
            os.replace(rotation + ".part", rotation)

        class Device:
            def __init__(self, props):
                self.props = props

            def get_properties(self):
                return self

            def get_value(self, key):
                return self.props.get(key)

        assert camera._mount(Device({"api.libcamera.rotation": "270"})) == 270
        assert camera._mount(Device({"api.libcamera.Rotation": 90})) == 90
        assert camera._mount(Device({})) == 0

        back = camera.Camera("Задняя", live_source(Gst))
        front = camera.Camera("Передняя", live_source(Gst), front=True, mount=270)
        assert camera.screen() == 0  # no file: a PC
        assert camera._turn(back, 0) == 0 and camera._turn(front, 0) == 270
        assert camera._turn(back, 90) == 90 and camera._turn(front, 90) == 0
        assert camera._turn(front, 270) == 180  # the same sign as the back camera
        assert camera._turn(camera.Camera("x", None, mount=90), 270) == 0
        plain = back.make()
        assert plain.get_factory().get_name() == "videotestsrc"  # no file, not turned
        sized = back.make(caps="video/x-raw,width=160,height=120")  # a size, no turn
        assert sized.get_by_name("turn") is None
        assert sized.get_by_name("size").get_property("caps").get_structure(0) \
            .get_int("width") == (True, 160)
        swapped = camera._swap(Gst.Caps.from_string("video/x-raw,width=480,height=[1,360]"))
        s = swapped.get_structure(0)
        assert s.get_int("height") == (True, 480) and s.has_field("width"), swapped.to_string()
        if Gst.ElementFactory.find("videoflip") is None:
            return

        def direction(flip):
            return int(flip.get_property("video-direction"))

        try:
            write("garbage")
            assert camera.screen() == 0
            write("90")
            assert camera.screen() == 90
            got = camera.capture(back, where=os.path.join(top, "turned"), settle=1)
            with Image.open(got) as im:
                assert im.size == (240, 320), im.size  # videotestsrc's 320 x 240, turned

            frames, states = [], []
            pv = camera.Preview(back, lambda w, h, d: frames.append((w, h)), states.append)
            pv.start()
            pump(lambda: frames)
            try:
                assert frames and frames[0] == (240, 320), frames
                flip = pv.pipe.get_by_name("turn")
                assert direction(flip) == 1  # 90r
                write("normal")  # turned back while it plays
                pump(lambda: direction(flip) == 0)
                assert direction(flip) == 0
            finally:
                pv.stop()

            write("180")
            made = back.make()  # a program's own pipeline: a bin, turned too
            flip = made.get_by_name("turn")
            assert made.get_static_pad("src") is not None and direction(flip) == 2
            write("270")
            pump(lambda: direction(flip) == 3)
            assert direction(flip) == 3
            # The folder is watched: only the rotation file turns the picture.
            camera._direct(flip, 0)
            other = Gio.File.new_for_path(rotation + ".part")
            camera._turn_all(None, other, None, Gio.FileMonitorEvent.CHANGED)
            assert direction(flip) == 0
            camera._turn_all(None, other, Gio.File.new_for_path(rotation),
                             Gio.FileMonitorEvent.RENAMED)
            assert direction(flip) == 3
            for text, want in (("90", 1), ("normal", 0), ("180", 2)):  # replaced quickly
                write(text)
                pump(lambda: direction(flip) == want)
                assert direction(flip) == want, text

            # A size asked for comes right after the source, before the flip,
            # its width and height swapped for the source turned by 90: the
            # camera itself gives it, and the picture comes out in it.
            write("90")
            made = back.make(caps="video/x-raw,width=160,height=120")
            size = made.get_by_name("size")
            before = size.get_static_pad("sink").get_peer().get_parent_element()
            after = size.get_static_pad("src").get_peer().get_parent_element()
            assert before.get_factory().get_name() == "videotestsrc"
            assert after.get_name() == "turn"
            assert size.get_property("caps").get_structure(0).get_int("width") == (True, 120)
            pipe = Gst.Pipeline.new(None)
            sink = Gst.ElementFactory.make("appsink", None)
            pipe.add(made)
            pipe.add(sink)
            assert made.link(sink)
            pipe.set_state(Gst.State.PLAYING)
            try:
                got = sink.emit("try-pull-sample", 5 * Gst.SECOND)
            finally:
                pipe.set_state(Gst.State.NULL)
            s = got.get_caps().get_structure(0)
            assert (s.get_int("width"), s.get_int("height")) == ((True, 160), (True, 120))
        finally:
            for path in (rotation, rotation + ".part"):
                if os.path.exists(path):
                    os.remove(path)

    def test_13_formats(self):
        assert formats.plural(1, "файл", "файла", "файлов") == "1 файл"
        assert formats.plural(3, "файл", "файла", "файлов") == "3 файла"
        assert formats.plural(11, "файл", "файла", "файлов") == "11 файлов"
        assert formats.nice_size(2048) == "2.0 КБ" and formats.nice_duration(65) == "1:05"
        oct17 = time.mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))
        assert formats.nice_date(oct17, now=oct17) == "17 окт"
        assert formats.month(oct17) == "Октябрь 2026"

    def test_14_reader_group(self):
        """A reader killed at its timeout takes its ffprobe with it; a video
        thumbnail fits in the reader's time."""
        from haltia_kit import worker

        assert photos.PROBE_S + 2 * photos.FRAME_S < worker.TIMEOUT_S
        fake = os.path.join(top, "fakebin")
        os.makedirs(fake)
        pidfile = os.path.join(top, "ffprobe.pid")
        probe = os.path.join(fake, "ffprobe")
        with open(probe, "w") as fh:  # an ffprobe that hangs
            fh.write("#!/bin/sh\necho $$ > '%s'\nexec sleep 60\n" % pidfile)
        os.chmod(probe, 0o755)
        path = os.environ["PATH"]
        os.environ["PATH"] = fake + os.pathsep + path
        w = worker.Worker(timeout=4)
        try:
            assert w.call({"op": "info", "path": os.path.join(top, "slow.mp4"),
                           "kind": "video"}) is None
        finally:
            os.environ["PATH"] = path
            w.close()
        with open(pidfile) as fh:
            pid = int(fh.read())
        end = time.time() + 5
        while alive(pid) and time.time() < end:
            time.sleep(0.05)
        assert not alive(pid), "ffprobe outlived its reader"

    def test_15_thumbs(self):
        """One thumbnail thread for all the Thumbs, ended when idle; the
        pictures kept are bounded by their bytes."""
        from haltia_kit import gallery

        def pixbuf(side):
            return GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, side, side)

        memo = gallery.Memo(limit=3 * 30000)  # three of 100 x 100 RGB
        for key in "abc":
            memo.put(key, pixbuf(100))
        assert memo.get("a") is not None  # now the latest
        memo.put("d", pixbuf(100))
        assert "b" not in memo and all(k in memo for k in "acd") and memo.used == 90000
        memo.put("e", pixbuf(400))  # larger than the limit: only it is kept
        assert list(memo.kept) == ["e"] and memo.used == 400 * 1200

        class Img:
            pix = None

            def set_from_pixbuf(self, pix):
                self.pix = pix

        def threads():
            return sum(t.name == "haltia-thumbs" for t in threading.enumerate())

        real = gallery.IDLE_S
        gallery.IDLE_S = 0.3
        try:
            images = [Img(), Img()]
            for image in images:  # a chooser window each
                gallery.Thumbs(post=lambda fn: fn()).want(pic, image, 64)
            assert threads() <= 1
            end = time.time() + 30
            while not all(i.pix for i in images) and time.time() < end:
                time.sleep(0.05)
            assert all(i.pix and i.pix.get_width() == 64 for i in images)
            end = time.time() + 5
            while threads() and time.time() < end:
                time.sleep(0.05)
            assert threads() == 0 and not gallery._runs  # idle: ended
            again = Img()
            gallery.Thumbs(post=lambda fn: fn()).want(pic, again, 32)  # started again
            end = time.time() + 30
            while again.pix is None and time.time() < end:
                time.sleep(0.05)
            assert again.pix is not None and again.pix.get_width() == 32
        finally:
            gallery.IDLE_S = real

    def test_16_camera_never_waits(self):
        """The viewfinder finds its camera off the main thread; pause()
        lets the camera go; a still always ends, said on the main thread,
        and the picture comes back after it."""
        camera, Gst = gst_or_skip(self)
        real = camera.cameras
        camera.cameras = lambda: time.sleep(1) or []
        camera._seen[:] = []
        try:
            states = []
            pv = camera.Preview(None, lambda *_a: None, states.append)
            t = time.monotonic()
            pv.start()
            assert time.monotonic() - t < 0.5  # the devices are asked on a thread
            pump(lambda: states)
            assert states == [camera.NONE], states
        finally:
            camera.cameras = real
            camera._seen[:] = []

        test_cam = camera.Camera("Тест", live_source(Gst))
        frames, states = [], []
        pv = camera.Preview(test_cam, lambda *_a: frames.append(1), states.append)
        pv.start()
        pv.stop()  # before it opened: it never does
        pump(lambda: frames, wait=1)
        assert not frames and not states and pv.pipe is None, states

        def boom(*_a, **_k):
            raise RuntimeError("not a CameraError")

        def busy(*_a, **_k):
            raise camera.CameraError(camera.BUSY)

        real = camera.capture
        try:
            for fake, want in ((boom, (None, camera.FAILED)), (busy, (None, camera.BUSY)),
                               (lambda *_a, **_k: "/p/IMG.jpg", ("/p/IMG.jpg", None))):
                camera.capture = fake
                got = []
                camera._shoot(test_cam, 1, lambda path, state: got.append((path, state)))
                pump(lambda: got)
                assert got == [want], got
        finally:
            camera.capture = real

        from gi.repository import Gdk, Gtk
        if Gdk.Display.get_default() is None:
            return  # the widget itself needs a display
        vf = camera.Viewfinder(test_cam)
        win = Gtk.OffscreenWindow()
        win.add(vf)
        win.show_all()
        try:
            pump(lambda: vf.state == camera.ON)
            assert vf.state == camera.ON
            vf.pause()  # the window in the background, still mapped
            assert vf.state == camera.OFF and vf.preview.pipe is None
            vf.resume()
            pump(lambda: vf.state == camera.ON)
            assert vf.state == camera.ON
            taken = []
            real_still = camera._still
            camera.capture = camera._still = boom  # from the picture or its own: failed
            try:
                vf.capture(taken.append)
                pump(lambda: taken)
            finally:
                camera.capture, camera._still = real, real_still
            assert taken == [None] and vf.failure.state == camera.FAILED
            pump(lambda: vf.state == camera.ON)
            assert vf.state == camera.ON and not vf.taking  # the picture back
        finally:
            win.destroy()

    def test_17_share(self):
        """The programs a file can be sent to: X-Haltia-Share and a MimeType
        that matches, wildcards too, never the program calling; the one
        chosen gets the file as its argument."""
        from gi.repository import Gio
        from haltia_kit import share

        def ids(found):
            return [info.get_id()[len("test.share."):-len(".desktop")]
                    for info in found if info.get_id().startswith("test.share.")]

        real = share._own
        share._own = lambda: None
        try:
            assert ids(share.targets("image/png")) == ["Notes", "Self", "Photos"]
        finally:
            share._own = real
        share._own = lambda: "test.share.Self.desktop"
        try:
            assert ids(share.targets("image/png")) == ["Notes", "Photos"]
            assert ids(share.targets("image/jpeg")) == ["Photos"]
            assert ids(share.targets("text/plain")) == ["Notes"]
            assert ids(share.targets("audio/mpeg")) == []
            if Gio.content_type_guess("x.png", None)[0] == "image/png":  # a MIME database
                assert ids(share.targets(pic)) == ["Notes", "Photos"]
                nameless = os.path.join(top, "nameless")
                Image.new("RGB", (4, 4)).save(nameless, "PNG")
                assert ids(share.targets(nameless)) == ["Notes", "Photos"]  # sniffed
        finally:
            share._own = real

        info = Gio.DesktopAppInfo.new("test.share.Photos.desktop")
        assert share.launch(info, pic)
        pump(lambda: os.path.exists(received))
        with open(received) as fh:
            assert fh.read() == pic + "\n"

    def test_18_crash(self):
        """An uncaught exception, in the main thread or another, leaves a
        record with the whole traceback and the log's tail; a segfault, the
        faulthandler file; a clean run, nothing; only the newest stay."""
        from haltia_kit import crash
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        state = os.path.join(top, "crash-state")
        crashes = os.path.join(state, "haltia", "crashes")
        log = os.path.join(top, "crash.log")
        with open(log, "w") as fh:
            fh.writelines("line %d\n" % i for i in range(500))
        env = dict(os.environ, XDG_STATE_HOME=state, PYTHONPATH=here)
        script = os.path.join(top, "crash_app.py")  # a file: its lines are in the traceback

        def run(code):
            with open(script, "w") as fh:
                fh.write("import haltia_kit\nfrom haltia_kit import crash\n"
                         "crash.install('test.App', version='1.2.3', log=%r)\n%s" % (log, code))
            return subprocess.run([sys.executable, script], cwd=here, env=env,
                                  capture_output=True, text=True, timeout=60)

        def records():
            out = {}
            for name in sorted(os.listdir(crashes)):
                with open(os.path.join(crashes, name), encoding="utf-8") as fh:
                    out[name] = fh.read()
            return out

        done = run("print('fine')")
        assert done.returncode == 0 and done.stdout == "fine\n", done.stderr
        assert records() == {}  # a clean run leaves nothing

        failed = run("def inner():\n    raise ValueError('broken')\n"
                     "try:\n    {}['key']\nexcept KeyError:\n    inner()\n")
        assert failed.returncode == 1 and "ValueError: broken" in failed.stderr  # still said
        [(name, text)] = records().items()
        assert name.startswith("test.App-") and name.endswith(".json"), name
        rec = json.loads(text)
        assert rec["app"] == "test.App" and rec["version"] == "1.2.3", rec
        assert rec["exception"] == "ValueError: broken", rec["exception"]
        assert "raise ValueError" in rec["traceback"] and "KeyError" in rec["traceback"]
        assert "in inner" in rec["traceback"] and rec["thread"] == "MainThread"
        assert rec["log_tail"] == "".join("line %d\n" % i for i in range(300, 500))
        assert rec["time"] and rec["pid"] and rec["kit"]
        os.unlink(os.path.join(crashes, name))

        threaded = run("import threading\ndef work():\n    raise RuntimeError('in a thread')\n"
                       "t = threading.Thread(target=work, name='worker')\nt.start()\nt.join()\n")
        assert threaded.returncode == 0, threaded.stderr
        [(name, text)] = records().items()
        rec = json.loads(text)
        assert rec["exception"] == "RuntimeError: in a thread" and rec["thread"] == "worker"
        assert "in work" in rec["traceback"]
        os.unlink(os.path.join(crashes, name))

        interrupted = run("raise KeyboardInterrupt")
        assert interrupted.returncode != 0 and records() == {}  # Ctrl+C is no crash

        segfault = run("import ctypes\nctypes.string_at(0)\n")
        assert segfault.returncode < 0, segfault.returncode
        [(name, text)] = records().items()
        assert name.startswith("test.App-") and name.endswith(".fault"), name
        assert text.startswith("# haltia-crash app=test.App version=1.2.3 pid=")
        assert "Segmentation fault" in text and "crash_app.py" in text, text
        segfault_name = name

        killed = subprocess.Popen(  # killed: just the header, removed at the next start
            [sys.executable, "-c", "import haltia_kit, time\nprint(1, flush=True)\n"
             "time.sleep(60)"], cwd=here, env=env, stdout=subprocess.PIPE, text=True)
        try:
            assert killed.stdout.readline() == "1\n"
            assert len(records()) == 2
        finally:
            killed.kill()
            killed.wait()
            killed.stdout.close()
        for i in range(25):  # older crashes
            path = os.path.join(crashes, "old.App-%02d.json" % i)
            with open(path, "w") as fh:
                fh.write("{}\n")
            os.utime(path, (1000 + i, 1000 + i))
        run("raise OSError('again')")
        left = records()
        assert len(left) == crash.KEEP and segfault_name in left, sorted(left)
        assert not [n for n in left if n.startswith("python-")]  # the killed run's
        assert "old.App-05.json" not in left and "old.App-24.json" in left
        assert len([n for n in left if n.startswith("test.App-")]) == 2
    def test_18_camera_colour(self):
        """A still of its own waits until the colour stops changing (a
        camera's white balance settling); the viewfinder's photo is the
        next frame of its picture, not a pipeline started anew."""
        camera, Gst = gst_or_skip(self)
        red, steps = (200, 40, 30), 40

        def plain():
            src = Gst.ElementFactory.make("videotestsrc", None)
            src.set_property("is-live", True)
            Gst.util_set_object_arg(src, "pattern", "solid-color")
            src.set_property("foreground-color",
                             0xff000000 | red[0] << 16 | red[1] << 8 | red[2])
            return src

        def settling():
            """Red, grey at first and coloured over the first frames."""
            src = plain()
            balance = Gst.ElementFactory.make("videobalance", None)
            balance.set_property("saturation", 0.0)
            n = [0]

            def probe(_pad, _info):
                n[0] += 1  # for the next frame
                balance.set_property("saturation", min(n[0], steps) / steps)
                return Gst.PadProbeReturn.OK

            src.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, probe)
            box = Gst.Bin.new(None)
            box.add(src)
            box.add(balance)
            src.link(balance)
            box.add_pad(Gst.GhostPad.new("src", balance.get_static_pad("src")))
            return box

        def near(rgb, want, by=10):
            return all(abs(a - b) <= by for a, b in zip(rgb, want))

        def mean_of(path):
            with Image.open(path) as im:
                small = im.convert("RGB").resize((8, 8))
                px = list(small.getdata())
            return tuple(sum(p[c] for p in px) / len(px) for c in range(3))

        if Gst.ElementFactory.find("videobalance") is not None:
            cam = camera.Camera("Тест", settling)
            got = camera.capture(cam, where=os.path.join(top, "settled"), settle=5)
            assert near(mean_of(got), red), mean_of(got)  # not a frame still settling

        assert camera._steady([(1.0, 2.0, 3.0)] * (camera.STEADY_OVER + 1))
        assert not camera._steady([(1.0, 2.0, 3.0)] * camera.STEADY_OVER)
        assert not camera._steady([(9.0, 2.0, 3.0)] + [(1.0, 2.0, 3.0)] * camera.STEADY_OVER)
        caps = Gst.Caps.from_string("video/x-raw,format=RGBA,width=2,height=1")
        pixels = bytes([10, 20, 30, 255, 30, 40, 50, 255])
        sample = Gst.Sample.new(Gst.Buffer.new_wrapped(pixels), caps, None, None)
        assert camera._mean(sample) == (20.0, 30.0, 40.0)

        flat = camera.Camera("Тест", plain)
        states = []
        pv = camera.Preview(flat, lambda *_a: None, states.append)
        snapped = []
        assert pv.snap(snapped.append) is False and not snapped  # no picture yet
        pv.start()
        pump(lambda: pv.state == camera.ON)
        try:
            assert pv.snap(snapped.append)
            pump(lambda: snapped)
            assert len(snapped) == 1 and snapped[0][:2] == (320, 240), snapped[:1]
            pv.snap(snapped.append)
        finally:
            pv.stop()  # a snap still waiting is told None
        pump(lambda: len(snapped) == 2, wait=1)
        assert len(snapped) == 2

        real_folder, real_capture = camera.folder, camera.capture
        camera.folder = lambda: os.path.join(top, "snapped")

        def boom(*_a, **_k):
            raise AssertionError("a pipeline of its own for a still")

        camera.capture = boom
        try:
            done = []
            camera._shoot(flat, 6, lambda path, state: done.append((path, state)),
                          frame=snapped[0])
            pump(lambda: done)
            path, state = done[0]
            assert state is None and os.path.dirname(path) == os.path.join(top, "snapped")
            with Image.open(path) as im:
                assert im.size == (320, 240) and im.getexif()[0x0112] == 6
            assert near(mean_of(path), red), mean_of(path)

            from gi.repository import Gdk, Gtk
            if Gdk.Display.get_default() is None:
                return  # the widget itself needs a display
            vf = camera.Viewfinder(flat)
            win = Gtk.OffscreenWindow()
            win.add(vf)
            win.show_all()
            try:
                pump(lambda: vf.state == camera.ON)
                taken = []
                vf.capture(taken.append)
                pump(lambda: taken)
                assert taken and taken[0] and vf.failure is None, taken
                assert near(mean_of(taken[0]), red), mean_of(taken[0])
                assert vf.state == camera.ON and not vf.taking  # the picture went on
            finally:
                win.destroy()
        finally:
            camera.folder, camera.capture = real_folder, real_capture


if __name__ == "__main__":
    unittest.main()
