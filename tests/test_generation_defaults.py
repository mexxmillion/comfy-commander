"""Execute the shared browser canvas policy used by mode switches/draft restore."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_canvas_defaults_survive_mode_switches_and_stale_draft_settings():
    node=shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for the browser policy regression')
    source=Path(__file__).resolve().parents[1]/'backend/app/static/generation-defaults.js'
    script=r'''
require(process.argv[1]);
const policy=globalThis.CommanderGenerationDefaults;
const controls={
 '#aspect-ratio':{value:'16:9 (Widescreen)',options:[{value:'9:16 (Portrait Widescreen)'},{value:'16:9 (Widescreen)'}]},
 '#megapixels':{value:1},'#duration':{value:10},'#direction':{value:'Keep my prompt'}
};
const find=id=>controls[id];
for(const mode of ['h3','qwen','h3']) {
 policy.apply(find);
 for(const [id,old] of [['aspect-ratio','16:9 (Widescreen)'],['megapixels',1],['duration',10]]) {
  controls['#'+id].value=policy.restoreValue(id,controls['#'+id].value,old);
 }
 if(find('#aspect-ratio').value!=='9:16 (Portrait Widescreen)'||find('#megapixels').value!==0.5||find('#duration').value!==5)throw Error(mode+' leaked old canvas settings');
}
if(policy.restoreValue('direction','', 'Keep my draft prompt')!=='Keep my draft prompt')throw Error('Creative draft data was lost');
policy.apply(find,{aspect_ratio:'16:9',megapixels:0.8,duration:8});
if(find('#duration').value!==8)throw Error('Explicit saved defaults were ignored');
'''
    subprocess.run([node,'-e',script,str(source)],check=True,capture_output=True,text=True)
