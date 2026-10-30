"""Stop only this checkout's Commander web server and local worker bridges."""
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]


def owned_role(process):
    command = process.cmdline()
    if Path(process.cwd()).resolve() != ROOT.resolve():
        return None
    for index, value in enumerate(command[:-1]):
        if value != '-m':
            continue
        module = command[index + 1]
        arguments = command[index + 2:]
        if module == 'uvicorn' and arguments and arguments[0] == 'backend.app.main:app':
            return 'controller'
        if module == 'backend.worker' and '--local-gpu' in arguments:
            return 'local worker'
    return None


def stop():
    targets = []
    failed = False
    for process in psutil.process_iter():
        try:
            role = owned_role(process)
            if role:
                targets.append((process, role))
        except (psutil.Error, OSError):
            continue
    # Stop bridges first so they cannot claim another job during shutdown.
    targets.sort(key=lambda item: item[1] == 'controller')
    stopped = []
    for process, role in targets:
        try:
            process.terminate()
            stopped.append(process)
            print(f'Stopping {role} (PID {process.pid}).')
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            failed = True
            print(f'Could not stop {role} (PID {process.pid}); check process permissions.')
    _, alive = psutil.wait_procs(stopped, timeout=10)
    if alive:
        failed = True
        print('Some Commander processes are still stopping. Try again shortly.')
    elif not targets:
        print('No running Commander server or local worker found for this folder.')
    else:
        print('Commander server and local workers stopped for this folder.')
    print('ComfyUI, cloud workers, rental managers and deadline watchdogs were not stopped.')
    print('Provider billing continues until a rental is terminated. Saved files and credentials are unchanged.')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(stop())
