#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""haltia-kit through its interface: settings, awake, the fake player,
the player's awake rule, kinds, thumbnails, formats.
Run: python3 tests/test_compat.py (PyGObject with GdkPixbuf; no display,
no libmpv, no sound card needed)."""
import os
import sys
import tempfile
import time

top = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = os.path.join(top, "cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(top, "config")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from haltia_kit import formats, photos, settings, sound, testing  # noqa: E402
from haltia_kit.awake import Awake  # noqa: E402

# --- settings: a file from before the rename moves to ~/.config/haltia ----------------
os.makedirs(os.path.join(top, "config", "b8k"))
with open(os.path.join(top, "config", "b8k", "news.conf"), "w") as fh:
    fh.write("[news]\nbuzz = no\n")
n = settings.Settings("news")
assert n.path == os.path.join(top, "config", "haltia", "news.conf")
assert n.get_bool("buzz", True) is False
assert not os.path.exists(os.path.join(top, "config", "b8k", "news.conf"))
print("settings moved ok")

# --- the old module name and the folder migration --------------------------------------
import b8k_compat.settings as old_settings  # noqa: E402
from b8k_compat import formats as old_formats  # noqa: E402
from haltia_kit import paths  # noqa: E402
assert old_settings is settings and old_formats is formats
os.makedirs(os.path.join(top, "data", "b8k-max", "photos"))
moved = paths.migrate_dirs("b8k-max", "haltia-max", bases=[os.path.join(top, "data")])
assert moved == [os.path.join(top, "data", "haltia-max")]
assert os.path.isdir(os.path.join(top, "data", "haltia-max", "photos"))
assert os.path.islink(os.path.join(top, "data", "b8k-max"))
assert os.path.isdir(os.path.join(top, "data", "b8k-max", "photos"))
assert paths.migrate_dirs("b8k-max", "haltia-max", bases=[os.path.join(top, "data")]) == []
print("old name and folders ok")

# --- settings: the old yes/no files read as they are ----------------------------------
s = settings.Settings("max")
assert s.path == os.path.join(top, "config", "haltia", "max.conf")
assert s.get_bool("telemetry", True) is True and s.get("folder", "x") == "x"
os.makedirs(os.path.dirname(s.path), exist_ok=True)
with open(s.path, "w") as fh:
    fh.write("[max]\ntelemetry = no\nbuzz = yes\nfolder = /home/user/Загрузки/MAX\nodd = maybe\n")
assert s.get_bool("telemetry", True) is False and s.get_bool("buzz", False) is True
assert s.get_bool("odd", True) is True  # unreadable: the default
assert s.get("folder") == "/home/user/Загрузки/MAX"
assert s.set("buzz", False) and s.set("quality", "high")
assert "buzz = no" in open(s.path).read()
assert settings.Settings("max").get("quality") == "high"
with open(s.path, "w") as fh:
    fh.write("not an ini file at all")
assert s.get_bool("buzz", True) is True  # a broken file gives defaults
print("settings ok")

# --- awake: an inhibitor fd held once, let go -------------------------------------------
inh = testing.FakeInhibit()
a = Awake("Музыка", "plays", inhibit=inh)
assert a.hold() and a.hold() and inh.held == 1 and a.held
a.release()
assert inh.held == 0 and not a.held


def broken(*_a):
    raise OSError("no logind")


assert Awake("x", "y", inhibit=broken).hold() is False  # without logind: nothing


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
print("awake ok")

# --- the player: awake only while playing and not paused (no libmpv: fake mpv) ---------


class FakeMpv:
    def __init__(self):
        self.pause = False
        self.played = []

    def play(self, what):
        self.played.append(what)

    def stop(self):
        pass


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


class End:
    class data:
        reason = "eof"


p._end(End())
assert ended == [1] and not p.playing and inh.held == 0
p.play("/m/b.mp3")
p.stop()
assert inh.held == 0
opt = sound.Player("x", options={"user_agent": "UA/1", "load_unsafe_playlists": True, "video": True})
assert opt.options == {"user_agent": "UA/1"}  # the safe settings cannot be undone
sys.modules["mpv"] = None  # libmpv missing: play() says so
try:
    sound.Player("x", post=lambda fn: fn(), awake=Awake("x", "y", inhibit=inh)).play("/m/c")
    raise AssertionError("play without libmpv must say so")
except RuntimeError:
    pass
finally:
    del sys.modules["mpv"]
print("player ok")

# --- the fake player programs test with ---------------------------------------------
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
print("fake player ok")

# --- kinds and thumbnails (freedesktop, shared with file managers) ---------------------
assert photos.kind_of("a.JPG") == "photo" and photos.kind_of("b.mkv") == "video"
assert photos.kind_of("c.opus") == "audio" and photos.kind_of("d.txt") is None
pic = os.path.join(top, "Кот 1.png")
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
song = os.path.join(top, "song.mp3")
with open(song, "wb") as fh:
    fh.write(b"not a real song")
assert photos.thumbnail(song) is None
assert os.listdir(os.path.join(top, "cache", "thumbnails", "fail", photos.FAIL))
assert photos.thumbnail(song) is None  # the failure is remembered
huge = os.path.join(top, "huge.png")  # a PNG claiming 20000 x 20000 pixels
with open(huge, "wb") as fh:
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

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


class DeadReader:
    calls = 0

    def call(self, _req):
        DeadReader.calls += 1
        return None


photos._reader[:] = [DeadReader()]
bomb = os.path.join(top, "bomb.jpg")
with open(bomb, "wb") as fh:
    fh.write(b"\xff\xd8 pretend this kills the parser")
assert photos.thumbnail(bomb) is None and DeadReader.calls == 1
assert photos.thumbnail(bomb) is None and DeadReader.calls == 1  # not tried again
assert photos.media_info(bomb.replace(".jpg", ".mp4")) == {"duration": None}
photos._reader[:] = real
from haltia_kit.worker import Worker  # noqa: E402

w = Worker(timeout=5)
assert w.call({"op": "nonsense"}) == {"error": "unknown op"}
w.proc.kill()
w.proc.wait()
assert w.call({"op": "info", "path": song, "kind": "audio"}) == {"info": {"duration": None}}
w.close()
sq = photos.square(tp, 64)
assert (sq.get_width(), sq.get_height()) == (64, 64)
print("thumbnails ok")

# --- formats -------------------------------------------------------------------------
assert formats.plural(1, "файл", "файла", "файлов") == "1 файл"
assert formats.plural(3, "файл", "файла", "файлов") == "3 файла"
assert formats.plural(11, "файл", "файла", "файлов") == "11 файлов"
assert formats.nice_size(2048) == "2.0 КБ" and formats.nice_duration(65) == "1:05"
oct17 = time.mktime((2026, 10, 17, 12, 0, 0, 0, 0, -1))
assert formats.nice_date(oct17, now=oct17) == "17 окт"
assert formats.month(oct17) == "Октябрь 2026"
print("all passed")
