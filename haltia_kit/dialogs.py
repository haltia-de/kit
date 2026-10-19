# SPDX-License-Identifier: GPL-2.0
"""Questions inside the program's window, as GTK dialogs: they open at
once and take the desktop's look (the Haltia shell's GTK theme makes them
large). Each waits for the answer (a nested main loop) and returns it.

    ask(parent, "Удалить «кот.png»?", "Удалить")  -> True / False
    choose(parent, "Как показывать", ["Плитки", "Список"], current=0)
                                                 -> an index / None
    text(parent, "Новое имя", "кот.png")         -> the text / None
    info(parent, "Не удалось удалить.")

Отмена is always there and is what Enter or Escape gives, so a stray key
or tap does no harm. Dialog widgets carry the CSS class "haltia-dialog",
the title "haltia-title"; choices are buttons of class "haltia-choice",
the current one also "current". Until 3.0 each also carries the old b8k-
class, which the shell's theme before 2.0 styles.
"""
import gi

# GTK 4 is often installed too: without this gi may load it.
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

CANCEL = "Отмена"



def _classes(widget, name):
    ctx = widget.get_style_context()
    ctx.add_class("haltia-" + name)
    ctx.add_class("b8k-" + name)  # the shell's theme before 2.0

def _dialog(parent, title):
    dialog = Gtk.Dialog(transient_for=parent, modal=True, destroy_with_parent=True)
    _classes(dialog, "dialog")
    # The question is the label below, large; a title bar would repeat it.
    box = dialog.get_content_area()
    box.set_spacing(12)
    box.set_border_width(16)
    head = Gtk.Label(label=title, wrap=True, max_width_chars=40)
    _classes(head, "title")
    box.pack_start(head, False, False, 0)
    return dialog, box


def ask(parent, question, yes, no=CANCEL):
    dialog, _box = _dialog(parent, question)
    dialog.add_button(yes, Gtk.ResponseType.OK)
    dialog.add_button(no, Gtk.ResponseType.CANCEL)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.get_widget_for_response(Gtk.ResponseType.CANCEL).grab_focus()
    dialog.show_all()
    res = dialog.run()
    dialog.destroy()
    return res == Gtk.ResponseType.OK


def choose(parent, title, items, current=None):
    dialog, box = _dialog(parent, title)
    chosen = []
    for i, item in enumerate(items):
        btn = Gtk.Button(label=item)
        ctx = btn.get_style_context()
        ctx.add_class("haltia-choice")
        ctx.add_class("b8k-choice")
        if i == current:
            ctx.add_class("current")
        btn.connect("clicked", lambda _b, n=i: (chosen.append(n),
                                                dialog.response(Gtk.ResponseType.OK)))
        box.pack_start(btn, False, False, 0)
    dialog.add_button(CANCEL, Gtk.ResponseType.CANCEL)
    dialog.set_default_response(Gtk.ResponseType.CANCEL)
    dialog.get_widget_for_response(Gtk.ResponseType.CANCEL).grab_focus()
    dialog.show_all()
    res = dialog.run()
    dialog.destroy()
    return chosen[0] if res == Gtk.ResponseType.OK and chosen else None


def text(parent, title, value=""):
    dialog, box = _dialog(parent, title)
    entry = Gtk.Entry(text=value, activates_default=True)
    box.pack_start(entry, False, False, 0)
    dialog.add_button(CANCEL, Gtk.ResponseType.CANCEL)
    dialog.add_button("Готово", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    dialog.show_all()
    res = dialog.run()
    answer = entry.get_text().strip()
    dialog.destroy()
    return answer if res == Gtk.ResponseType.OK and answer else None


def info(parent, message):
    dialog, _box = _dialog(parent, message)
    dialog.add_button("Понятно", Gtk.ResponseType.OK)
    dialog.show_all()
    dialog.run()
    dialog.destroy()
