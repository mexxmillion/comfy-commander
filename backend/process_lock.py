"""Process ownership locks automatically release on crash or Windows reboot."""
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    stream.seek(0)
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b'0')
        stream.flush()
    stream.seek(0)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            pass
        yield acquired
    finally:
        stream.close()
