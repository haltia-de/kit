# SPDX-License-Identifier: GPL-2.0
"""The camera: a viewfinder, photos taken, and a photo asked for by any
program.

    available()              is there a camera (a program hides its camera
                             button when not); it asks the devices, which
                             takes a while with libcamera, so the answer
                             is kept for SEEN_FOR seconds
    cameras(contrast=CONTRAST)  the cameras found: libcamera first, else
                             V4L2; libcamerasrc gets the control Contrast
                             (None: the camera's own)
    capture(camera=None)     a still at full resolution, saved as JPEG in
                             <XDG pictures>/Камера -> its path
    Viewfinder(camera=None)  a widget: the camera's picture, scaled to it,
                             mirrored for a front camera
    take_photo(parent=None)  a photo the person kept -> its path, or None

The pipelines run on GStreamer's threads (libcamerasrc, or v4l2src where
libcamera does not know the camera); a viewfinder gets only the latest
frame, on the GTK main thread. Finding the camera and opening it happen on
a thread of their own, never blocking the GTK loop. A viewfinder holds the
camera only while it is mapped and not paused: hidden, unmapped or
paused, its pipeline is stopped and the camera is off. A window left in
the background stays mapped (Wayland), so a program calls pause() when
its window stops being active and resume() when it is active again: a
video call in another program finds the camera free. A webcam that gives
only MJPEG is decoded with jpegdec. A front
camera is mirrored only in the viewfinder: its photos are saved as the
camera sees them, unmirrored, like the photos of other camera apps.

The picture turns with the screen: the shell writes how far the device
is turned clockwise from its natural position (normal, 90, 180 or 270) in
ROTATION_FILE, $XDG_RUNTIME_DIR/haltia/rotation. A videoflip after the
source (in the viewfinder, in capture(), and in the element of
Camera.make() where the file is or the sensor is mounted turned) turns the
picture by that and by the sensor's mounting (libcamera's Rotation), and
follows the file while it plays. Without the file (a PC): no turn.

A camera another program holds is the state BUSY, never an exception into
GTK: the viewfinder says so itself and tells changed(state), so a program
can say it its own way («Камера занята звонком»). capture() raises
CameraError, whose state is BUSY, NONE or FAILED.

take_photo() runs the Camera app (APP) as its command line with
--take-photo. The contract with the app:
- with --take-photo it runs as an instance of its own
  (Gio.ApplicationFlags.NON_UNIQUE), never handing the request to an
  instance already open: that one would answer nothing, read here as a
  cancel. If the open instance holds the camera, the new one shows BUSY;
- the photo kept: its absolute path as the last line of standard output,
  then exit 0;
- cancelled: exit 0 without a path.
Other lines of the output are not read. Where the app is not installed
(or inside Flatpak, which does not see it), or it exits with an error
(a crash, or an older app that does not know --take-photo), a dialog with
a Viewfinder takes the photo in this process.
"""
import os
import threading
import time
import weakref

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
except (ImportError, ValueError, GLib.Error):
    Gst = None  # no GStreamer, or it would not start: no camera
try:
    gi.require_version("GstVideo", "1.0")
    from gi.repository import GstVideo  # noqa: E402
except (ImportError, ValueError):
    GstVideo = None  # frames read as packed rows

APP = "io.github.haltia_de.Camera"
FOLDER = "Камера"  # in the pictures folder

OFF, ON, BUSY, NONE, FAILED = "off", "on", "busy", "none", "failed"
NOTES = {BUSY: "Камера занята другой программой.", NONE: "Камеры нет.",
         FAILED: "Камера не включилась."}

RAW, JPEG = "video/x-raw", "image/jpeg"  # the kinds of picture a camera gives
# The viewfinder asks the camera for a picture no larger than this: enough
# for a screen, light to convert every frame.
PREVIEW_RAW = RAW + ",width=[1,1280],height=[1,1280]"
PREVIEW_JPEG = JPEG + ",width=[1,1280],height=[1,1280]"  # a webcam of MJPEG only
SEEN_FOR = 30  # seconds available() keeps its answer
SETTLE = 5  # frames let go by before a still: exposure and focus settle
RANK = {"libcamerasrc": 0, "v4l2src": 1}  # the others: pipewiresrc and the like
# libcamera's control Contrast for libcamerasrc: its soft ISP gives a milky
# picture at 1.0 (no colour matrix); a little more is a table, almost free.
CONTRAST = 1.25
# How far the device is turned clockwise, written by the shell.
ROTATION_FILE = os.path.join(GLib.get_user_runtime_dir(), "haltia", "rotation")
SCREEN = {"normal": 0, "90": 90, "180": 180, "270": 270}
# videoflip's video-direction for a clockwise turn of the picture.
DIRECTIONS = {0: "identity", 90: "90r", 180: "180", 270: "90l"}


class CameraError(Exception):
    def __init__(self, state):
        super().__init__(NOTES.get(state, state))
        self.state = state


def _contrast(src, value):
    """libcamera's control Contrast on a libcamerasrc that has it as a
    property (libcamera 0.5 and later); other sources are left as they are."""
    if value is None or _kind(src) != "libcamerasrc" or src.find_property("contrast") is None:
        return
    src.set_property("contrast", float(value))


def screen():
    """How far the device is turned clockwise (0, 90, 180, 270), as the
    shell writes it in ROTATION_FILE; 0 without the file."""
    try:
        with open(ROTATION_FILE, encoding="utf-8") as fh:
            return SCREEN.get(fh.read().strip(), 0)
    except (OSError, ValueError):
        return 0


def _turn(cam, turned):
    """The clockwise turn (0, 90, 180, 270) of cam's picture for the device
    turned clockwise by turned: the sensor's mounting, then the screen's.
    A front camera looks the other way, so the screen turns its unmirrored
    picture back the other way (as Android's JPEG orientation does)."""
    mount = int(round((cam.mount or 0) / 90.0)) * 90
    return (mount + (-turned if cam.front else turned)) % 360


_flips = weakref.WeakKeyDictionary()  # the videoflips playing -> their camera
_flips_lock = threading.Lock()
_watch = []  # the monitor of ROTATION_FILE, made once


def _direct(flip, degrees):
    if flip.find_property("video-direction") is not None:
        Gst.util_set_object_arg(flip, "video-direction", DIRECTIONS[degrees])
    else:  # GStreamer before 1.10
        Gst.util_set_object_arg(flip, "method", {0: "none", 90: "clockwise", 180: "rotate-180",
                                                 270: "counterclockwise"}[degrees])


def _turn_all(*_a):
    """On the main thread, the file changed: every videoflip turned anew."""
    turned = screen()
    with _flips_lock:
        live = list(_flips.items())
    for flip, cam in live:
        _direct(flip, _turn(cam, turned))


def _flip(cam):
    """A videoflip turning cam's picture with the screen, followed while it
    lives -> the element, or None without videoflip."""
    flip = Gst.ElementFactory.make("videoflip", "turn")
    if flip is None:
        return None
    _direct(flip, _turn(cam, screen()))
    # An attribute keeps the Python wrapper alive as long as the element,
    # so the weak entry lasts as long as the element does.
    flip.haltia_camera = cam.name
    with _flips_lock:
        try:
            _flips[flip] = cam
        except TypeError:  # an object without weak references: not followed
            pass
        if not _watch:
            try:  # made on the default main context, whatever the thread
                mon = Gio.File.new_for_path(ROTATION_FILE).monitor_file(
                    Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed", _turn_all)
                _watch.append(mon)
            except GLib.Error:
                pass
    return flip


def _turned(cam, src):
    """src -> videoflip in a bin, its pad "src"; src itself where the flip
    does not link (a picture videoflip does not take, like MJPEG)."""
    flip = _flip(cam)
    if flip is None:
        return src
    box = Gst.Bin.new(None)
    box.add(src)
    box.add(flip)
    if not src.link(flip):
        box.remove(src)
        return src
    box.add_pad(Gst.GhostPad.new("src", flip.get_static_pad("src")))
    return box


class Camera:
    """A camera: name, front (faces the person: mirrored in the viewfinder),
    mount (the sensor's clockwise turn, libcamera's Rotation), make() -> a
    new GStreamer element for it: its source, with contrast (None: the
    camera's own) where it is libcamerasrc, and a videoflip turning the
    picture with the screen (a bin) where ROTATION_FILE is or mount is not
    0. source() is the source alone."""

    def __init__(self, name, make, front=False, contrast=CONTRAST, mount=0):
        self.name, self.front, self.contrast, self.mount = name, front, contrast, mount
        self._make = make

    def source(self):
        src = self._make()
        if src is not None:
            _contrast(src, self.contrast)
        return src

    def make(self):
        src = self.source()
        if src is None or Gst is None or not isinstance(src, Gst.Element) or \
                not (_turn(self, 0) or os.path.exists(ROTATION_FILE)):
            return src
        return _turned(self, src)


def _mount(device):
    """The sensor's clockwise turn in degrees from the device's properties
    (libcamera's Rotation, where its provider gives it), else 0."""
    props = device.get_properties()
    if props is None:
        return 0
    for key in ("api.libcamera.rotation", "api.libcamera.Rotation", "camera.rotation",
                "rotation"):
        try:
            value = props.get_value(key)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return 0


def _front(device):
    props = device.get_properties()
    if props is None:
        return False
    for key in ("api.libcamera.location", "api.libcamera.Location", "camera.location",
                "location"):
        where = props.get_string(key)
        if where:  # "front", or libcamera's "CameraLocationFront"
            where = where.lower()
            if where.startswith("cameralocation"):
                where = where[len("cameralocation"):]
            return where in ("front", "external")
    return props.get_string("device.bus") == "usb"  # a webcam faces the person


def cameras(contrast=CONTRAST):
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
                             _front(dev), contrast, _mount(dev))))
    if not found:
        return []
    # The same camera is often seen by several: only the best kind is kept.
    best = min(rank for rank, _cam in found)
    return [cam for rank, cam in found if rank == best]


_seen = []  # (when, the cameras found) of the last asking


def _known():
    """cameras(), kept for SEEN_FOR seconds: asking the devices blocks (a
    second or more with libcamera)."""
    now = time.monotonic()
    seen = _seen[:]
    if not seen or now - seen[0] > SEEN_FOR:
        seen = [now, cameras()]
        _seen[:] = seen
    return seen[1]


def available():
    """Is there a camera. The answer is kept for SEEN_FOR seconds."""
    return bool(_known())


def _first():
    found = _known()
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


def _sizes(src):
    """The largest picture of each kind (RAW, JPEG) a V4L2 camera gives:
    {kind: (width, height)}; empty for other cameras (then they choose)."""
    if _kind(src) != "v4l2src":
        return {}
    best = {}
    if src.set_state(Gst.State.READY) != Gst.StateChangeReturn.FAILURE:
        caps = src.get_static_pad("src").query_caps(None)
        for i in range(caps.get_size()):
            s = caps.get_structure(i)
            kind = s.get_name()
            if kind not in (RAW, JPEG):
                continue
            (ok_w, w), (ok_h, h) = s.get_int("width"), s.get_int("height")
            if ok_w and ok_h and (kind not in best or w * h > best[kind][0] * best[kind][1]):
                best[kind] = (w, h)
    src.set_state(Gst.State.NULL)
    return best


def _pipeline(src, caps, out, flip=None):
    """src (caps: raw, or JPEG decoded here) -> convert, turn (flip, a
    videoflip, if any) and scale -> out -> an appsink keeping the latest
    frame: (pipeline, sink)."""
    names = (["jpegdec"] if caps.startswith(JPEG) else []) + \
        ["videoconvert", "videoscale", "capsfilter", "appsink"]
    parts = [Gst.ElementFactory.make(n, None) for n in names]
    if any(p is None for p in parts):
        raise CameraError(FAILED)
    if flip is not None:
        parts.insert(len(parts) - 3, flip)  # after videoconvert
    filt, sink = parts[-2:]
    filt.set_property("caps", Gst.Caps.from_string(out))
    sink.set_property("max-buffers", 1)
    sink.set_property("drop", True)
    sink.set_property("sync", False)
    pipe = Gst.Pipeline.new(None)
    for p in [src] + parts:
        pipe.add(p)
    if not (src.link_filtered(parts[0], Gst.Caps.from_string(caps))
            and all(a.link(b) for a, b in zip(parts, parts[1:]))):
        raise CameraError(FAILED)
    return pipe, sink


def _layout(sample, width):
    """(offset, stride) of a frame's rows: the buffer's video meta, else
    the caps', else rows packed one after another."""
    if GstVideo is not None:
        try:
            meta = GstVideo.buffer_get_video_meta(sample.get_buffer())
            if meta is not None:
                return int(meta.offset[0]), int(meta.stride[0])
            info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
            if info is not None:
                return int(info.offset[0]), int(info.stride[0])
        except Exception:  # an older GstVideo or PyGObject without these fields
            pass
    return 0, width * 4


def _frame(sample):
    """(width, height, bytes) of a sample of 4 bytes a pixel, its rows
    packed (width * 4 bytes each) whatever the stride of the buffer."""
    s = sample.get_caps().get_structure(0)
    w, h = s.get_int("width")[1], s.get_int("height")[1]
    buf = sample.get_buffer()
    offset, stride = _layout(sample, w)
    row = w * 4
    if stride < row or offset + stride * (h - 1) + row > buf.get_size():
        offset, stride = 0, row  # a layout that does not fit: as packed
    if stride == row:
        return w, h, buf.extract_dup(offset, row * h)
    data = buf.extract_dup(0, buf.get_size())
    return w, h, b"".join(data[offset + y * stride:offset + y * stride + row] for y in range(h))


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
    src = cam.source()
    if src is None:
        raise CameraError(FAILED)
    _role(src, "still-capture")
    sizes = _sizes(src)
    kind = RAW  # or JPEG, where the webcam gives a larger picture as MJPEG only
    if JPEG in sizes and (RAW not in sizes or
                          sizes[JPEG][0] * sizes[JPEG][1] > sizes[RAW][0] * sizes[RAW][1]):
        kind = JPEG
    size = sizes.get(kind)
    caps = kind + (",width=%d,height=%d" % size if size else "")
    pipe, sink = _pipeline(src, caps, RAW + ",format=RGBx,pixel-aspect-ratio=1/1", _flip(cam))
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
    thread). camera None: the first of cameras(). start() returns at once:
    the camera is found and its pipeline made on a thread of its own."""

    def __init__(self, camera, shown, changed, post=None):
        self.camera, self.shown, self.changed = camera, shown, changed
        self.post = post or GLib.idle_add
        self.used = None  # the camera of the running pipeline
        self.pipe = self.bus = None
        self.state = OFF
        self.gen = 0
        self.asked = 0  # the latest start()
        self.opening = 0  # the start() being opened, 0 for none
        self.frame = None
        self.pending = False
        self.lock = threading.Lock()

    def start(self):
        if self.pipe is not None or self.opening:
            return
        self.asked += 1
        self.opening = self.asked
        threading.Thread(target=self._open, args=(self.asked,), daemon=True).start()

    def _open(self, asked):
        """On a thread of its own: finding the camera and asking it its sizes
        block, the GTK loop does not wait for them."""
        got = None, None, None, FAILED
        try:
            cam = self.camera or (_first() if Gst is not None else None)
            if cam is None:
                raise CameraError(NONE)
            src = cam.source()
            if src is None:
                raise CameraError(FAILED)
            _role(src, "view-finder")
            sizes = _sizes(src)
            caps = PREVIEW_JPEG if JPEG in sizes and RAW not in sizes else PREVIEW_RAW
            pipe, sink = _pipeline(src, caps, RAW + ",format=RGBA,pixel-aspect-ratio=1/1",
                                   _flip(cam))
            got = cam, pipe, sink, None
        except CameraError as e:
            got = None, None, None, e.state
        except Exception:  # whatever it was: said as FAILED, never left opening
            pass
        finally:
            self.post(lambda: (self._opened(asked, *got), False)[1])

    def _opened(self, asked, cam, pipe, sink, state):
        if asked != self.opening:  # stopped meanwhile
            if pipe is not None:
                pipe.set_state(Gst.State.NULL)
            return
        self.opening = 0
        if state is not None:
            self._state(state)
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
        self.opening = 0  # one being opened is let go when it comes
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
    (mirror None: as the camera says; the photo itself is never mirrored).
    The camera is on only while the widget is mapped and not paused:
    pause() when the window stops being active, resume() when it is active
    again. changed(state) is told the state; capture(done) takes a photo
    with the orientation in .orientation and always calls done on the main
    thread: with its path, or with None and .failure the CameraError
    (state BUSY, NONE or FAILED; the viewfinder says it too). After a
    photo, taken or not, the picture comes back."""

    def __init__(self, camera=None, mirror=None, changed=None):
        super().__init__()
        gallery._viewer_css()
        self.get_style_context().add_class("haltia-viewer")
        self.changed = changed or (lambda _s: None)
        self.mirror = mirror
        self.orientation = 1
        self.pix = None
        self.taking = False
        self.paused = False
        self.failure = None  # the CameraError of the last capture, None when taken
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
        if not self.taking and not self.paused:
            self.preview.start()

    def pause(self):
        """The camera off, for a window that is not active (still mapped)."""
        self.paused = True
        self.preview.stop()

    def resume(self):
        self.paused = False
        if self.get_mapped() and not self.taking:
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
        _shoot(cam, self.orientation, lambda path, state: self._taken(done, path, state))

    def _taken(self, done, path, state):
        self.taking = False
        self.note.set_text("")
        self.failure = CameraError(state) if state is not None else None
        if state is not None:
            self.preview._state(state)
        try:
            done(path)
        finally:
            if self.get_mapped() and not self.paused:
                self.preview.start()  # the picture back, after a failed still too


def _shoot(cam, orientation, done, post=None):
    """capture() on a thread; done(path, None) or done(None, state) through
    post (on the main thread) whatever happened there."""
    post = post or GLib.idle_add

    def work():
        path, state = None, FAILED
        try:
            path, state = capture(cam, orientation=orientation), None
        except CameraError as e:
            state = e.state
        except Exception:  # unexpected: FAILED, never «Снимаю…» for ever
            pass
        finally:
            post(lambda: (done(path, state), False)[1])

    threading.Thread(target=work, daemon=True).start()


def _app_argv():
    """The Camera app's command line with --take-photo, or None where it is
    not installed."""
    info = Gio.DesktopAppInfo.new(APP + ".desktop")
    if info is None or not info.get_commandline():
        return None
    try:
        args = GLib.shell_parse_argv(info.get_commandline())[1]
    except GLib.Error:
        return None
    # Without the field codes (%U and the like): nothing to open.
    return [a for a in args if not (len(a) == 2 and a[0] == "%")] + ["--take-photo"]


def _ask(argv, parent=None):
    """Runs argv, the window parent insensitive meanwhile -> the absolute
    path of a file on the last line of its output when it exits 0, None
    when it exits 0 without one (cancelled), False when it did not run or
    failed (then the dialog here)."""
    try:
        proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.STDOUT_PIPE)
    except GLib.Error:
        return False
    loop = GLib.MainLoop()
    out = []

    def finished(p, res):
        try:
            _ok, stdout, _err = p.communicate_utf8_finish(res)
            out.append((p.get_successful(), stdout or ""))
        except GLib.Error:
            pass
        loop.quit()

    if parent is not None:
        parent.set_sensitive(False)
    proc.communicate_utf8_async(None, None, finished)
    loop.run()
    if parent is not None:
        parent.set_sensitive(True)
    if not out or not out[0][0]:
        return False
    lines = [line.strip() for line in out[0][1].splitlines() if line.strip()]
    path = lines[-1] if lines else None
    return path if path and os.path.isabs(path) and os.path.isfile(path) else None


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
    # The camera free for others while the dialog is in the background.
    dialog.connect("notify::is-active",
                   lambda d, _p: view.resume() if d.is_active() else view.pause())
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
    argv = _app_argv()
    path = _ask(argv, parent) if argv else False
    if path is not False:
        return path
    return _take_here(parent)
