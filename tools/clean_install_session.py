"""Prepare a separate, credential-isolated clone for a recorded first install.

Run clone, install and start separately so each step can be recorded. This
helper never stops another installation, submits a render or rents a GPU.
"""
import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys

REPOSITORY = 'https://github.com/mexxmillion/comfy-commander.git'
PREFIXES = ('TENSOR_', 'COMFY_', 'COMFYUI_', 'COMMANDER_', 'OPENROUTER_', 'HF_', 'HUGGINGFACE_')
OVERRIDES = {
    'DATABASE_URL', 'TOKEN_FILE', 'CLOUD_SSH_KEY_FILE', 'CLOUD_RUNTIME_SOURCE', 'CLOUD_OUTPUT_DIR',
    'MODEL_DIRECTORIES', 'VAST_API_KEY', 'VAST_API_KEY_FILE', 'RUNPOD_API_KEY',
    'RUNPOD_API_KEY_FILE', 'PREFERENCES_FILE', 'RENTAL_PREFERENCES_FILE',
    'COMMUNITY_LORA_MANIFEST', 'APP_ENV', 'APP_SECRET_KEY', 'PYTHONPATH',
    'PYTHONHOME', 'VIRTUAL_ENV', 'PIP_INDEX_URL', 'PIP_EXTRA_INDEX_URL',
    'MAX_GPU_PRICE_PER_HOUR', 'MAX_TOTAL_PRICE_PER_HOUR', 'MAX_ACTIVE_GPUS',
    'MAX_INSTANCE_LIFETIME_HOURS', 'MAX_DOWNLOAD_COST_PER_GB',
    'MIN_CREDIT_GPU_HOURS', 'IDLE_DESTROY_MINUTES', 'UNRESPONSIVE_DESTROY_MINUTES',
}


def isolated_environment(app, source=None):
    env = {key: value for key, value in (os.environ if source is None else source).items()
           if not key.upper().startswith(PREFIXES) and key.upper() not in OVERRIDES}
    credentials = app / 'state' / 'credentials'
    env.update(TENSOR_MASTER_CREDENTIAL_DIR=str(credentials),
               TOKEN_FILE=str(credentials / 'api_token'),
               CLOUD_SSH_KEY_FILE=str(credentials / 'cloud-worker-ed25519'),
               OPENROUTER_SECRET_FILE=str(credentials / 'openrouter_api_key'),
               HF_HOME=str(app / 'state' / 'huggingface-cache'),
               HF_HUB_DISABLE_IMPLICIT_TOKEN='1', PYTHONNOUSERSITE='1', PYTHONUTF8='1',
               PIP_INDEX_URL='https://pypi.org/simple')
    return env


def require_checkout(app):
    if not (app / '.git').is_dir() or not (app / 'pyproject.toml').is_file():
        raise RuntimeError('Clone the repository first; this is not a Commander checkout.')
    result = subprocess.run(['git', 'config', '--get', 'remote.origin.url'], cwd=app,
                            capture_output=True, text=True, check=True)
    if result.stdout.strip().removesuffix('.git') != REPOSITORY.removesuffix('.git'):
        raise RuntimeError('This helper only operates on the public Commander recording clone.')


def clone(workspace, env):
    app = workspace / 'app'
    if app.exists():
        raise RuntimeError('The app folder already exists. Choose another recording workspace; nothing was replaced.')
    workspace.mkdir(parents=True, exist_ok=True)
    print(f'git clone --branch main --single-branch {REPOSITORY} app', flush=True)
    subprocess.run(['git', 'clone', '--branch', 'main', '--single-branch', REPOSITORY, 'app'],
                   cwd=workspace, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('status', 'clone', 'install', 'start', 'worker'))
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    app = workspace / 'app'
    env = isolated_environment(app)
    if not 1024 <= args.port <= 65535:
        parser.error('Choose a port from 1024 to 65535.')
    try:
        if args.action == 'status':
            for name, present in [('checkout', (app / '.git').exists()),
                                  ('virtual environment', (app / '.venv').exists()),
                                  ('saved state', (app / 'state').exists()),
                                  ('configuration', (app / '.env').exists())]:
                print(f'{name}: {"present" if present else "absent"}')
            print('No application state or credentials were created by this check.')
            return 0
        if args.action == 'clone':
            clone(workspace, env)
            return 0
        require_checkout(app)
        python = app / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if args.action == 'install':
            if not python.exists():
                subprocess.run([sys.executable, '-m', 'venv', str(app / '.venv')], env=env, check=True)
            subprocess.run([str(python), '-m', 'pip', 'install', '-e', '.'], cwd=app, env=env, check=True)
            return 0
        if not python.is_file():
            raise RuntimeError('Install the dependencies first.')
        if args.action == 'worker':
            if args.port != 8000:
                raise RuntimeError('The simple local-worker launcher targets port 8000. Use a deliberate normal-port cutover.')
            subprocess.run([str(python), '-m', 'backend.local_worker_start'], cwd=app, env=env, check=True)
            return 0
        try:
            with socket.create_connection(('127.0.0.1', args.port), timeout=.5):
                raise RuntimeError('That port is already in use. Stop the previous controller when safe, '
                                   'or choose another port. This helper will not stop it or open the wrong installation.')
        except (ConnectionRefusedError, TimeoutError):
            pass
        print('Starting this clone with installation-owned credentials. Existing installations are unchanged.')
        print(f'Open http://127.0.0.1:{args.port}/setup in the new recording browser profile.')
        subprocess.run([str(python), '-m', 'backend.service_start', '--no-browser', '--port', str(args.port)],
                       cwd=app, env=env, check=True)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
