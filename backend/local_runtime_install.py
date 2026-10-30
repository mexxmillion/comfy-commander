"""Run one reviewed local software installation independently of the web UI."""
from backend.app import local_runtime as runtime
from backend.process_lock import process_lock


def main():
    with process_lock(runtime.operation_file().with_suffix('.lock')) as owned:
        if not owned:
            return 1
        # Do not use read_operation here: the worker may start before its parent
        # has published the PID. The request contains no credentials or commands.
        import json
        import os
        data = json.loads(runtime.operation_file().read_text(encoding='utf-8'))
        runtime.STATE.clear()
        runtime.STATE.update(data)
        runtime.STATE['pid'] = os.getpid()
        runtime.prepare(runtime.RuntimeSelection(destination=data['destination'], model_set=data['model_set']))
        return 0 if runtime.STATE['status'] == 'prepared' else 1


if __name__ == '__main__':
    raise SystemExit(main())
