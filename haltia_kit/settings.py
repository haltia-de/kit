# SPDX-License-Identifier: GPL-2.0
"""A program's settings: one ini file, ~/.config/haltia/<name>.conf (under
$XDG_CONFIG_HOME), section [name]. Yes/no are written as yes and no, as
the Haltia programs always have; a value that cannot be read gives the
default. Every set() writes the file at once, through a temporary file.

    s = Settings("music")
    s.get_bool("buzz", True)
    s.get("quality", "low")
    s.set("buzz", False)
"""
import configparser
import os

from gi.repository import GLib


def _move_old(old, new):
    """Until 3.0: the file from before the rename (~/.config/b8k) moves to
    the new place the first time, unless the new one is already there."""
    try:
        if os.path.exists(old) and not os.path.exists(new):
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.replace(old, new)
    except OSError:
        pass


class Settings:
    def __init__(self, name, folder=None):
        self.name = name
        base = folder or os.path.join(GLib.get_user_config_dir(), "haltia")
        self.path = os.path.join(base, name + ".conf")
        if folder is None:
            _move_old(os.path.join(GLib.get_user_config_dir(), "b8k", name + ".conf"),
                      self.path)

    def _read(self):
        conf = configparser.ConfigParser(interpolation=None)
        try:
            conf.read(self.path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            conf = configparser.ConfigParser(interpolation=None)
        return conf

    def get(self, key, default=None):
        return self._read().get(self.name, key, fallback=default)

    def get_bool(self, key, default=False):
        try:
            return self._read().getboolean(self.name, key, fallback=default)
        except ValueError:
            return default

    def set(self, key, value):
        """Write one value (True/False as yes/no); False when the file could
        not be written."""
        conf = self._read()
        if not conf.has_section(self.name):
            conf.add_section(self.name)
        if isinstance(value, bool):
            value = "yes" if value else "no"
        conf.set(self.name, key, str(value))
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path + ".new", "w", encoding="utf-8") as fh:
                conf.write(fh)
            os.replace(self.path + ".new", self.path)
        except OSError:
            return False
        return True
