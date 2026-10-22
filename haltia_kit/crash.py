# SPDX-License-Identifier: GPL-2.0
"""Crashes of a program, kept for haltia-report: $XDG_STATE_HOME/haltia/crashes/.

    crash.install("io.github.haltia_de.Max", version="1.4.0", log=path)

Importing haltia_kit already calls install() with the program's name from
its command (sys.argv[0]); a program calls it again with its id once it
knows it, and with its version and its own log when it has one.

- An exception nobody caught, in the main thread (sys.excepthook, which
  PyGObject also calls for an exception in a GTK callback) or another one
  (threading.excepthook), leaves a record <app>-<time>.json, written
  atomically: app, version, time, pid, thread, the exception, its full
  traceback (with the exceptions it came from), the Python and kit
  versions, and the last LOG_LINES lines of the log. The hook that was
  there before still runs (the traceback still goes to stderr). Ctrl+C
  is not a crash.
- faulthandler writes to <app>-<time>-<pid>.fault, a file of this run: a
  first line "# haltia-crash app=... version=... pid=..." and, after a
  segfault or an abort, the traceback of every thread. A run that ends
  removes its file when nothing was added; one that was killed leaves just
  the line, and the next install removes it. A hang is not watched
  (faulthandler.dump_traceback_later is not enabled).
- GLib warnings and criticals are not crashes and are not kept.
- Only the KEEP newest crashes stay in the folder.

HALTIA_CRASH=no in the environment turns it off (the reader process of
worker.py has it: a file that crashes a parser is not the program's crash).
"""
import atexit
import datetime
import faulthandler
import json
import os
import sys
import threading
import traceback

KEEP = 20
LOG_LINES = 200
FOLDER = os.path.join("haltia", "crashes")

_lock = threading.Lock()
_state = {"app": None, "version": None, "log": None, "folder": None, "fault": None,
          "hooks": False}


def folder():
    """The folder of the records."""
    try:
        from gi.repository import GLib
        base = GLib.get_user_state_dir()
    except (ImportError, AttributeError):  # no PyGObject, or GLib before 2.72
        base = os.environ.get("XDG_STATE_HOME") or \
            os.path.join(os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, FOLDER)


def _name(app):
    """A name safe for a file name."""
    return "".join(c if c.isalnum() or c in "._" else "-" for c in app) or "python"


def _own_name():
    command = os.path.basename(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if not command or command.startswith("-"):  # python3 -c, python3 -m
        return "python"
    return command[:-3] if command.endswith(".py") else command


def _stamp(now):
    return now.strftime("%Y%m%dT%H%M%S.%f")


def install(app_id=None, version=None, log=None):
    """Keep this program's crashes as app_id (else the command's name), with
    its version and the tail of the log file log. Calling it again changes
    them. False when it is off (HALTIA_CRASH=no) or the folder cannot be
    made."""
    if os.environ.get("HALTIA_CRASH", "").lower() in ("no", "0", "false", "off"):
        return False
    with _lock:
        app = app_id or _state["app"] or _own_name()
        if version is None and app == _state["app"]:
            version = _state["version"]
        if log is None and app == _state["app"]:
            log = _state["log"]
        place = folder()
        try:
            os.makedirs(place, exist_ok=True)
        except OSError:
            return False
        _state.update(app=app, version=version, log=log, folder=place)
        _fault(app, version, place)
        if not _state["hooks"]:
            _state["hooks"] = True
            before, before_thread = sys.excepthook, threading.excepthook

            def hook(kind, value, tb):
                if not issubclass(kind, KeyboardInterrupt):
                    record(value, tb)
                before(kind, value, tb)

            def thread_hook(args):
                if not issubclass(args.exc_type, SystemExit):
                    record(args.exc_value, args.exc_traceback, args.thread)
                before_thread(args)

            sys.excepthook, threading.excepthook = hook, thread_hook
            atexit.register(_end)
        _prune(place)
    return True


def _header(app, version):
    return "# haltia-crash app=%s version=%s pid=%d\n" % (app, version or "", os.getpid())


def _fault(app, version, place):
    """faulthandler into a new file of this run, the old one removed."""
    old = _state["fault"]
    path = os.path.join(place, "%s-%s-%d.fault" % (
        _name(app), _stamp(datetime.datetime.now()), os.getpid()))
    try:
        fh = open(path, "w", encoding="utf-8")
    except OSError:
        return
    try:
        fh.write(_header(app, version))
        fh.flush()
    except OSError:
        fh.close()
        _drop(path)
        return
    faulthandler.enable(fh, all_threads=True)
    _state["fault"] = fh
    if old is not None:
        old.close()
        _drop(old.name)


def _drop(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _end():
    """At exit: this run's file goes when nothing was written after its line."""
    fh = _state["fault"]
    if fh is None:
        return
    faulthandler.disable()
    _state["fault"] = None
    fh.close()
    if _only_header(fh.name):
        _drop(fh.name)


def _only_header(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            first, rest = fh.readline(), fh.read()
    except OSError:
        return False
    return first.startswith("# haltia-crash ") and not rest.strip()


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        pass
    return True


def _prune(place):
    """Fault files of runs that ended without a crash go; of the crashes,
    the KEEP newest stay."""
    own = _state["fault"].name if _state["fault"] is not None else None
    crashes = []
    try:
        names = os.listdir(place)
    except OSError:
        return
    for name in names:
        path = os.path.join(place, name)
        if path == own:
            continue
        if name.endswith(".fault"):
            try:
                pid = int(name[:-len(".fault")].rsplit("-", 1)[1])
            except (IndexError, ValueError):
                pid = 0
            if pid and _alive(pid):
                continue
            if _only_header(path):
                _drop(path)
                continue
        elif not name.endswith(".json"):
            continue
        try:
            crashes.append((os.stat(path).st_mtime, name, path))
        except OSError:
            pass
    crashes.sort(reverse=True)
    for _mtime, _n, path in crashes[KEEP:]:
        _drop(path)


def _tail(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    return "".join(lines[-LOG_LINES:])


def record(error, tb=None, thread=None):
    """Write the record of error (an exception) now; its path, or None. The
    hooks call it; a program may call it for an error it caught but cannot
    go on after."""
    try:
        from . import __version__ as kit
        now = datetime.datetime.now().astimezone()
        app = _state["app"] or _own_name()
        place = _state["folder"] or folder()
        thread = thread or threading.current_thread()
        if tb is None:
            tb = error.__traceback__
        data = {
            "app": app,
            "version": _state["version"],
            "time": now.isoformat(timespec="seconds"),
            "pid": os.getpid(),
            "thread": thread.name if thread is not None else None,
            "exception": "".join(traceback.format_exception_only(type(error), error)).strip(),
            "traceback": "".join(traceback.format_exception(type(error), error, tb)),
            "argv": list(sys.argv),
            "python": sys.version.split()[0],
            "kit": kit,
            "log": _state["log"],
            "log_tail": _tail(_state["log"]),
        }
        os.makedirs(place, exist_ok=True)
        path = os.path.join(place, "%s-%s.json" % (_name(app), _stamp(now)))
        part = os.path.join(place, ".%s.part" % os.path.basename(path))
        with open(part, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(part, path)
        _prune(place)
        return path
    except Exception:  # a crash record never makes the crash worse
        return None
