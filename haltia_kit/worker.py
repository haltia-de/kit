# SPDX-License-Identifier: GPL-2.0
"""The file reader in a process of its own: what parses files that came
from anywhere (pictures through Pillow, tags and covers through mutagen,
video through ffmpeg) runs here, with its memory capped and no display
(it loads no Gtk), so a file made to exhaust or crash a parser costs a
restart of this process, not the program.

    python3 -m haltia_kit.worker    reads one JSON request per line on
                                    stdin, answers one JSON line on stdout:
        {"op": "thumb", "path": p}            -> {"thumb": path or null}
        {"op": "info", "path": p, "kind": k}  -> {"info": {...}}
        {"op": "picture", "path": p, "size": s, "box": [w, h], "out": f}
            -> {"picture": {"width", "height", "alpha"}}, the pixels in f
               (pictures.write)

The program side is Worker: call(request) -> the answer, or None when the
reader died, broke the protocol or took longer than TIMEOUT_S (it is
killed and started again for the next request). One request at a time.
The reader runs in a process group of its own, which its ffmpeg and
ffprobe join: killing it kills the whole group, nothing goes on decoding.
A request never needs longer than TIMEOUT_S (photos.PROBE_S, FRAME_S).
"""
import json
import os
import select
import signal
import subprocess
import sys
import threading

MEMORY = 400 * 1024 * 1024  # bytes of address space for the reader's own parsing
CEILING = 1536 * 1024 * 1024  # the hard limit, for ffmpeg and ffprobe too
TIMEOUT_S = 60


class Worker:
    def __init__(self, timeout=TIMEOUT_S):
        self.timeout = timeout
        self.proc = None
        self.lock = threading.Lock()

    def _start(self):
        env = dict(os.environ)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = here + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH")
                                    else "")
        for name in ("WAYLAND_DISPLAY", "DISPLAY"):  # nothing here reaches the display
            env.pop(name, None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "haltia_kit.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, cwd="/", close_fds=True, text=True, bufsize=1,
            start_new_session=True)  # its group: the reader and its children

    def _kill(self):
        if self.proc is not None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)  # ffmpeg and ffprobe too
            except OSError:
                pass  # the group is gone already
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
            self.proc = None

    def call(self, request):
        with self.lock:
            for _attempt in range(2):  # a reader that died meanwhile: once more
                if self.proc is None or self.proc.poll() is not None:
                    self._kill()
                    try:
                        self._start()
                    except OSError:
                        return None
                try:
                    self.proc.stdin.write(json.dumps(request) + "\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    self._kill()
                    continue
                ready, _w, _x = select.select([self.proc.stdout], [], [], self.timeout)
                if not ready:
                    self._kill()  # stuck on this file: not tried again
                    return None
                line = self.proc.stdout.readline()
                if not line:
                    self._kill()
                    return None  # died on this file
                try:
                    return json.loads(line)
                except ValueError:
                    self._kill()
                    return None
            return None

    def close(self):
        with self.lock:
            self._kill()


def serve():
    try:
        import resource

        # A hard ceiling nothing here can lift, and a lower soft limit for
        # the reader's own parsing; ffmpeg and ffprobe, which need more
        # address space, raise the soft one to the ceiling (photos._uncap).
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, CEILING))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, ValueError, OSError):
        pass
    from haltia_kit import photos, pictures

    for line in sys.stdin:
        try:
            req = json.loads(line)
            if req.get("op") == "thumb":
                answer = {"thumb": photos.make_thumbnail(req["path"])}
            elif req.get("op") == "info":
                answer = {"info": photos.read_info(req["path"], req.get("kind"))}
            elif req.get("op") == "picture":
                answer = {"picture": pictures.write(req["path"], req["out"], req.get("size"),
                                                    req.get("box"))}
            else:
                answer = {"error": "unknown op"}
        except MemoryError:
            answer = {"error": "memory"}
        except Exception as exc:  # one bad file: an answer, the reader goes on
            answer = {"error": type(exc).__name__}
        sys.stdout.write(json.dumps(answer) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    serve()
