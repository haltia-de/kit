#!/bin/sh
# SPDX-License-Identifier: GPL-2.0
# Install haltia-kit (and its old name b8k_compat, until 3.0) into a root file system: sh install.sh [ROOT]
# (ROOT empty or / for this system), into the system Python's
# site-packages. Elsewhere `pip install .` does the same.
set -eu
root=${1:-}
root=${root%/}
here=$(cd "$(dirname "$0")" && pwd)
# The site-packages of the Python in ROOT (an image's may differ from the
# Python running this); else this Python's.
site=
for d in "$root"/usr/lib/python3.*/site-packages; do
	[ -d "$d" ] && site=${d#"$root"}
done
[ -n "$site" ] || site=$(python3 -c 'import sysconfig; print(sysconfig.get_path("purelib", vars={"base": "/usr"}))')
for pkg in haltia_kit b8k_compat; do
	rm -rf "$root$site/$pkg"
	for f in "$here"/$pkg/*.py; do
		install -D -m 644 "$f" "$root$site/$pkg/${f##*/}"
	done
done
