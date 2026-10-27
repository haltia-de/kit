# kit

The shared library of the [Haltia](https://github.com/haltia-de) programs
for any Linux with GTK 3: questions inside the window, settings, staying
awake while something plays, sound, photos and secrets. It knows nothing
about the shell: the look comes from the desktop's GTK theme, the link to
the desktop from freedesktop standards. It is a library, not a program, so
it has no `io.github.haltia_de.<Program>` id and no metainfo.

Python module `haltia_kit`, distribution `haltia-kit`; packages
`py3-haltia-kit` (Alpine) and `python3-haltia-kit` (Debian, Ubuntu).
Before ADR 0004 it was `b8k_compat` (`py3-b8k-compat`); until 3.0 the old
module name stays as an alias and the packages replace the old one.

## Layout

- `haltia_kit/`: the modules (`settings`, `dialogs`, `awake`, `sound`,
  `photos`, `pictures`, `gallery`, `camera`, `share`, `formats`, `paths`, `vault`,
  `worker`, `crash`, `background`, `testing`); `README.md`
  has a table of what each one gives.
- `b8k_compat/`: the alias for the old module name, until 3.0.
- `tests/test_compat.py`: the tests.
- `install.sh`, `pyproject.toml`: installing from the source.
- `packaging/alpine/APKBUILD`, `debian/`, `packaging/flatpak/haltia-kit.yml`:
  the Alpine and Debian packages and the Flatpak module for programs.
- `.github/workflows/release.yml`: the release build.

## Use from the checkout

- Tests: `python3 tests/test_compat.py` (no display, libmpv or sound card
  needed).
- Install: `sh install.sh [ROOT]` into the system site-packages, or
  `pip install .`.
- Folders only through the GLib XDG functions: in Flatpak `~` is closed and
  settings and data live in `~/.var/app/<id>/`.

## A photo for a program

`haltia_kit.camera.take_photo(parent=None)` returns the path of the photo
the person kept, or `None` when they cancelled. It runs the Camera app
(`haltia-de/camera`, desktop id `io.github.haltia_de.Camera`, still to be
agreed there) as its `Exec` line with `--take-photo`. The contract the app
keeps:

- with `--take-photo` it runs as its own instance
  (`Gio.ApplicationFlags.NON_UNIQUE`), never forwarding to an instance
  already open (that would print nothing and read as a cancel); if the open
  one holds the camera, the new one shows the busy state;
- kept: the photo's absolute path as the last line of standard output, then
  exit 0 (earlier lines are ignored);
- cancelled: exit 0 without a path.

A non-zero exit (a crash, an older app without `--take-photo`), no app, or
Flatpak: a dialog with `camera.Viewfinder` in the process.

Release the camera when your window is not active: a program that shows a
`camera.Viewfinder` calls its `pause()` when the window's `is-active` turns
false and `resume()` when it turns true. A Wayland window left in the
background stays mapped, so without it the camera stays held and a video
call in another program finds it busy. `Viewfinder.capture(done)` always
calls `done` on the main thread: the path, or `None` with `.failure` the
`CameraError` (`BUSY`, `NONE`, `FAILED`); the picture comes back after it.
While the picture plays, the photo is its next frame (`Preview.snap`), at
the viewfinder's size, so its colours are the ones the person saw; only
when none plays does it run `capture()`. `capture()` starts the camera
itself, so it waits for libcamera's white balance and exposure to settle:
at least `SETTLE` (30) frames, then until the mean colour of the frames
stops changing (at most `SETTLE_MORE` frames more).
`camera.available()` says whether there is a camera at all, so a program
can hide its camera button; it blocks while it asks the devices (a second
or more with libcamera) and keeps the answer for 30 seconds. Photos go to
the XDG pictures folder + `Камера`, as `IMG_YYYYMMDD_HHMMSS.jpg`, unmirrored
even from a front camera (only the viewfinder mirrors). A libcamerasrc
gets libcamera's control Contrast, `camera.CONTRAST` (1.25, against the
milky soft ISP picture), where it has it as the property `contrast`;
`cameras(contrast=...)` or `Camera(..., contrast=...)` changes it, `None`
leaves the camera's own. The tests use
`videotestsrc` in place of a camera.

The picture turns with the screen. The shell writes
`$XDG_RUNTIME_DIR/haltia/rotation` (`camera.ROTATION_FILE`): `normal`, `90`,
`180` or `270`, how far the device is turned clockwise from its natural
position. A `videoflip` after the source in the `Viewfinder`, in
`capture()` and in the element of `Camera.make()` (a bin with the pad
`src`, made only where the file is or the sensor is mounted turned; the
source alone is `Camera.source()`) turns the picture clockwise by the
sensor's mounting (`Camera.mount`, libcamera's Rotation where the device
gives it) plus the screen's turn, minus it for a front camera (it looks
the other way). The file's folder is watched (the shell replaces the file
by a rename), and the turn follows it while the picture plays. Without the
file (a PC, Flatpak) there is no turn. A program's own size for the
picture goes in `Camera.make(caps=...)`, never after the element: a
capsfilter right after the source, before the `videoflip`, so libcamera
gives that size itself (its width and height swapped for the source when
turned 90 or 270 at `make()`); a size after the flip lets libcamera pick a
larger one.

## Sending a file to another program

`haltia_kit.share.send(path, parent=None)` opens the dialog «Отправить»:
one big button (icon and name) per program that takes the file, and
«Отмена», the default. It asks even with a single program, so the person
sees where the file goes; with none it says «Отправить некуда». It does
not wait: it returns the dialog, which does its work on the GTK loop.
`share.targets(path_or_mime)` gives those programs (`Gio.DesktopAppInfo`,
by name); a path is absolute or exists, anything else is a MIME type. The
contract a receiver keeps:

- its `.desktop` has `X-Haltia-Share=true`, a `MimeType=` with the types it
  takes (a whole kind as `image/*` too), and `%U`, `%u`, `%F` or `%f` in
  `Exec=`;
- it is launched with `Gio.DesktopAppInfo.launch_uris` and the file's
  `file://` URI, and gets the file as its argument (in a `GApplication`
  with `HANDLES_OPEN`, through `open`);
- the file may be in the sender's own folder: a receiver that keeps it
  copies it.

The program calling is never offered: its desktop id is its application id
(`Gio.Application.get_default()`).

## Staying awake from the GTK thread

`Awake.hold()` blocks: logind's `Manager.Inhibit` is asked on the system
bus synchronously, up to five seconds when the bus does not answer. On the
GTK thread call `Awake.hold_async(done=None)` instead: the bus and the call
are Gio's asynchronous ones, the fd comes out of the reply's fd list, and
`done(held)` runs later on the main context that was the thread's default
at the call, never from inside `hold_async`. Calls while a request is under
way join it. `release()` cancels a request under way: a late fd is closed
at once, and nothing a late answer brings ever takes the place of a release
or of a newer holder (a `hold()` that won meanwhile). A failure is reported
once, without retries; the next explicit hold asks again. The application
way (`Gtk.Application.inhibit`, synchronous in GTK 3) is still tried first;
`app=False` asks logind only. `logind_inhibit_async(what, who, why, mode,
done, cancellable=None, bus=None)` is the call underneath. The tests serve
a fake logind on a peer-to-peer D-Bus connection with real fds.

## Work off the GTK thread

`haltia_kit.background.background(fetch, apply=None, fallback=None,
latest=None, serial=False)` runs `fetch()` in a worker thread and
`apply(result)` in the default main context through `GLib.idle_add`. fetch
runs commands, reads files and asks D-Bus, never GTK; anything slower than
about 50 ms on the GTK thread is a freeze on the tablet. `fallback` is what
apply gets when fetch raises (without it apply is not called and the error
goes to stderr); `latest=KEY` applies only the newest call's result of
that key, so a slow old read never undoes a newer one; `serial=True` queues
the call for a single worker thread (many small reads at once). Call it
from the main thread. It is haltia-settings' helper (the shell), shared so
that programs stop writing their own `Thread` plus `idle_add`.

## Crashes

Importing `haltia_kit` calls `haltia_kit.crash.install()`, so every program
keeps its crashes for haltia-report (the shell's), which reads
`$XDG_STATE_HOME/haltia/crashes/` (`crash.folder()`). A program calls
`crash.install(app_id, version=..., log=...)` once it knows them: until
then the name is the command's (`sys.argv[0]`), the version unknown, no
log. In the folder:

- `<app>-<time>.json`, written atomically (a `.part` file renamed): an
  exception nobody caught, through `sys.excepthook` (PyGObject calls it for
  a GTK callback too, where the program may live on) or
  `threading.excepthook`. Keys: `app`, `version`, `time` (ISO 8601 with
  the zone), `pid`, `thread`, `exception` (`Type: message`), `traceback`
  (in full, with the exceptions it came from), `argv`, `python`, `kit`,
  `log` (its path) and `log_tail` (its last 200 lines, `crash.LOG_LINES`),
  `null` when unknown. The hook that was there before still runs;
  `KeyboardInterrupt` is not a crash. `crash.record(error)` writes one for
  an error a program caught but cannot go on after.
- `<app>-<time>-<pid>.fault`: faulthandler's file of one run, the first
  line `# haltia-crash app=<app> version=<version> pid=<pid>`, then the
  traceback of every thread after a segfault or an abort. A run that ends
  removes its file; a killed one leaves just that line, and the next start
  removes it. A file with more than that line is a crash. Hangs are not
  watched (`faulthandler.dump_traceback_later` is not enabled).
- Only the 20 newest crashes (`crash.KEEP`), json and fault, stay.

GLib warnings and criticals are not crashes. `HALTIA_CRASH=no` turns it
off; the reader process (`worker.py`) has it, as a file that crashes a
parser is not the program's crash.

## Release

A release needs the user's approval. Bump the version (SemVer) everywhere
it is written: `version` in `pyproject.toml`, `pkgver` in
`packaging/alpine/APKBUILD`, a new entry at the top of `debian/changelog`,
the `tag` and the example URL in `packaging/flatpak/haltia-kit.yml`. Write
the release notes in `NEWS.md`. The tag `vX.Y.Z` then runs
`.github/workflows/release.yml`, which builds the apk and deb packages with
the shared workflows of `haltia-de/ci`.

## Conventions

The project-wide decisions are in
[haltia-de/haltia](https://github.com/haltia-de/haltia) (`docs/adr/`), the
shared rules in
[docs/conventions.md](https://github.com/haltia-de/haltia/blob/main/docs/conventions.md).
Human documents are in Russian; code, comments and this file in English.
