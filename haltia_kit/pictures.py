# SPDX-License-Identifier: GPL-2.0
"""Pictures decoded with Pillow, never larger than needed.

    load(src, size=None, box=None)  a Pillow image, turned upright: about
                                    size px on its short side, or fitted
                                    into box (w, h); never scaled up
    write(path, out, size, box)     load's pixels, row after row (RGB, or
                                    RGBA when it has transparency), into
                                    the file out; {"width", "height",
                                    "alpha"}
    save_png(im, out, text)         a PNG with tEXt chunks (thumbnails)
    png_text(png)                   a PNG's size and tEXt chunks, from its
                                    header only

load and write run in the reader process (worker.py, memory capped), the
program gets only the pixels: the GdkPixbuf loaders are not used there
(on some systems they go to glycin in bwrap, and one loader that hung froze
a program's GTK thread). src is a path or bytes (a cover). A picture over
MAX_PIXELS is refused from its header; a JPEG is decoded at the scale
needed (draft), not at full size. A file Pillow cannot read (SVG) goes to
GdkPixbuf, in the reader process too.
"""
import io

MAX_PIXELS = 60 * 1000 * 1000
TURNED = (5, 6, 7, 8)  # EXIF orientations that swap width and height


def _open(src):
    from PIL import Image

    im = Image.open(io.BytesIO(src) if isinstance(src, (bytes, bytearray)) else src)
    w, h = im.size
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        raise ValueError("picture too large or of unknown size")
    return im


def _scale(w, h, size, box):
    """The factor to size or box, at most 1."""
    k = 1.0
    if size:
        k = min(k, size / float(min(w, h)))
    if box:
        k = min(k, box[0] / float(w), box[1] / float(h))
    return k


def load(src, size=None, box=None):
    from PIL import Image, ImageOps

    try:
        im = _open(src)
    except Image.UnidentifiedImageError:
        return _by_pixbuf(src, size, box)
    w, h = im.size
    turned = im.getexif().get(0x0112) in TURNED
    if turned:
        w, h = h, w
    k = _scale(w, h, size, box)
    tw, th = max(1, int(w * k + 0.5)), max(1, int(h * k + 0.5))
    im.draft("RGB", (th, tw) if turned else (tw, th))  # a JPEG: decoded smaller
    im = ImageOps.exif_transpose(im)
    alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
    im = im.convert("RGBA" if alpha else "RGB")
    if im.size != (tw, th):
        im = im.resize((tw, th), Image.Resampling.LANCZOS)
    return im


def _by_pixbuf(src, size, box):
    """What Pillow does not read (SVG), through GdkPixbuf: here, in the
    reader process only."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    from PIL import Image

    if isinstance(src, (bytes, bytearray)):
        raise ValueError("not a picture")
    _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(src)  # -1 when it cannot tell
    if w <= 0 or h <= 0 or w * h > MAX_PIXELS:
        raise ValueError("picture too large or of unknown size")
    k = _scale(w, h, size, box)
    pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(
        src, max(1, int(w * k + 0.5)), max(1, int(h * k + 0.5)), True)
    pix = pix.apply_embedded_orientation() or pix
    mode = "RGBA" if pix.get_has_alpha() else "RGB"
    w, h, stride = pix.get_width(), pix.get_height(), pix.get_rowstride()
    data = pix.get_pixels()  # the last row without its padding
    return Image.frombytes(mode, (w, h), data + b"\0" * (stride * h - len(data)),
                           "raw", mode, stride)


def write(path, out, size=None, box=None):
    im = load(path, size, box)
    with open(out, "wb") as fh:
        fh.write(im.tobytes())
    return {"width": im.width, "height": im.height, "alpha": im.mode == "RGBA"}


def save_png(im, out, text):
    from PIL import PngImagePlugin

    info = PngImagePlugin.PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    im.save(out, "PNG", pnginfo=info)


def png_text(png):
    """(width, height, {key: text}) from the chunks before the pixels;
    raises OSError when it is not a PNG."""
    from PIL import Image

    with Image.open(png, formats=["PNG"]) as im:
        return im.width, im.height, {k: v for k, v in im.info.items() if isinstance(v, str)}
