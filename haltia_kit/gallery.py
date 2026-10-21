# SPDX-License-Identifier: GPL-2.0
"""The GTK side of photos.py: Thumbs and Viewer (also photos.Thumbs and
photos.Viewer). Kept apart so that the reader process, which imports
photos.py, never loads Gtk.

    Thumbs()               small pictures made and read in a thread, set on
                           Gtk.Images on the GTK side
    Viewer(titled, held)   a widget: photos one at a time on black

Both decode only in the reader process (photos.pixbuf_of). The viewer asks
for a photo at the size of its screen, one at a time (the newest wins:
photos stepped past are not decoded), and keeps the last few as cairo
surfaces, so a frame only paints one.

Taps only in the viewer: ‹ › and a swipe go through the photos, + − zoom,
a drag pans, ▶ shows them on by themselves; no pinch or double tap, which
some touch screens (the Haltia shell's touch filter) do not pass.
"""
import os
import queue
import threading

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import photos  # noqa: E402

SLIDE_S = 4
KEPT = 3  # photos kept decoded in the viewer: the one shown and the last ones


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
            thumb = photos.thumbnail(path)
            if not thumb:
                continue
            try:
                if crop:
                    pix = photos.square(photos.pixbuf_of(thumb, size=size), size)
                else:
                    pix = photos.pixbuf_of(thumb, box=(size, size))
            except ValueError:
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
.haltia-viewer { background-color: #000000; }
.haltia-viewer label.haltia-note { color: #ffffff; font-size: 24px; }
.haltia-vbar { background-color: rgba(0,0,0,0.55); border-radius: 18px; padding: 8px; }
.haltia-vbar button { background-image: none; background-color: rgba(255,255,255,0.18);
  color: #ffffff; border: none; box-shadow: none; border-radius: 14px;
  min-height: 64px; min-width: 76px; font-size: 26px; }
.haltia-vbar button.on { background-color: #3584e4; }
.haltia-vbar label { color: #ffffff; font-size: 20px; margin: 0 8px; }
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
        self.get_style_context().add_class("haltia-viewer")
        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self._draw)
        self.add(self.area)
        self.note = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.note.get_style_context().add_class("haltia-note")
        self.add_overlay(self.note)
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, valign=Gtk.Align.END,
                      margin_bottom=16)
        bar.get_style_context().add_class("haltia-vbar")
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
        self.shown = None  # (box, cairo surface, width, height)
        self.kept = {}  # path -> the same, the latest KEPT
        self.zoom, self.dx, self.dy = 1.0, 0.0, 0.0
        self.start = (0.0, 0.0)
        self.loading = 0
        self.slides = 0
        self.wanted = None  # (token, path, box) for the loader, the newest only
        self.cond = threading.Condition()
        self.loader = None
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
        self.shown = None
        self.kept.clear()
        self.paths = []
        self.loading += 1
        with self.cond:
            self.wanted = None

    def step(self, d):
        if self.paths:
            self.i = (self.i + d) % len(self.paths)
            self._load()

    def drop(self, path):
        self.paths = [p for p in self.paths if p != path]
        self.kept.pop(path, None)
        if not self.paths:
            return False
        self.i = min(self.i, len(self.paths) - 1)
        self._load()
        return True

    def _box(self):
        """The size to decode to, in device pixels: the area's, or before it
        has one its monitor's."""
        alloc = self.area.get_allocation()
        w, h = alloc.width, alloc.height
        if w < 64 or h < 64:
            display = Gdk.Display.get_default()
            mon = display and (display.get_primary_monitor() or display.get_monitor(0))
            geo = mon.get_geometry() if mon else None
            w, h = (geo.width, geo.height) if geo else (1280, 800)
        k = self.area.get_scale_factor()
        return (w * k, h * k)

    def _load(self):
        path = self.paths[self.i]
        self.loading += 1
        token = self.loading
        self.zoom, self.dx, self.dy = 1.0, 0.0, 0.0
        self.count.set_text("%d из %d" % (self.i + 1, len(self.paths)))
        self.titled(os.path.basename(path))
        box = self._box()
        kept = self.kept.pop(path, None)
        if kept and kept[0][0] >= box[0] and kept[0][1] >= box[1]:
            self.kept[path] = kept  # now the latest
            with self.cond:
                self.wanted = None
            self._shown(kept)
            return
        self.note.set_text("Открываю…" if self.shown is None else "")
        with self.cond:
            self.wanted = (token, path, box)
            self.cond.notify()
        if self.loader is None:
            self.loader = threading.Thread(target=self._work, daemon=True)
            self.loader.start()

    def _work(self):
        """The one loader: the newest photo asked for, the others skipped."""
        while True:
            with self.cond:
                while self.wanted is None:
                    self.cond.wait()
                token, path, box = self.wanted
                self.wanted = None
            try:
                pix = photos.pixbuf_of(path, box=box)
            except ValueError:
                pix = None
            GLib.idle_add(lambda t=token, p=path, b=box, x=pix:
                          (self._loaded(t, p, b, x), False)[1])

    def _loaded(self, token, path, box, pix):
        if not self.paths:
            return  # closed meanwhile
        kept = None
        if pix is not None:
            surface = Gdk.cairo_surface_create_from_pixbuf(pix, 1, self.area.get_window())
            kept = (box, surface, pix.get_width(), pix.get_height())
            self.kept.pop(path, None)
            self.kept[path] = kept
            while len(self.kept) > KEPT:
                self.kept.pop(next(iter(self.kept)))
        if token == self.loading:
            self._shown(kept)

    def _shown(self, kept):
        self.shown = kept
        self.note.set_text("" if kept else "Не удалось открыть картинку.")
        self.area.queue_draw()

    def _draw(self, _w, cr):
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        if not self.shown:
            return
        _box, surface, pw, ph = self.shown
        alloc = self.area.get_allocation()
        aw, ah = alloc.width, alloc.height
        k = min(aw / pw, ah / ph) * self.zoom
        w, h = pw * k, ph * k
        mx, my = max(0, (w - aw) / 2), max(0, (h - ah) / 2)
        self.dx = min(mx, max(-mx, self.dx))
        self.dy = min(my, max(-my, self.dy))
        cr.translate((aw - w) / 2 + self.dx, (ah - h) / 2 + self.dy)
        cr.scale(k, k)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()

    def zoom_by(self, f):
        if not self.shown:
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
