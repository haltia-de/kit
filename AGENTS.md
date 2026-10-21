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
  `photos`, `pictures`, `gallery`, `camera`, `formats`, `paths`, `vault`,
  `worker`, `testing`); `README.md`
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
`camera.available()` says whether there is a camera at all, so a program
can hide its camera button; it blocks while it asks the devices (a second
or more with libcamera) and keeps the answer for 30 seconds. Photos go to
the XDG pictures folder + `Камера`, as `IMG_YYYYMMDD_HHMMSS.jpg`, unmirrored
even from a front camera (only the viewfinder mirrors). The tests use
`videotestsrc` in place of a camera.

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
