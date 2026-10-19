# SPDX-License-Identifier: GPL-2.0
"""b8k_compat is haltia_kit under its name before ADR 0004, until 3.0:
`import b8k_compat.settings` and `from b8k_compat import dialogs` give the
haltia_kit modules themselves, loaded only when asked for (sound would
load libmpv)."""
import importlib
import importlib.abc
import importlib.util
import sys

import haltia_kit

__version__ = haltia_kit.__version__
__path__ = []  # a package, so b8k_compat.<module> is looked up below


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path, target=None):
        if fullname.startswith(__name__ + "."):
            return importlib.util.spec_from_loader(fullname, self)
        return None

    def create_module(self, spec):
        return importlib.import_module("haltia_kit." + spec.name.split(".", 1)[1])

    def exec_module(self, module):
        pass


sys.meta_path.insert(0, _Alias())


def __getattr__(name):
    try:
        return importlib.import_module("haltia_kit." + name)
    except ImportError:
        raise AttributeError(name) from None
