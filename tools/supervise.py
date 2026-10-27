"""Keep the local stack and existing farm connections alive after sign-in."""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.process_lock import process_lock
from runtime.h3.recover_farm import recoverable, rental_files


def resume_rentals():
    for path in rental_files():
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            if not recoverable(record):
                continue
            with process_lock(path.with_suffix('.manager.lock')) as available:
                if not available:
                    continue
            with path.with_suffix('.recovery.log').open('a', encoding='utf-8') as output:
                subprocess.Popen([sys.executable, str(ROOT / 'runtime/h3/recover_farm.py'), '--state-file', str(path)],
                                 cwd=ROOT, stdout=output, stderr=output,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except (OSError, ValueError) as error:
            print(f'Rental recovery state unavailable: {path.name}: {type(error).__name__}', flush=True)


def main():
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with process_lock(ROOT / 'state' / 'supervisor.lock') as owned:
        if not owned:
            return
        print('Service supervision and existing rental recovery running.', flush=True)
        while True:
            result = subprocess.run([sys.executable, str(ROOT / 'tools/service_check.py'), '--quiet'],
                                    cwd=ROOT, creationflags=flags, check=False)
            if result.returncode:
                print('Service disconnected; restoring the existing stack.', flush=True)
                subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                '-File', str(ROOT / 'tools/launch.ps1'), '-NoBrowser', '-NoSupervisor'],
                               cwd=ROOT, creationflags=flags, check=False)
            resume_rentals()
            time.sleep(10)


if __name__ == '__main__':
    main()
