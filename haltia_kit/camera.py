# SPDX-License-Identifier: GPL-2.0
"""The camera: a viewfinder, photos taken, and a photo asked for by any
program.

    available()              is there a camera (a program hides its camera
                             button when not)
    cameras()                the cameras found: libcamera first, else V4L2
    capture(camera=None)     a still at full resolution, saved as JPEG in
                             <XDG pictures>/Камера -> its path
    Viewfinder(camera=None)  a widget: the camera's picture, scaled to it,
                             mirrored for a front camera
    take_photo(parent=None)  a photo the person kept -> its path, or None

The pipelines run on GStreamer's threads (libcamerasrc, or v4l2src where
libcamera does not know the camera); a viewfinder gets only the latest
frame, on the GTK main thread. A viewfinder holds the camera only while it
is mapped: hidden or unmapped, its pipeline is stopped and the camera is
off.

A camera another program holds is the state BUSY, never an exception into
GTK: the viewfinder says so itself and tells changed(state), so a program
can say it its own way («Камера занята звонком»). capture() raises
CameraError, whose state is BUSY, NONE or FAILED.

take_photo() runs the Camera app (APP) as its command line with
--take-photo: the app prints the path of the photo kept on its standard
output and exits, or exits without one when the person cancelled. Where
the app is not installed (or inside Flatpak, which does not see it), a
dialog with a Viewfinder takes the photo in this process.
"""
import os
import threading
import time

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402
from PIL import Image  # noqa: E402

from . import dialogs, gallery, photos  # noqa: E402

try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # noqa: E402
    Gst.init(None)
except (ImportError, ValueError):
    Gst = None  # no GStreamer: no camera

APP = "io.github.haltia_de.Camera"
FOLDER = "Камера"  # in the pictures folder

OFF, ON, BUSY, NONE, FAILED = "off", "on", "busy", "none", "failed"
NOTES = {BUSY: "Камера занята другой программой.", NONE: "Камеры нет.",
         FAILED: "Камера не включилась."}

# The viewfinder asks the camera for a picture no larger than this: enough
# for a screen, light to convert every frame.
PREVIEW_RAW = "video/x-raw,width=[1,1280],height=[1,1280]"
SETTLE = 5  # frames let go by before a still: exposure and focus settle
RANK = {"libcamerasrc": 0, "v4l2src": 1}  # the others: pipewiresrc and the like


class CameraError(Exception):
    def __init__(self, state):
        super().__init__(NOTES.get(state, state))
        self.state = state


class Camera:
    """A camera: name, front (faces the person: mirrored in the viewfinder),
    make() -> a new GStreamer source element for it."""

    def __init__(self, name, make, front=False):
        self.name, self.make, self.front = name, make, front


def _front(device):
    props = device.get_properties()
    if props is None:
        return False
    for key in ("api.libcamera.location", "camera.location", "location"):
        where = props.get_string(key)
        if where:
            return where in ("front", "external")
    return props.get_string("device.bus") == "usb"  # a webcam faces the person


def cameras():
    if Gst is None:
        return []
    monitor = Gst.DeviceMonitor.new()
    monitor.add_filter("Video/Source", None)
    monitor.set_show_all_devices(True)  # libcamera's and V4L2's, not just PipeWire's
    if not monitor.start():
        return []
    try:
        devices = monitor.get_devices() or []
    finally:
        monitor.stop()
    found = []
    for dev in devices:
        element = dev.create_element(None)
        kind = element.get_factory().get_name() if element else None
        found.append((RANK.get(kind, len(RANK)),
                      Camera(dev.get_display_name(), lambda d=dev: d.create_element(None),
                             _front(dev))))
    if not found:
        return []
    # The same camera is often seen by several: only the best kind is kept.
    best = min(rank for rank, _cam in found)
    return [cam for rank, cam in found if rank == best]


def available():
    return bool(cameras())


def _first():
    found = cameras()
    return found[0] if found else None


def folder():
    pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or \
        os.path.join(GLib.get_home_dir(), "Изображения")
    return os.path.join(pictures, FOLDER)


def _state_of(err):
    """BUSY or FAILED for a GStreamer error (a GLib.Error)."""
    text = (err.message or "").lower()
    if (err.domain == "gst-resource-error-quark" and err.code == int(Gst.ResourceError.BUSY)) \
            or "busy" in text or "in use" in text:
        return BUSY
    return FAILED


def _kind(src):
    factory = src.get_factory()
    return factory.get_name() if factory else None


def _role(src, role):
    """libcamera picks its sizes by the stream's role."""
    if _kind(src) == "libcamerasrc":
        pad = src.get_static_pad("src")
        if pad is not None:
            Gst.util_set_object_arg(pad, "stream-role", role)


def _largest(src):
    """The largest raw picture a V4L2 camera gives, (width, height), or None
    (then the camera chooses)."""
    if _kind(src) != "v4l2src":
        return None
    best = None
    if src.set_state(Gst.State.READY) != Gst.StateChangeReturn.FAILURE:
        caps = src.get_static_pad("src").query_caps(None)
        for i in range(caps.get_size()):
            s = caps.get_structure(i)
            if s.get_name() != "video/x-raw":
                continue
            (ok_w, w), (ok_h, h) = s.get_int("width"), s.get_int("height")
            if ok_w and ok_h and (best is None or w * h > best[0] * best[1]):
                best = (w, h)
    src.set_state(Gst.State.NULL)
    return best


def _pipeline(src, raw, out):
    """src (caps raw) -> convert and scale -> out -> an appsink keeping the
    latest frame: (pipeline, sink)."""
    parts = [src] + [Gst.ElementFactory.make(n, None)
                     for n in ("videoconvert", "videoscale", "capsfilter", "appsink")]
    if any(p is None for p in parts):
        raise CameraError(FAILED)
    _src, conv, scale, filt, sink = parts
    filt.set_property("caps", Gst.Caps.from_string(out))
    sink.set_property("max-buffers", 1)
    sink.set_property("drop", True)
    sink.set_property("sync", False)
    pipe = Gst.Pipeline.new(None)
    for p in parts:
        pipe.add(p)
    if not (src.link_filtered(conv, Gst.Caps.from_string(raw)) and conv.link(scale)
            and scale.link(filt) and filt.link(sink)):
        raise CameraError(FAILED)
    return pipe, sink


def _frame(sample):
    """(width, height, bytes) of a sample of 4 bytes a pixel."""
    s = sample.get_caps().get_structure(0)
    buf = sample.get_buffer()
    return s.get_int("width")[1], s.get_int("height")[1], buf.extract_dup(0, buf.get_size())


def _save(image, where, orientation):
    """image as a JPEG in where, named by the date and time, written whole
    or not at all -> its path."""
    os.makedirs(where, exist_ok=True)
    stamp = time.strftime("IMG_%Y%m%d_%H%M%S")
    part = os.path.join(where, ".%s.jpg.part" % stamp)
    exif = Image.Exif()
    exif[0x0112] = orientation
    try:
        with open(part, "wb") as fh:
            image.save(fh, "JPEG", quality=92, exif=exif)
            fh.flush()
            os.fsync(fh.fileno())
        n = 0
        while True:
            path = os.path.join(where, stamp + ("_%d" % n if n else "") + ".jpg")
            try:
                os.link(part, path)  # never over another photo
                return path
            except FileExistsError:
                n += 1
            except OSError:  # no hard links (FAT): the name is free just now
                if not os.path.lexists(path):
                    os.replace(part, path)
                    return path
                n += 1
    finally:
        if os.path.lexists(part):
            os.remove(part)


def capture(camera=None, where=None, orientation=1, settle=SETTLE, timeout=10):
    """A still at the camera's full resolution, saved as a JPEG with the
    EXIF orientation given (1 upright, 6 turned right, 8 left, 3 upside
    down) in where (folder()) -> its path. Blocks for about a second: call
    it from a thread, or use Viewfinder.capture. Raises CameraError."""
    if Gst is None:
        raise CameraError(NONE)
    cam = camera or _first()
    if cam is None:
        raise CameraError(NONE)
    src = cam.make()
    if src is None:
        raise CameraError(FAILED)
    _role(src, "still-capture")
    size = _largest(src)
    raw = "video/x-raw" + (",width=%d,height=%d" % size if size else "")
    pipe, sink = _pipeline(src, raw, "video/x-raw,format=RGBx,pixel-aspect-ratio=1/1")
    sample, frames = None, 0
    try:
        pipe.set_state(Gst.State.PLAYING)
        end = time.monotonic() + timeout
        while frames <= settle and time.monotonic() < end:
            got = sink.emit("try-pull-sample", Gst.SECOND // 5)
            if got is not None:
                sample, frames = got, frames + 1
                continue
            msg = pipe.get_bus().pop_filtered(Gst.MessageType.ERROR)
            if msg is not None:  # busy, gone: said at once, not at the timeout
                raise CameraError(_state_of(msg.parse_error()[0]))
            if sink.get_property("eos"):
                break
        if sample is None:
            raise CameraError(FAILED)
    finally:
        pipe.set_state(Gst.State.NULL)
    w, h, data = _frame(sample)
    image = Image.frombuffer("RGB", (w, h), data, "raw", "RGBX", 0, 1)
    return _save(image, where or folder(), orientation)


class Preview:
    """A viewfinder's pipeline, apart from GTK: start(), stop(). shown(width,
    height, bytes of RGBA) gets the latest frame and changed(state) the
    state (OFF, ON, BUSY, NONE, FAILED), both through post (on the main
    thread). camera None: the first of cameras()."""

    def __init__(self, camera, shown, changed, post=None):
        self.camera, self.shown, self.changed = camera, shown, changed
        self.post = post or GLib.idle_add
        self.used = None  # the camera of the running pipeline
        self.pipe = self.bus = None
        self.state = OFF
        self.gen = 0
        self.frame = None
        self.pending = False
        self.lock = threading.Lock()

    def start(self):
        if self.pipe is not None:
            return
        cam = self.camera or (_first() if Gst is not None else None)
        if cam is None:
            self._state(NONE)
            return
        try:
            src = cam.make()
            if src is None:
                raise CameraError(FAILED)
            _role(src, "view-finder")
            pipe, sink = _pipeline(src, PREVIEW_RAW,
                                   "video/x-raw,format=RGBA,pixel-aspect-ratio=1/1")
        except CameraError as e:
            self._state(e.state)
            return
        self.used, self.pipe = cam, pipe
        self.gen += 1
        sink.set_property("emit-signals", True)
        sink.connect("new-sample", self._sample, self.gen)
        self.bus = pipe.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect("message", self._message)
        if pipe.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._fail(self.bus.pop_filtered(Gst.MessageType.ERROR))

    def stop(self, state=OFF):
        if self.pipe is None:
            return
        self.gen += 1
        self.pipe.set_state(Gst.State.NULL)  # the camera off
        self.bus.remove_signal_watch()
        self.pipe = self.bus = None
        self._state(state)

    def _fail(self, msg):
        self.stop(_state_of(msg.parse_error()[0]) if msg else FAILED)

    def _message(self, bus, msg):
        if bus is not self.bus:
            return  # a pipeline stopped since
        if msg.type == Gst.MessageType.ERROR:
            self._fail(msg)
        elif msg.type == Gst.MessageType.EOS:
            self._fail(None)

    def _sample(self, sink, gen):
        """On a GStreamer thread: keep the frame, wake the main thread once."""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        frame = (gen,) + _frame(sample)
        with self.lock:
            self.frame = frame
            if self.pending:
                return Gst.FlowReturn.OK
            self.pending = True
        self.post(self._deliver)
        return Gst.FlowReturn.OK

    def _deliver(self):
        with self.lock:
            gen, w, h, data = self.frame
            self.pending = False
        if gen == self.gen:
            self._state(ON)
            self.shown(w, h, data)
        return False

    def _state(self, state):
        if state != self.state:
            self.state = state
            self.changed(state)


class Viewfinder(Gtk.Overlay):
    """The camera's picture, fitted on black, mirrored for a front camera
    (mirror None: as the camera says). The camera is on only while the
    widget is mapped. changed(state) is told the state; capture(done) takes
    a photo with the orientation in .orientation and gives done its path,
    or None (the viewfinder says why)."""

    def __init__(self, camera=None, mirror=None, changed=None):
        super().__init__()
        gallery._viewer_css()
        self.get_style_context().add_class("haltia-viewer")
        self.changed = changed or (lambda _s: None)
        self.mirror = mirror
        self.orientation = 1
        self.pix = None
        self.taking = False
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_size_request(160, 120)
        self.area.connect("draw", self._draw)
        self.add(self.area)
        self.note = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, wrap=True,
                              justify=Gtk.Justification.CENTER)
        self.note.get_style_context().add_class("haltia-note")
        self.add_overlay(self.note)
        self.preview = Preview(camera, self._shown, self._changed)
        self.connect("map", self._map)
        self.connect("unmap", lambda _w: self.preview.stop())

    @property
    def state(self):
        return self.preview.state

    def _map(self, _w):
        if not self.taking:
            self.preview.start()

    def _shown(self, w, h, data):
        self.pix = GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
        self.area.queue_draw()

    def _changed(self, state):
        self.note.set_text(NOTES.get(state, ""))
        if state != ON:
            self.pix = None
            self.area.queue_draw()
        self.changed(state)

    def _draw(self, _w, cr):
        cr.set_source_rgb(0, 0, 0)
        cr.paint()
        if self.pix is None:
            return
        alloc = self.area.get_allocation()
        aw, ah = alloc.width, alloc.height
        pw, ph = self.pix.get_width(), self.pix.get_height()
        k = min(aw / pw, ah / ph)
        mirror = self.mirror
        if mirror is None:
            mirror = bool(self.preview.used and self.preview.used.front)
        if mirror:
            cr.translate(aw, 0)
            cr.scale(-1, 1)
        cr.translate((aw - pw * k) / 2, (ah - ph * k) / 2)
        cr.scale(k, k)
        Gdk.cairo_set_source_pixbuf(cr, self.pix, 0, 0)
        cr.paint()

    def capture(self, done):
        if self.taking:
            return
        self.taking = True
        cam = self.preview.used or self.preview.camera
        self.preview.stop()  # one pipeline at a time holds the camera
        self.note.set_text("Снимаю…")
        orientation = self.orientation

        def work():
            try:
                path, state = capture(cam, orientation=orientation), None
            except CameraError as e:
                path, state = None, e.state
            except (OSError, ValueError):
                path, state = None, FAILED
            GLib.idle_add(lambda: (self._taken(done, path, state), False)[1])

        threading.Thread(target=work, daemon=True).start()

    def _taken(self, done, path, state):
        self.taking = False
        self.note.set_text("")
        if state is not None:
            self.preview._state(state)
        done(path)
        if state is None and self.get_mapped():
            self.preview.start()


def _from_app(parent):
    """The Camera app's answer: a path, None when cancelled, False when there
    is no app to ask."""
    info = Gio.DesktopAppInfo.new(APP + ".desktop")
    if info is None or not info.get_commandline():
        return False
    try:
        argv = [a for a in GLib.shell_parse_argv(info.get_commandline())[1]
                if not (len(a) == 2 and a[0] == "%")]  # the field codes
        proc = Gio.Subprocess.new(argv + ["--take-photo"], Gio.SubprocessFlags.STDOUT_PIPE)
    except GLib.Error:
        return False
    loop = GLib.MainLoop()
    out = []

    def finished(p, res):
        try:
            _ok, stdout, _err = p.communicate_utf8_finish(res)
            if p.get_successful():
                out.append(stdout or "")
        except GLib.Error:
            pass
        loop.quit()

    if parent is not None:
        parent.set_sensitive(False)
    proc.communicate_utf8_async(None, None, finished)
    loop.run()
    if parent is not None:
        parent.set_sensitive(True)
    lines = [line.strip() for line in "".join(out).splitlines() if line.strip()]
    path = lines[-1] if lines else None
    return path if path and os.path.isfile(path) else None


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


SHOOT, AGAIN = 1, 2


def _take_here(parent):
    """The dialog of take_photo where there is no Camera app."""
    if not available():
        dialogs.info(parent, NOTES[NONE])
        return None
    dialog, box = dialogs._dialog(parent, "Снимок")
    dialog.set_default_size(720, 640)
    stack = Gtk.Stack(vexpand=True)
    view = Viewfinder()
    shot = Gtk.Image()
    stack.add_named(view, "view")
    stack.add_named(shot, "shot")
    box.pack_start(stack, True, True, 0)
    dialog.add_button(dialogs.CANCEL, Gtk.ResponseType.CANCEL)
    again = dialog.add_button("Переснять", AGAIN)
    take = dialog.add_button("Снять", SHOOT)
    keep = dialog.add_button("Оставить", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    taken, closed = [], []

    def buttons(done):
        take.set_visible(not done)
        again.set_visible(done)
        keep.set_visible(done)

    def show(path):
        if closed:
            if path:
                _remove(path)  # cancelled while it was being taken
            return
        take.set_sensitive(True)
        if path is None:
            return  # the viewfinder says why
        taken.append(path)
        alloc = stack.get_allocation()
        k = stack.get_scale_factor()
        try:
            pix = photos.pixbuf_of(path, box=(max(alloc.width, 320) * k,
                                              max(alloc.height, 240) * k))
        except ValueError:
            pix = None
        shot.set_from_pixbuf(pix)
        stack.set_visible_child_name("shot")
        buttons(True)

    dialog.show_all()
    buttons(False)
    kept = None
    while True:
        res = dialog.run()
        if res == SHOOT:
            take.set_sensitive(False)
            view.capture(show)
            continue
        if res == AGAIN:
            if taken:
                _remove(taken.pop())
            stack.set_visible_child_name("view")
            buttons(False)
            continue
        if res == Gtk.ResponseType.OK and taken:
            kept = taken.pop()
        break
    closed.append(True)
    for path in taken:
        _remove(path)
    dialog.destroy()
    return kept


def take_photo(parent=None):
    """A photo for any program: the Camera app, or a dialog here -> the path
    of the photo the person kept, or None when they cancelled."""
    path = _from_app(parent)
    if path is not False:
        return path
    return _take_here(parent)
