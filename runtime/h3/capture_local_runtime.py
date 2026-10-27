"""Capture the working local H3 runtime without copying credentials or environments."""
import argparse
import json
import shutil
import subprocess
from pathlib import Path

PACKAGES = ('torch', 'torchvision', 'torchaudio', 'transformers', 'huggingface-hub',
            'comfy-kitchen', 'comfy-aimdo', 'numpy', 'safetensors', 'accelerate', 'tokenizers')


def capture(comfy_root: Path, python: Path, destination: Path):
    revision = subprocess.check_output(['git', '-C', str(comfy_root), 'rev-parse', 'HEAD'], text=True).strip()
    query = 'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ' + repr(PACKAGES) + '}))'
    packages = json.loads(subprocess.check_output([str(python), '-c', query], text=True))
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'local-runtime.json').write_text(json.dumps({'comfyui_revision': revision, 'packages': packages}, indent=2)+'\n')
    (destination/'local-runtime-constraints.txt').write_text(''.join(f'{name}=={version}\n' for name, version in packages.items()))
    # Include local Python core additions/edits used by experimental attention nodes.
    changed = subprocess.check_output(['git','-C',str(comfy_root),'diff','HEAD','--name-only','--','comfy','comfy_extras'],text=True).splitlines()
    added = subprocess.check_output(['git','-C',str(comfy_root),'ls-files','--others','--exclude-standard','--','comfy','comfy_extras'],text=True).splitlines()
    source = destination.parent/'comfy-source'
    source.mkdir(parents=True, exist_ok=True)
    overrides=[]
    for name in sorted(set(changed+added)):
        relative=Path(name)
        if relative.suffix != '.py' or '..' in relative.parts:
            continue
        original=comfy_root/relative
        if not original.is_file():
            raise RuntimeError(f'Cannot package deleted core file: {name}')
        target=source/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(original,target)
        overrides.append(name)
    print(json.dumps({'comfyui_revision':revision,'packages':packages,'core_overrides':overrides}))


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--comfy-root',type=Path,required=True)
    parser.add_argument('--python',type=Path,required=True)
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args()
    capture(args.comfy_root,args.python,args.destination)
