"""Publish a finished temporary file under its final name without replacing another file."""

import os


def publish_new(source, target):
    """Give source the name target; an existing target raises FileExistsError.

    A hard link is atomic and never overwrites. FAT32, exFAT, cloud drives and some
    network shares have no hard links, so there the file is renamed instead. Windows
    refuses to rename onto an existing file; elsewhere only the check below guards it.
    Afterwards source may or may not still exist.
    """
    try:
        os.link(source, target)
    except FileExistsError:
        raise
    except OSError:
        if os.path.lexists(target):
            raise FileExistsError(target) from None
        os.rename(source, target)
