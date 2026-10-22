#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""haltia-kit through its interface: settings, awake, the fake player,
the player's awake rule, kinds, thumbnails, pictures, the vault, the camera,
formats, sharing. Run: python3 tests/test_compat.py, or python3 -m unittest from the
tests folder (PyGObject with GdkPixbuf, Pillow; no display, no libmpv, no
sound card, no keyring, no camera needed). The tests run in the order of
their numbers: later ones use the files of earlier ones."""
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
        assert "buzz = no" in open(s.path).read()
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
