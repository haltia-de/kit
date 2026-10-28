# SPDX-License-Identifier: GPL-2.0
"""haltia-kit: what the Haltia programs share, for GTK 3 on any Linux desktop.

    settings  Settings(name): ~/.config/haltia/<name>.conf, section [name]
    dialogs   ask, choose, text, info: questions inside the window
    awake     Awake(who, why): keep the machine from sleeping (logind);
              hold_async(done) does not block the GTK thread
    sound     Player: sound through libmpv, awake while it plays
    photos    kind_of, thumbnail (freedesktop thumbnails), pixbuf_of, square,
              Thumbs (small pictures loaded in a thread), Viewer (photos one
              at a time)
    pictures  pictures decoded with Pillow in the reader process, never
              larger than needed
    gallery   Thumbs and Viewer, the GTK side of photos
    camera    take_photo(parent), available(), Viewfinder, capture(): the
              camera through GStreamer (libcamera, else V4L2)
    crash     install(app_id, version, log): uncaught exceptions and
              faulthandler kept in $XDG_STATE_HOME/haltia/crashes for
              haltia-report (done on import, under the command's name)
    share     targets(path_or_mime), send(path, parent): a file sent to a
              program whose .desktop says X-Haltia-Share=true
    formats   plural, nice_size, nice_duration, nice_date, month (Russian)
    background  background(fetch, apply, latest=, fallback=, serial=): fetch
              in a thread, apply(result) in the GTK main loop
    testing   FakePlayer, FakeInhibit, FakeKeyring for programs' own tests
    worker    the reader process the photos module parses files in
    paths     migrate_dirs(old, new): move a program's own folders to its
              new name once
    vault     Vault(app, folder): secrets in the Secret Service (libsecret),
              a 0600 file where none answers, Locked when refused

It knows nothing of any shell: a desktop (GNOME, KDE, the Haltia shell)
gives the look through its GTK theme and meets the program through the
freedesktop standards (.desktop files, logind, Unity LauncherEntry, the
Secret Service).
"""
__version__ = "2.4.0"

from . import crash  # noqa: E402

crash.install()  # every program importing kit: its crashes kept for haltia-report
