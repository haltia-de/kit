# SPDX-License-Identifier: GPL-2.0
"""Slow work off the GTK thread, its result back on it.

    from haltia_kit.background import background

    background(fetch, apply)                  # fetch() in a thread, apply(result) in the loop
    background(fetch, apply, latest="wifi")   # only the newest "wifi" result applied
    background(fetch, apply, fallback="")     # apply("") when fetch raises
    background(fetch, apply, serial=True)     # queued: one thread for all of these

fetch runs commands, reads files and asks D-Bus; it never touches GTK.
apply(result) then runs in the default GLib main context through
GLib.idle_add, where GTK may be used. The window stays live meanwhile: a
command, a file on a slow card or a D-Bus call without an answer no longer
freezes it.

fallback: what apply gets when fetch raises (otherwise apply is not called
then, and the error goes to stderr). latest: a key; of several calls with
the same key, only the newest call's result is applied, so a slow old read
never undoes a newer one (a call whose fetch raised without a fallback
still counts as the newest). serial: the call waits in one queue served by
a single thread, for many small reads at once (a list of lines that each
run a command), which would otherwise all start together. apply=None runs
fetch for its effect only.

Call it from the main thread. Modelled on haltia-settings' own helper in
the shell.
"""
import sys
import threading

from gi.repository import GLib

_lock = threading.Lock()
_latest = {}  # latest=KEY: the number of the newest call with that key
_queue = []  # serial=True: the jobs waiting for the one worker
_draining = [False]


def background(fetch, apply=None, fallback=None, latest=None, serial=False):
    """fetch() in a worker thread, then apply(its result) in the main loop."""
    token = None
    if latest is not None:
        with _lock:
            token = _latest[latest] = _latest.get(latest, 0) + 1

    def done(result):
        if latest is not None:
            with _lock:
                if _latest.get(latest) != token:
                    return False  # a newer call with this key: its result counts
        apply(result)
        return False

    def work():
        try:
            result = fetch()
        except Exception as exc:  # noqa: BLE001 - what cannot be had is the fallback
            sys.stderr.write("haltia-kit background: %s: %s\n" % (type(exc).__name__, exc))
            if fallback is None:
                return
            result = fallback
        if apply is not None:
            GLib.idle_add(done, result)

    if not serial:
        threading.Thread(target=work, daemon=True).start()
        return
    with _lock:
        _queue.append(work)
        if _draining[0]:
            return  # the worker running takes it
        _draining[0] = True
    threading.Thread(target=_drain, daemon=True).start()


def _drain():
    # One worker at a time, the queue and its flag under one lock: a job
    # queued just as the worker took the last one would otherwise start a
    # second worker, so one job ran twice and another was lost.
    while True:
        with _lock:
            if not _queue:
                _draining[0] = False
                return
            job = _queue.pop(0)
        job()
