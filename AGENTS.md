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
  `photos`, `formats`, `paths`, `vault`, `worker`, `testing`); `README.md`
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
