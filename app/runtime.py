"""Platform paths and a kernel-held, per-data-directory instance lock."""
import errno
import os
from pathlib import Path
import sys


def default_data_directory(project):
    if sys.platform.startswith("linux"):
        base = Path(os.environ.get("XDG_DATA_HOME", ""))
        if not base.is_absolute():
            base = Path.home() / ".local/share"
        return base / "KeyTrail"
    return (Path(sys.executable).parent / "Puzzle71Desk-data"
            if getattr(sys, "frozen", False) else Path(project) / ".local-data")


def fallback_data_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Puzzle71Desk"
    return default_data_directory(Path.home())


class InstanceLock:
    def __init__(self, directory):
        self.path = Path(directory) / "instance.lock"
        self.stream = None

    def acquire(self):
        self.stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(self.stream.fileno()).st_size == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError as error:
            self.close()
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            return False

    def close(self):
        if self.stream:
            # Closing releases both flock and Windows byte-range locks. Never
            # unlink the lock file: that would allow two owners of two inodes.
            self.stream.close()
            self.stream = None
