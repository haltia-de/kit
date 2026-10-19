# SPDX-License-Identifier: GPL-2.0
"""A program's own folders under a new name (ADR 0004: b8k-<x> became
haltia-<x>). Call once at start, before anything opens them:

    paths.migrate_dirs("b8k-max", "haltia-max")

For each of $XDG_CONFIG_HOME, $XDG_DATA_HOME and $XDG_CACHE_HOME: when the
old folder exists and the new one does not, the old one is renamed to the
new name and a link with the old name is left pointing at it, so a part
still using the old path (an old version, a script) finds the same files.
Nothing happens when the new folder is already there. Returns the list of
folders moved.
"""
import os

from gi.repository import GLib

_BASES = (GLib.get_user_config_dir, GLib.get_user_data_dir, GLib.get_user_cache_dir)


def migrate_dirs(old, new, bases=None):
    moved = []
    for base in bases or [b() for b in _BASES]:
        src = os.path.join(base, old)
        dst = os.path.join(base, new)
        try:
            if os.path.isdir(src) and not os.path.islink(src) and not os.path.lexists(dst):
                os.replace(src, dst)
                os.symlink(new, src)
                moved.append(dst)
        except OSError:
            pass
    return moved
