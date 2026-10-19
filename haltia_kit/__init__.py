# SPDX-License-Identifier: GPL-2.0
"""haltia-kit: what the Haltia programs share, for GTK 3 on any Linux desktop.

    settings  Settings(name): ~/.config/haltia/<name>.conf, section [name]
    dialogs   ask, choose, text, info: questions inside the window
    awake     Awake(who, why): keep the machine from sleeping (logind)
    sound     Player: sound through libmpv, awake while it plays
    photos    kind_of, thumbnail (freedesktop thumbnails), pixbuf_of, square,
              Thumbs (small pictures loaded in a thread), Viewer (photos one
              at a time)
    formats   plural, nice_size, nice_duration, nice_date, month (Russian)
    testing   FakePlayer, FakeInhibit for programs' own tests
    worker    the reader process the photos module parses files in
    paths     migrate_dirs(old, new): move a program's own folders to its
              new name once

It knows nothing of any shell: a desktop (GNOME, KDE, the Haltia shell)
gives the look through its GTK theme and meets the program through the
freedesktop standards (.desktop files, logind, Unity LauncherEntry).
"""
__version__ = "2.1.0"
