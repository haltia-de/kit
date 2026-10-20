# SPDX-License-Identifier: GPL-2.0
"""Stand-ins for programs' tests.

FakePlayer has Player's interface without libmpv or a sound card; the
test drives it: tick(seconds), finish(), fail(text), and reads calls
(a list of (name, args)) and what (the last thing played). Callbacks are
called at once, not posted, so a test needs no main loop.

FakeInhibit is an inhibit for Awake: it hands out a real pipe fd per hold
and counts how many are held now (held).

FakeKeyring is a keyring for Vault: kept is its dict of name: text,
answers=False plays a machine without a Secret Service, and locked=True
a keyring whose unlock the person refuses (every call raises Locked)
until the test sets it back.
"""
import os


class FakePlayer:
    def __init__(self, who="test", on_end=None, on_time=None, on_pause=None, on_error=None,
                 **_ignored):
        self.who = who
        self.on_end, self.on_time = on_end, on_time
        self.on_pause, self.on_error = on_pause, on_error
        self.calls = []
        self.what = None
        self.position = None
        self.duration = None
        self.playing = False
        self.paused = False

    def play(self, what, start=None):
        self.calls.append(("play", (what, start)))
        self.what, self.position = what, start or 0
        self.playing, self.paused = True, False

    def toggle(self):
        self.calls.append(("toggle", ()))
        if self.playing:
            self._set_pause(not self.paused)

    def pause(self):
        self.calls.append(("pause", ()))
        if self.playing:
            self._set_pause(True)

    def resume(self):
        self.calls.append(("resume", ()))
        if self.playing:
            self._set_pause(False)

    def seek(self, sec):
        self.calls.append(("seek", (sec,)))
        if self.playing:
            self.position = max(0, sec)

    def stop(self):
        self.calls.append(("stop", ()))
        self.playing = False

    def quit(self):
        self.calls.append(("quit", ()))
        self.playing = False

    def _set_pause(self, value):
        self.paused = value
        if self.on_pause:
            self.on_pause(value)

    # driven by the test
    def tick(self, seconds):
        self.position = seconds
        if self.on_time:
            self.on_time(seconds)

    def finish(self):
        self.playing = False
        if self.on_end:
            self.on_end()

    def fail(self, text="не удалось проиграть"):
        self.playing = False
        if self.on_error:
            self.on_error(text)


class FakeInhibit:
    def __init__(self):
        self.fds = []  # (fd, inode) handed out

    def __call__(self, what, who, why, mode):
        r, w = os.pipe()
        os.close(w)
        self.fds.append((r, os.fstat(r).st_ino))
        return r

    @property
    def held(self):
        """How many handed out are still open (an fd number reused by
        something else does not count)."""
        n = 0
        for fd, ino in self.fds:
            try:
                n += os.fstat(fd).st_ino == ino
            except OSError:
                pass
        return n


class FakeKeyring:
    def __init__(self, answers=True, locked=False):
        self.on, self.locked = answers, locked
        self.kept = {}

    def answers(self):
        return self.on

    def _open(self):
        if self.locked:
            from .vault import Locked
            raise Locked()

    def lookup(self, name):
        self._open()
        return self.kept.get(name)

    def store(self, name, text):
        self._open()
        self.kept[name] = text

    def clear(self, name):
        self._open()
        self.kept.pop(name, None)
