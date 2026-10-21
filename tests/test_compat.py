#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""haltia-kit through its interface: settings, awake, the fake player,
the player's awake rule, kinds, thumbnails, pictures, the vault, the camera,
formats. Run: python3 tests/test_compat.py, or python3 -m unittest from the
tests folder (PyGObject with GdkPixbuf, Pillow; no display, no libmpv, no
sound card, no keyring, no camera needed). The tests run in the order of
their numbers: later ones use the files of earlier ones."""
import os
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

top = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = os.path.join(top, "cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(top, "config")
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


def pump(until):
    from gi.repository import GLib
    end = time.time() + 10
    while not until() and time.time() < end:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


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

    def test_13_formats(self):
        assert formats.plural(1, "файл", "файла", "файлов") == "1 файл"
        assert formats.plural(3, "файл", "файла", "файлов") == "3 файла"
        assert formats.plural(11, "файл", "файла", "файлов") == "11 файлов"
        assert formats.nice_size(2048) == "2.0 КБ" and formats.nice_duration(65) == "1:05"
        oct17 = time.mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))
        assert formats.nice_date(oct17, now=oct17) == "17 окт"
        assert formats.month(oct17) == "Октябрь 2026"


if __name__ == "__main__":
    unittest.main()
