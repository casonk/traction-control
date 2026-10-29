import atexit
import os
import shutil
import stat
import tempfile
from pathlib import Path


def _private_tempdir() -> None:
    """Keep test temp files out of a group- or world-writable ancestor.

    The sidecar and quadlet checks refuse a working directory with a group- or
    world-writable ancestor, and the default temp directory (/tmp) is one. When it
    is, use a private directory under the user's cache instead, so a bare
    ``pytest`` does not depend on TMPDIR. ``tests/test_repository_visibility_ci.sh``
    does the same for the unittest runs.
    """
    if not os.stat(tempfile.gettempdir()).st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    base = cache / "traction-control-tests"
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = tempfile.mkdtemp(prefix="pytest.", dir=base)
    tempfile.tempdir = private
    os.environ["TMPDIR"] = private
    atexit.register(shutil.rmtree, private, ignore_errors=True)


_private_tempdir()
