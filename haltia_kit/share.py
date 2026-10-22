# SPDX-License-Identifier: GPL-2.0
"""A file sent to another program that takes it.

    targets(path_or_mime)    the programs that take such a file -> a list of
                             Gio.DesktopAppInfo, by name
    send(path, parent=None)  the dialog «Отправить»: a big button per program,
                             a tap launches it with the file -> the dialog

A program takes files when its .desktop file says so:

    X-Haltia-Share=true
    MimeType=image/jpeg;image/png;   (a whole kind as image/*, too)
    Exec=program %U                  (or %u, %F, %f)

The program calling is never among them: its desktop id is its application
id (Gio.Application.get_default()). The receiver is launched as its Exec
line with the file's file:// URI (Gio.DesktopAppInfo.launch_uris), and gets
it as its argument, or in a GApplication with HANDLES_OPEN as open(). The
file may be in the sender's own folder: a receiver that keeps it copies it.

send() never waits for the answer: the dialog stays open on the GTK loop
and does its work when tapped. With a single program it still asks, so the
person sees where the file goes; with none it says «Отправить некуда».
"""
import os

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import dialogs  # noqa: E402

KEY = "X-Haltia-Share"
SNIFF = 4096  # bytes read to tell the kind of a file whose name does not


def _own():
    """The desktop id of the program calling, or None."""
    app = Gio.Application.get_default()
    name = app.get_application_id() if app is not None else None
    return name + ".desktop" if name else None


def _mime(path_or_mime):
    """A path (absolute, or one that exists) -> its MIME type; anything else
    is taken as a MIME type already."""
    if not (os.path.isabs(path_or_mime) or os.path.exists(path_or_mime)):
        return path_or_mime
    data = None
    try:
        with open(path_or_mime, "rb") as fh:
            data = fh.read(SNIFF)
    except OSError:
        pass
    kind = Gio.content_type_guess(path_or_mime, data)[0]
    return Gio.content_type_get_mime_type(kind) or kind


def _takes(mime, wanted):
    if wanted.endswith("/*"):
        return mime.startswith(wanted[:-1])
    return Gio.content_type_is_a(mime, wanted)


def targets(path_or_mime):
    mime = _mime(path_or_mime)
    own = _own()
    found = []
    for info in Gio.AppInfo.get_all():
        if not isinstance(info, Gio.DesktopAppInfo) or info.get_id() == own:
            continue
        if not info.get_boolean(KEY):
            continue
        if any(_takes(mime, wanted) for wanted in info.get_supported_types() or []):
            found.append(info)
    return sorted(found, key=lambda info: info.get_name().casefold())


def launch(info, path):
    """Starts info with the file -> True, or False when it would not start."""
    display = Gdk.Display.get_default()
    context = display.get_app_launch_context() if display is not None else None
    try:
        return info.launch_uris([Gio.File.new_for_path(path).get_uri()], context)
    except GLib.Error:
        return False


def _note(parent, message):
    """dialogs.info that does not wait for the answer."""
    dialog, _box = dialogs._dialog(parent, message)
    dialog.add_button("Понятно", Gtk.ResponseType.OK)
    dialog.connect("response", lambda d, _r: d.destroy())
    dialog.show_all()
    return dialog


def _button(info):
    row = Gtk.Box(spacing=16)
    icon = info.get_icon()
    if icon is not None:
        row.pack_start(Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DIALOG), False, False, 0)
    row.pack_start(Gtk.Label(label=info.get_name(), xalign=0), True, True, 0)
    btn = Gtk.Button()
    btn.add(row)
    dialogs._classes(btn, "choice")
    return btn


def send(path, parent=None):
    found = targets(path)
    if not found:
        return _note(parent, "Отправить некуда")
    dialog, box = dialogs._dialog(parent, "Отправить")

    def tapped(_b, info):
        dialog.destroy()
        if not launch(info, path):
            _note(parent, "Не удалось открыть «%s»." % info.get_name())

    for info in found:
        btn = _button(info)
        btn.connect("clicked", tapped, info)
        box.pack_start(btn, False, False, 0)
    dialog.add_button(dialogs.CANCEL, Gtk.ResponseType.CANCEL)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.get_widget_for_response(Gtk.ResponseType.CANCEL).grab_focus()
    # Only Отмена and Escape answer: a tapped program destroys it itself.
    dialog.connect("response", lambda d, _r: d.destroy())
    dialog.show_all()
    return dialog
