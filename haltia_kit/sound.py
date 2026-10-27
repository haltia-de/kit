# SPDX-License-Identifier: GPL-2.0
"""Sound through libmpv (python-mpv), one thing at a time, with the
machine kept awake while it plays (Awake), not while paused.

    player = Player("Музыка", on_end=..., on_time=..., on_pause=..., on_error=...,
                    options={"user_agent": "..."})
    player.play(path_or_url, start=None)
    player.toggle() / pause() / resume() / seek(sec) / stop() / quit()
    player.position, player.duration, player.paused, player.playing

The callbacks come on the GTK side (post, GLib.idle_add unless given):
on_end() when what plays comes to its end, on_error(text) when it could
not be played or broke off, on_time(seconds) as it plays, on_pause(bool).
on_time comes at most TIME_STEP apart (mpv tells the time many times a
second, and each redraw costs on a slow tablet); the newest time is never
dropped: one held back comes at the end of the step, before on_end, and
the first after seek() or play() comes at once.
libmpv is set up for files and streams from anywhere: no playlists that
reach other files, no youtube-dl, no video, no terminal or key bindings.
options are further mpv options a program needs (a CDN that wants a
browser's user agent: {"user_agent": ...}); they cannot undo those.
Raises RuntimeError from play() when libmpv cannot be loaded.

tests: haltia_kit.testing.FakePlayer has the same interface.
"""
import threading
import time

from gi.repository import GLib

from .awake import Awake

TIME_STEP = 0.5  # seconds: on_time at most twice a second


class Player:
    def __init__(self, who, on_end=None, on_time=None, on_pause=None, on_error=None,
                 post=None, awake=None, ytdl=False, options=None):
        self.who = who
        self.on_end, self.on_time = on_end, on_time
        self.on_pause, self.on_error = on_pause, on_error
        self.post = post or GLib.idle_add
        self.awake = awake or Awake(who, "plays sound")
        self.ytdl = ytdl
        self.options = dict(options or {})
        for locked in ("video", "ytdl", "load_unsafe_playlists", "input_default_bindings",
                       "terminal"):
            self.options.pop(locked, None)
        self.mpv = None
        self.playing = False
        self.paused = False
        self.time_step = TIME_STEP
        self._time_lock = threading.Lock()
        self._time_value = None  # the newest time not yet delivered
        self._time_next = 0.0  # monotonic: no on_time before this
        self._time_due = False  # a delivery is on its way

    def _call(self, fn, *args):
        if fn:
            self.post(lambda: (fn(*args), False)[1])

    def _player(self):
        if self.mpv is None:
            try:
                import mpv
            except (ImportError, OSError) as exc:
                raise RuntimeError("libmpv: %s" % exc)
            self.mpv = mpv.MPV(video=False, ytdl=self.ytdl, load_unsafe_playlists=False,
                               input_default_bindings=False, terminal=False, **self.options)
            self.mpv.observe_property("time-pos", self._time)
            self.mpv.observe_property("pause", self._pause)
            self.mpv.event_callback("end-file")(self._end)
        return self.mpv

    def _time(self, _name, value):
        """mpv's thread: the newest time kept, a delivery started unless one
        is on its way (it takes the newest)."""
        if value is None:
            return
        with self._time_lock:
            self._time_value = value
            if self._time_due:
                return
            self._time_due = True
            wait = self._time_next - time.monotonic()
        if wait <= 0:
            self.post(self._time_deliver)
        else:
            GLib.timeout_add(max(1, int(wait * 1000)),
                             lambda: (self.post(self._time_deliver), False)[1])

    def _time_deliver(self):
        with self._time_lock:
            value, self._time_value = self._time_value, None
            self._time_due = False
            if value is not None:
                self._time_next = time.monotonic() + self.time_step
        if value is not None and self.on_time:
            self.on_time(value)
        return False

    def _time_reset(self, keep):
        """Forget the step: the next time comes at once. keep: deliver the
        one held back now, else drop it."""
        if keep:
            self._time_deliver()
        with self._time_lock:
            self._time_value = None
            self._time_next = 0.0
            self._time_due = False  # one on its way finds nothing, or the newest

    def _pause(self, _name, value):
        self.post(lambda: (self._paused(bool(value)), False)[1])

    def _paused(self, value):
        self.paused = value
        self._awake()
        if self.on_pause:
            self.on_pause(value)

    def _end(self, event):
        reason = str(getattr(getattr(event, "data", None), "reason", ""))
        if reason in ("0", "eof", "b'eof'") or reason.lower().endswith("eof"):
            self.post(lambda: (self._ended(None), False)[1])
        elif reason in ("4", "error", "b'error'") or reason.lower().endswith("error"):
            self.post(lambda: (self._ended("не удалось проиграть"), False)[1])

    def _ended(self, error):
        self._time_reset(True)  # the last time shows before the end
        self.playing = False
        self._awake()
        if error is None:
            if self.on_end:
                self.on_end()
        elif self.on_error:
            self.on_error(error)

    def _awake(self):
        if self.playing and not self.paused:
            self.awake.hold()
        else:
            self.awake.release()

    def play(self, what, start=None):
        player = self._player()
        if start:
            player.loadfile(what, start=str(start))
        else:
            player.play(what)
        player.pause = False
        self._time_reset(False)
        self.playing, self.paused = True, False
        self._awake()

    def toggle(self):
        if self.mpv is not None and self.playing:
            self.mpv.pause = not self.paused

    def pause(self):
        if self.mpv is not None and self.playing:
            self.mpv.pause = True

    def resume(self):
        if self.mpv is not None and self.playing:
            self.mpv.pause = False

    def seek(self, sec):
        if self.mpv is not None and self.playing:
            self._time_reset(False)  # the time after the seek, at once
            try:
                self.mpv.seek(max(0, sec), reference="absolute")
            except Exception:
                pass

    @property
    def position(self):
        try:
            return self.mpv.time_pos if self.mpv is not None else None
        except Exception:
            return None

    @property
    def duration(self):
        try:
            return self.mpv.duration if self.mpv is not None else None
        except Exception:
            return None

    def stop(self):
        if self.mpv is not None:
            try:
                self.mpv.stop()
            except Exception:
                pass
        self._time_reset(False)
        self.playing = False
        self._awake()

    def quit(self):
        self.stop()
        if self.mpv is not None:
            try:
                self.mpv.terminate()
            except Exception:
                pass
            self.mpv = None
