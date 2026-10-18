# SPDX-License-Identifier: GPL-2.0
"""Photos, videos and sound files as pictures.

    kind_of(name)          "photo", "video", "audio" or None, by extension
    media_info(path, kind) {"duration", and for sound "title", "artist"}
    thumbnail(path)        its freedesktop thumbnail (~/.cache/thumbnails/
                           large, 256 px, shared with file managers), made
                           if missing or stale; None when it has none
    pixbuf_of(path, size)  a picture file at about size px on its short
                           side, turned upright
    square(pix, size)      the middle square of a pixbuf, scaled
    Thumbs()               small pictures made and read in a thread, set on
                           Gtk.Images on the GTK side
    Viewer(titled, held)   a widget: photos one at a time on black

Video frames and lengths come from ffmpeg/ffprobe, sound details and covers
from mutagen; without them those files simply have no picture. ffmpeg and
ffprobe read only the file itself (files come from chats and cards: no
network, no playlists reaching other files).

Taps only in the viewer: ‹ › and a swipe go through the photos, + − zoom,
a drag pans, ▶ shows them on by themselves; no pinch or double tap, which
some touch screens (the b8k tablet's filter) do not pass.
"""
import hashlib
import os
import queue
import shutil
import subprocess
import tempfile
import threading

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

PHOTO = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg"}
VIDEO = {"mp4", "m4v", "mkv", "webm", "avi", "mov", "3gp", "mpg", "mpeg", "ts", "wmv", "flv"}
AUDIO = {"mp3", "m4a", "aac", "ogg", "oga", "opus", "flac", "wav", "wma", "amr"}
SIZE = 256  # the freedesktop "large" thumbnail
FAIL = "b8k-compat-1"
SAFE = ["-protocol_whitelist", "file", "-format_whitelist",
        "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpeg,mpegts,asf,flv,mp3,ogg,flac,wav,aac,amr"]
SLIDE_S = 4
# Pictures come from anywhere (chats, cards): one too large to decode in a
# tablet's memory is left alone rather than decoded.
MAX_PIXELS = 60 * 1000 * 1000
MAX_COVER = 8 * 1024 * 1024  # bytes of an embedded cover


def kind_of(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in PHOTO:
        return "photo"
    if ext in VIDEO:
        return "video"
    if ext in AUDIO:
        return "audio"
    return None


def media_info(path, kind=None):
    kind = kind or kind_of(path)
    info = {"duration": None}
    if kind == "audio":
        try:
            import mutagen

            f = mutagen.File(path, easy=True)
            if f is not None:
                if getattr(f, "info", None) and getattr(f.info, "length", None):
                    info["duration"] = float(f.info.length)
                tags = f.tags or {}
                for key in ("title", "artist"):
                    v = tags.get(key) if hasattr(tags, "get") else None
                    if v:
                        info[key] = str(v[0] if isinstance(v, list) else v)
        except Exception:
            pass
    if kind in ("video", "audio") and info["duration"] is None:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error"] + SAFE +
                ["-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                 "file:" + path], capture_output=True, text=True, timeout=20)
            info["duration"] = float(out.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return info


def cover_bytes(path):
    """The embedded cover of a sound file, as image bytes, or None."""
    try:
        import mutagen

        f = mutagen.File(path)
    except Exception:
        return None
    if f is None:
        return None
    pictures = getattr(f, "pictures", None)
    if pictures:
        return pictures[0].data
    tags = getattr(f, "tags", None) or {}
    try:
        for key in list(tags.keys()):
            if key.startswith("APIC"):
                return tags[key].data
            if key == "covr" and tags[key]:
                return bytes(tags[key][0])
            if key == "metadata_block_picture":
                import base64

                from mutagen.flac import Picture

                return Picture(base64.b64decode(tags[key][0])).data
    except Exception:
        return None
    return None


def pixbuf_of(path, size):
    """Raises GLib.Error when the file cannot be read, ValueError when it is
    larger than MAX_PIXELS or its size cannot be told."""
    _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(path)  # -1 when it cannot tell
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        raise ValueError("picture too large or of unknown size: %s" % path)
    k = size / float(min(w, h))
    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max(1, int(w * k + 0.5)),
                                                  max(1, int(h * k + 0.5)), True)
    return pix.apply_embedded_orientation() or pix


def square(pix, size):
    w, h = pix.get_width(), pix.get_height()
    side = min(w, h)
    part = pix.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)
    return part.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)


def _fit(pix, size):
    w, h = pix.get_width(), pix.get_height()
    k = min(1.0, size / float(max(w, h)))
    if k >= 1.0:
        return pix
    return pix.scale_simple(max(1, int(w * k + 0.5)), max(1, int(h * k + 0.5)),
                            GdkPixbuf.InterpType.BILINEAR)


def _cache():
    return os.path.join(GLib.get_user_cache_dir(), "thumbnails")


def thumbnail(path):
    """The freedesktop thumbnail of a file, made if missing or stale."""
    try:
        mtime = int(os.stat(path).st_mtime)
    except OSError:
        return None
    uri = Gio.File.new_for_path(os.path.abspath(path)).get_uri()
    name = hashlib.md5(uri.encode()).hexdigest() + ".png"
    out = os.path.join(_cache(), "large", name)
    fail = os.path.join(_cache(), "fail", FAIL, name)
    for done, good in ((out, True), (fail, False)):
        try:
            _f, w, _h = GdkPixbuf.Pixbuf.get_file_info(done)
            if w and str(mtime) == _option(done, "tEXt::Thumb::MTime"):
                return done if good else None
        except (GLib.Error, TypeError):
            pass
    try:
        pix = _make(path)
    except Exception:
        pix = None
    target = out if pix is not None else fail
    if pix is None:
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 1, 1)
    try:
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        tmp = target + ".%d.tmp" % os.getpid()
        pix.savev(tmp, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime"], [uri, str(mtime)])
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except (GLib.Error, OSError):
        return None
    return out if target == out else None


def _option(png, key):
    pix = GdkPixbuf.Pixbuf.new_from_file_at_size(png, 1, 1)
    return pix.get_option(key)


def _make(path):
    kind = kind_of(path)
    if kind == "photo":
        return _fit(pixbuf_of(path, SIZE), SIZE)
    if kind == "video":
        dur = media_info(path, "video").get("duration")
        work = tempfile.mkdtemp(prefix="b8k-compat-")  # 0700, a name no one guesses
        frame = os.path.join(work, "frame.png")
        try:
            for at in (min(3.0, dur / 3.0) if dur else 1.0, 0.0):
                subprocess.run(
                    ["ffmpeg", "-v", "error", "-y"] + SAFE +
                    ["-ss", "%.2f" % at, "-i", "file:" + path, "-frames:v", "1", "-vf",
                     "scale=%d:%d:force_original_aspect_ratio=decrease" % (SIZE, SIZE), frame],
                    capture_output=True, timeout=60)
                if os.path.exists(frame) and os.path.getsize(frame) > 0:
                    return GdkPixbuf.Pixbuf.new_from_file(frame)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return None
    if kind == "audio":
        data = cover_bytes(path)
        if not data or len(data) > MAX_COVER:
            return None
        loader = GdkPixbuf.PixbufLoader()

        too_big = []

        def prepared(ld, w, h):
            # An exception here would only be printed, the decoding going on:
            # a refusal is kept and the picture shrunk to a pixel instead.
            if w * h > MAX_PIXELS:
                too_big.append(True)
                ld.set_size(1, 1)
                return
            k = min(1.0, SIZE / float(max(w, h, 1)))
            ld.set_size(max(1, int(w * k)), max(1, int(h * k)))  # scaled as it decodes

        loader.connect("size-prepared", prepared)
        loader.write(data)
        loader.close()
        if too_big:
            return None
        pix = loader.get_pixbuf()
        return _fit(pix, SIZE) if pix is not None else None
    return None


class Thumbs:
    """Small pictures from thumbnail(), made and read in one thread and set
    on the GTK side. want(path, image, size, crop=True) sets image when its
    picture is ready (cropped to a square, or fitted); reset() forgets the
    requests of a page gone. Loaded pictures are kept for the latest few
    hundred files."""

    def __init__(self, post=None):
        self.post = post or GLib.idle_add
        self.jobs = queue.Queue()
        self.gen = 0
        self.memo = {}
        threading.Thread(target=self._work, daemon=True).start()

    def reset(self):
        self.gen += 1

    def want(self, path, image, size, crop=True):
        key = (path, size, crop)
        if key in self.memo:
            image.set_from_pixbuf(self.memo[key])
            return
        self.jobs.put((self.gen, path, image, size, crop))

    def _work(self):
        while True:
            gen, path, image, size, crop = self.jobs.get()
            if gen != self.gen:
                continue
            thumb = thumbnail(path)
            if not thumb:
                continue
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file(thumb)
                pix = square(pix, size) if crop else _fit(pix, size)
            except GLib.Error:
                continue
            self.post(lambda g=gen, k=(path, size, crop), i=image, p=pix:
                      (self._set(g, k, i, p), False)[1])

    def _set(self, gen, key, image, pix):
        if len(self.memo) > 600:
            self.memo.clear()
        self.memo[key] = pix
        if gen == self.gen:
            image.set_from_pixbuf(pix)


VIEWER_CSS = b"""
.b8k-viewer { background-color: #000000; }
.b8k-viewer label.b8k-note { color: #ffffff; font-size: 24px; }
.b8k-vbar { background-color: rgba(0,0,0,0.55); border-radius: 18px; padding: 8px; }
.b8k-vbar button { background-image: none; background-color: rgba(255,255,255,0.18);
  color: #ffffff; border: none; box-shadow: none; border-radius: 14px;
  min-height: 64px; min-width: 76px; font-size: 26px; }
.b8k-vbar button.on { background-color: #3584e4; }
.b8k-vbar label { color: #ffffff; font-size: 20px; margin: 0 8px; }
"""
_css_done = []


def _viewer_css():
    if _css_done or Gdk.Screen.get_default() is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(VIEWER_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_done.append(provider)


class Viewer(Gtk.Overlay):
    """Photos one at a time on black. show(paths, i); close(); drop(path)
    after one was deleted (False when none is left). titled(name) is told
    the file shown, held(path) a long press."""

    def __init__(self, titled=None, held=None):
        super().__init__()
        _viewer_css()
        self.titled = titled or (lambda _n: None)
        self.held_cb = held or (lambda _p: None)
        self.get_style_context().add_class("b8k-viewer")
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        self.add(self.area)
        self.note = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.note.get_style_context().add_class("b8k-note")
        self.add_overlay(self.note)
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                      margin_bottom=16)
        bar.get_style_context().add_class("b8k-vbar")
        self.btn = {}
        for key, label, act in (("prev", "‹", lambda: self.step(-1)),
                                ("out", "−", lambda: self.zoom_by(1 / 1.5)),
                                ("in", "+", lambda: self.zoom_by(1.5)),
                                ("slide", "▶", self.toggle_slides),
                                ("next", "›", lambda: self.step(1))):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, a=act: a())
            bar.pack_start(b, False, False, 0)
            self.btn[key] = b
            if key == "in":
                self.count = Gtk.Label()
                bar.pack_start(self.count, False, False, 0)
        self.add_overlay(bar)
        self.paths, self.i = [], 0
        self.pix = None
        self.zoom, self.dx, self.dy = 1.0, 0.0, 0.0
        self.start = (0.0, 0.0)
        self.loading = 0
        self.slides = 0
        drag = Gtk.GestureDrag(widget=self.area)
        drag.connect("drag-begin", self._begin)
        drag.connect("drag-update", self._update)
        drag.connect("drag-end", self._end)
        hold = Gtk.GestureLongPress(widget=self.area)
        hold.connect("pressed", lambda *_a: self.paths and self.held_cb(self.paths[self.i]))
        self.gestures = (drag, hold)
        self.area.add_events(Gdk.EventMask.TOUCH_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)

    def show(self, paths, i=0):
        self.paths, self.i = list(paths), i
        if self.paths:
            self._load()

    def close(self):
        self.stop_slides()
        self.pix = None
        self.paths = []
        self.loading += 1

    def step(self, d):
        if self.paths:
            self.i = (self.i + d) % len(self.paths)
            self._load()

    def drop(self, path):
        self.paths = [p for p in self.paths if p != path]
        if not self.paths:
            return False
        self.i = min(self.i, len(self.paths) - 1)
        self._load()
        return True

    def _load(self):
        path = self.paths[self.i]
        self.loading += 1
        token = self.loading
        self.zoom, self.dx, self.dy = 1.0, 0.0, 0.0
        self.count.set_text("%d из %d" % (self.i + 1, len(self.paths)))
        self.titled(os.path.basename(path))
        self.note.set_text("Открываю…" if self.pix is None else "")
        alloc = self.area.get_allocation()
        side = max(alloc.width, alloc.height, 800) * 2

        def work():
            try:
                pix = pixbuf_of(path, side)
            except (GLib.Error, ValueError):
                pix = None
            GLib.idle_add(lambda: (self._loaded(token, pix), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, token, pix):
        if token != self.loading:
            return
        self.pix = pix
        self.note.set_text("" if pix else "Не удалось открыть картинку.")
        self.area.queue_draw()

    def _draw(self, _w, cr):
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        if not self.pix:
            return
        alloc = self.area.get_allocation()
        aw, ah = alloc.width, alloc.height
        k = min(aw / self.pix.get_width(), ah / self.pix.get_height()) * self.zoom
        w, h = self.pix.get_width() * k, self.pix.get_height() * k
        mx, my = max(0, (w - aw) / 2), max(0, (h - ah) / 2)
        self.dx = min(mx, max(-mx, self.dx))
        self.dy = min(my, max(-my, self.dy))
        cr.translate((aw - w) / 2 + self.dx, (ah - h) / 2 + self.dy)
        cr.scale(k, k)
        Gdk.cairo_set_source_pixbuf(cr, self.pix, 0, 0)
        cr.paint()

    def zoom_by(self, f):
        if not self.pix:
            return
        self.zoom = min(4.0, max(1.0, self.zoom * f))
        if self.zoom == 1.0:
            self.dx = self.dy = 0.0
        self.area.queue_draw()

    def _begin(self, _g, _x, _y):
        self.stop_slides()
        self.start = (self.dx, self.dy)

    def _update(self, _g, ox, oy):
        if self.zoom > 1.0:
            self.dx, self.dy = self.start[0] + ox, self.start[1] + oy
            self.area.queue_draw()

    def _end(self, _g, ox, oy):
        if self.zoom == 1.0 and abs(ox) > 100 and abs(ox) > 2 * abs(oy):
            self.step(-1 if ox > 0 else 1)

    def toggle_slides(self):
        if self.slides:
            self.stop_slides()
            return
        self.btn["slide"].get_style_context().add_class("on")
        self.btn["slide"].set_label("❚❚")
        self.slides = GLib.timeout_add_seconds(SLIDE_S, lambda: (self.step(1), True)[1])

    def stop_slides(self):
        if self.slides:
            GLib.source_remove(self.slides)
            self.slides = 0
            self.btn["slide"].get_style_context().remove_class("on")
            self.btn["slide"].set_label("▶")
