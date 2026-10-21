# SPDX-License-Identifier: GPL-2.0
"""Photos, videos and sound files as pictures.

    kind_of(name)          "photo", "video", "audio" or None, by extension
    media_info(path, kind) {"duration", and for sound "title", "artist"}
    thumbnail(path)        its freedesktop thumbnail (~/.cache/thumbnails/
                           large, 256 px, shared with file managers), made
                           if missing or stale; None when it has none
    pixbuf_of(path, size=None, box=None)
                           a picture file at about size px on its short
                           side, or fitted into box (w, h), turned upright;
                           never scaled up

media_info, thumbnail and pixbuf_of parse the file in the reader process
(worker.py, memory capped; pictures through Pillow, pictures.py): a file
made to exhaust or crash a parser is given up on (a thumbnail remembered
as failed), the program goes on. They block: call them from a thread.
    square(pix, size)      the middle square of a pixbuf, scaled
    Thumbs()               small pictures made and read in a thread, set on
                           Gtk.Images on the GTK side
    Viewer(titled, held)   a widget: photos one at a time on black

Thumbs and Viewer are in gallery.py, loaded only when asked for: the reader
process imports this module and must not load Gtk.

Video frames and lengths come from ffmpeg/ffprobe, sound details and covers
from mutagen; without them those files simply have no picture. ffmpeg and
ffprobe read only the file itself (files come from chats and cards: no
network, no playlists reaching other files).
"""
import hashlib
import os
import shutil
import subprocess
import tempfile

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from . import pictures  # noqa: E402

PHOTO = {"jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg"}
VIDEO = {"mp4", "m4v", "mkv", "webm", "avi", "mov", "3gp", "mpg", "mpeg", "ts", "wmv", "flv"}
AUDIO = {"mp3", "m4a", "aac", "ogg", "oga", "opus", "flac", "wav", "wma", "amr"}
SIZE = 256  # the freedesktop "large" thumbnail
FAIL = "b8k-compat-1"  # the failure marker's name, kept: old markers stay valid
SAFE = ["-protocol_whitelist", "file", "-format_whitelist",
        "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpeg,mpegts,asf,flv,mp3,ogg,flac,wav,aac,amr"]
# Seconds for ffprobe and for each of ffmpeg's two tries: a video thumbnail
# (PROBE_S + 2 * FRAME_S) ends before the reader is killed (worker.TIMEOUT_S).
PROBE_S = 10
FRAME_S = 20


def _uncap():
    """In ffmpeg's and ffprobe's child: the address space up to the reader's
    hard ceiling (worker.CEILING), and no more processor time than FRAME_S."""
    try:
        import resource

        _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        resource.setrlimit(resource.RLIMIT_AS, (hard, hard))
        resource.setrlimit(resource.RLIMIT_CPU, (FRAME_S, FRAME_S))
    except (ImportError, ValueError, OSError):
        pass
# Pictures come from anywhere (chats, cards): one too large to decode in a
# tablet's memory is left alone rather than decoded.
MAX_PIXELS = pictures.MAX_PIXELS
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


_reader = []


def _worker():
    if not _reader:
        from .worker import Worker

        _reader.append(Worker())
    return _reader[0]


def media_info(path, kind=None):
    kind = kind or kind_of(path)
    if kind not in ("video", "audio"):
        return {"duration": None}
    answer = _worker().call({"op": "info", "path": path, "kind": kind}) or {}
    info = answer.get("info")
    return info if isinstance(info, dict) else {"duration": None}


def read_info(path, kind=None):
    """media_info's work, in the reader process."""
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
                 "file:" + path], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=PROBE_S, preexec_fn=_uncap)
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
    covers = getattr(f, "pictures", None)
    if covers:
        return covers[0].data
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


def pixbuf_of(path, size=None, box=None):
    """Decoded in the reader process; the pixbuf is made from its pixels, no
    GdkPixbuf loader runs here. Raises ValueError when the file cannot be
    read, is larger than MAX_PIXELS or its size cannot be told."""
    fd, out = tempfile.mkstemp(prefix="haltia-kit-")  # 0600, a name no one guesses
    try:
        os.close(fd)
        answer = _worker().call({"op": "picture", "path": path, "size": size,
                                 "box": list(box) if box else None, "out": out}) or {}
        got = answer.get("picture")
        if not isinstance(got, dict):
            raise ValueError("picture not read: %s" % path)
        w, h, alpha = got.get("width"), got.get("height"), got.get("alpha") is True
        n = 4 if alpha else 3
        if not (type(w) is int and type(h) is int and w > 0 and h > 0 and
                w * h <= MAX_PIXELS):
            raise ValueError("picture not read: %s" % path)
        with open(out, "rb") as fh:
            data = fh.read(w * h * n + 1)
        if len(data) != w * h * n:
            raise ValueError("picture not read: %s" % path)
    except OSError:
        raise ValueError("picture not read: %s" % path) from None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB,
                                           alpha, 8, w, h, w * n)


def square(pix, size):
    w, h = pix.get_width(), pix.get_height()
    side = min(w, h)
    part = pix.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)
    return part.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)


def _cache():
    return os.path.join(GLib.get_user_cache_dir(), "thumbnails")


def _names(path):
    """(uri, mtime, thumbnail path, failure path), or None for no file."""
    try:
        mtime = int(os.stat(path).st_mtime)
    except OSError:
        return None
    uri = Gio.File.new_for_path(os.path.abspath(path)).get_uri()
    name = hashlib.md5(uri.encode()).hexdigest() + ".png"
    return (uri, mtime, os.path.join(_cache(), "large", name),
            os.path.join(_cache(), "fail", FAIL, name))


def _known(names):
    """The thumbnail path, "" for a remembered failure, None if not made."""
    _uri, mtime, out, fail = names
    for done, good in ((out, True), (fail, False)):
        try:
            w, _h, text = pictures.png_text(done)  # its header only, nothing decoded
            if w and str(mtime) == text.get("Thumb::MTime"):
                return done if good else ""
        except (OSError, ValueError):
            pass
    return None


def thumbnail(path):
    """The freedesktop thumbnail of a file, made (in the reader process) if
    missing or stale; None when it has none."""
    names = _names(path)
    if names is None:
        return None
    known = _known(names)
    if known is not None:
        return known or None
    answer = _worker().call({"op": "thumb", "path": path})
    if answer is None:  # the reader died or hung on this file: never again
        _save(names, None)
        return None
    made = answer.get("thumb")
    return made if isinstance(made, str) and made == names[2] else None


def make_thumbnail(path):
    """thumbnail's work, in the reader process."""
    names = _names(path)
    if names is None:
        return None
    known = _known(names)
    if known is not None:
        return known or None
    try:
        im = _make(path)
    except Exception:
        im = None
    return _save(names, im)


def _save(names, im):
    """A Pillow image as the thumbnail, or for None the failure marker."""
    uri, mtime, out, fail = names
    target = out if im is not None else fail
    try:
        if im is None:
            from PIL import Image

            im = Image.new("RGBA", (1, 1))
        os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
        tmp = target + ".%d.tmp" % os.getpid()
        pictures.save_png(im, tmp, {"Thumb::URI": uri, "Thumb::MTime": str(mtime)})
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except (OSError, ValueError):
        return None
    return out if target == out else None


def _make(path):
    """The thumbnail as a Pillow image, or None; in the reader process."""
    kind = kind_of(path)
    box = (SIZE, SIZE)
    if kind == "photo":
        return pictures.load(path, box=box)
    if kind == "video":
        dur = read_info(path, "video").get("duration")
        work = tempfile.mkdtemp(prefix="haltia-kit-")  # 0700, a name no one guesses
        frame = os.path.join(work, "frame.png")
        try:
            for at in (min(3.0, dur / 3.0) if dur else 1.0, 0.0):
                subprocess.run(
                    ["ffmpeg", "-nostdin", "-v", "error", "-y"] + SAFE +
                    ["-ss", "%.2f" % at, "-i", "file:" + path, "-frames:v", "1", "-vf",
                     "scale=%d:%d:force_original_aspect_ratio=decrease" % (SIZE, SIZE), frame],
                    stdin=subprocess.DEVNULL, capture_output=True, timeout=FRAME_S,
                    preexec_fn=_uncap)
                if os.path.exists(frame) and os.path.getsize(frame) > 0:
                    return pictures.load(frame, box=box)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return None
    if kind == "audio":
        data = cover_bytes(path)
        if not data or len(data) > MAX_COVER:
            return None
        return pictures.load(data, box=box)
    return None


def __getattr__(name):
    """Thumbs, Viewer and VIEWER_CSS, from gallery.py when asked for."""
    if name in ("Thumbs", "Viewer", "VIEWER_CSS"):
        from . import gallery

        return getattr(gallery, name)
    raise AttributeError(name)
