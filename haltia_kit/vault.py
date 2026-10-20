# SPDX-License-Identifier: GPL-2.0
"""A program's secrets (tokens, cookies) in the Secret Service through
libsecret: gnome-keyring on GNOME and XFCE, KWallet on Plasma 6, the
Secret portal inside Flatpak (libsecret picks it there by itself). Where
none answers (the tablet has no keyring) they are files in the program's
own folder, readable by the owner only.

    vault = Vault("io.github.haltia_de.Messenger", folder, key="account")
    vault.state            None until the first use, then KEYRING, FILE,
                           or LOCKED (the last call was refused unlocking)
    vault.get(name)        the text kept under name, or None
    vault.put(name, text)
    vault.take(name)       the file of that name the person has just put
                           in place goes into the keyring (a new cookies
                           file); False when there is none
    vault.drop(name)
    vault.as_file(name)    a path holding the text, for yt-dlp and mpv,
                           or None
    vault.close()          what was changed in those copies goes back;
                           called at exit by itself

In the keyring a secret carries the attributes app=<app> and <key>=<name>
under the schema named <app>, and is found by <key> alone (so items kept
before app was added are found too). The file is folder/<file % name>
(file="%s" by default): it is the fallback, and a file found while the
keyring answers moves into it once.

A locked keyring is unlocked by the desktop's own prompt. When the person
refuses it, get, put, take and as_file raise Locked (and state is LOCKED)
instead of falling back to the file, so the program can say «Связка
ключей закрыта» and offer to try again, which asks again.

Whether a Secret Service answers is asked once, at the first use (~0.1 s
on the tablet when none does). keyring=None keeps files only; tests pass
that or a fake (haltia_kit.testing.FakeKeyring) and never touch the real
keyring. Thread-safe: programs call it from worker threads.
"""
import atexit
import os
import shutil
import tempfile
import threading

AUTO = "auto"
KEYRING, FILE, LOCKED = "keyring", "file", "locked"


class Locked(Exception):
    """The keyring is locked and the person did not unlock it."""


class Keyring:
    """The Secret Service through libsecret (gi), one string per name."""

    def __init__(self, app, key="name", label=None):
        import gi

        gi.require_version("Secret", "1")
        from gi.repository import GLib, Secret

        self.Secret = Secret
        self.Error = GLib.Error
        self.app, self.key = app, key
        self.label = label or app
        self.schema = Secret.Schema.new(app, Secret.SchemaFlags.NONE,
                                        {"app": Secret.SchemaAttributeType.STRING,
                                         key: Secret.SchemaAttributeType.STRING})

    def answers(self):
        try:
            self.Secret.password_search_sync(self.schema, {self.key: "-"},
                                             self.Secret.SearchFlags.NONE, None)
            return True
        except self.Error:  # no Secret Service on this bus
            return False

    def lookup(self, name):
        # Not password_lookup_sync: it gives None both for nothing kept and
        # for a refused unlock, and those must not look the same.
        flags = self.Secret.SearchFlags
        items = self.Secret.password_search_sync(
            self.schema, {self.key: name}, flags.UNLOCK | flags.LOAD_SECRETS, None)
        open_ = [i for i in items if not getattr(i, "get_locked", lambda: False)()]
        if items and not open_:
            raise Locked()
        for item in open_:
            value = item.retrieve_secret_sync(None)
            if value is not None:
                return value.get_text()
        return None

    def store(self, name, text):
        # One item per name: one kept before app was added would stay
        # beside the new one, and a lookup could give either.
        try:
            self.clear(name)
        except Locked:
            pass  # storing asks to unlock, and says if it was refused
        try:
            self.Secret.password_store_sync(
                self.schema, {"app": self.app, self.key: name},
                self.Secret.COLLECTION_DEFAULT, "%s: %s" % (self.label, name), text, None)
        except self.Error as e:
            if self._locked(e):
                raise Locked() from e
            raise

    def clear(self, name):
        try:
            self.Secret.password_clear_sync(self.schema, {self.key: name}, None)
        except self.Error as e:
            if self._locked(e):
                raise Locked() from e
            raise

    def _locked(self, error):
        """Whether error came of a refused unlock: libsecret's own word for
        it, or the default collection still locked after it."""
        if error.matches(self.Secret.error_get_quark(), self.Secret.Error.IS_LOCKED):
            return True
        try:
            service = self.Secret.Service.get_sync(self.Secret.ServiceFlags.NONE, None)
            default = self.Secret.Collection.for_alias_sync(
                service, "default", self.Secret.CollectionFlags.NONE, None)
            return default is not None and default.get_locked()
        except self.Error:  # the portal in Flatpak: no collections to ask
            return False


def _keyring(app, key, label):
    try:
        return Keyring(app, key, label)
    except (ImportError, ValueError):  # no libsecret (its typelib)
        return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _write(path, text):
    """Readable by the owner only, written whole or not at all."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".new"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Vault:
    def __init__(self, app, folder, key="name", file="%s", label=None,
                 keyring=AUTO, runtime_dir=None):
        """app: the program's id, the schema's name; folder: where the
        fallback files are; key: the attribute the name goes in; label:
        what the keyring's manager shows before the name (app by default);
        keyring: AUTO (libsecret, if it answers), None (files only, as in
        the tests), or anything with answers/lookup/store/clear."""
        self.app, self.dir, self.file = app, folder, file
        self.keyring = _keyring(app, key, label) if keyring == AUTO else keyring
        self.state = None if self.keyring else FILE
        self.runtime_dir = runtime_dir or os.environ.get("XDG_RUNTIME_DIR")
        self.lock = threading.Lock()
        self.kept = {}  # name: text (or None) as the keyring gave it
        self.copies = {}  # name: (path, text) written by as_file
        self.copy_dir = None

    def _path(self, name):
        return os.path.join(self.dir, self.file % name)

    def _on_keyring(self):
        if self.state is None:
            self.state = KEYRING if self.keyring.answers() else FILE
        return self.state != FILE

    def _ring(self, call, *args):
        """A keyring call; LOCKED while the person refuses to unlock."""
        try:
            result = call(*args)
        except Locked:
            self.state = LOCKED
            raise
        self.state = KEYRING
        return result

    def _get(self, name):
        if not self._on_keyring():
            return _read(self._path(name))
        if name not in self.kept:
            text = self._ring(self.keyring.lookup, name)
            if text is None:
                text = _read(self._path(name))
                if text is not None:  # a file of before the keyring, moved in once
                    self._ring(self.keyring.store, name, text)
                    _remove(self._path(name))
            self.kept[name] = text
        return self.kept[name]

    def get(self, name):
        with self.lock:
            return self._get(name)

    def put(self, name, text):
        with self.lock:
            if self._on_keyring():
                self._ring(self.keyring.store, name, text)
                self.kept[name] = text
                self._forget_copy(name)
            else:
                _write(self._path(name), text)

    def take(self, name):
        with self.lock:
            text = _read(self._path(name))
            if text is None:
                return False
            if self._on_keyring():
                self._ring(self.keyring.store, name, text)
                self.kept[name] = text
                self._forget_copy(name)
                _remove(self._path(name))
            return True

    def drop(self, name):
        """Forgets name everywhere; a locked keyring keeps its item (the
        person refused to open it) but the program's copy is gone."""
        with self.lock:
            if self._on_keyring():
                try:
                    self._ring(self.keyring.clear, name)
                except Exception:
                    pass
                self.kept[name] = None
                self._forget_copy(name)
            _remove(self._path(name))

    def as_file(self, name):
        with self.lock:
            if not self._on_keyring():
                path = self._path(name)
                return path if os.path.exists(path) else None
            text = self._get(name)
            if text is None:
                return None
            if name not in self.copies:
                if self.copy_dir is None:
                    # mkdtemp: a new folder only this user can enter.
                    self.copy_dir = tempfile.mkdtemp(prefix="haltia-vault-",
                                                     dir=self.runtime_dir or None)
                    atexit.register(self.close)
                path = os.path.join(self.copy_dir, self.file % name)
                _write(path, text)
                self.copies[name] = (path, text)
            return self.copies[name][0]

    def _forget_copy(self, name):
        path, _text = self.copies.pop(name, (None, None))
        if path:
            _remove(path)

    def close(self):
        """What yt-dlp changed in the copies back into the keyring; the
        copies removed."""
        with self.lock:
            for name, (path, text) in list(self.copies.items()):
                now = _read(path)
                if now and now != text:
                    try:
                        self.keyring.store(name, now)
                        self.kept[name] = now
                    except Exception:
                        pass
            self.copies = {}
            if self.copy_dir:
                shutil.rmtree(self.copy_dir, ignore_errors=True)
                self.copy_dir = None
