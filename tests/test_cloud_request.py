import shutil
import subprocess
from pathlib import Path
import pytest


def test_cloud_errors_distinguish_uncertain_allocation_from_rejection():
    node=shutil.which('node')
    if not node:pytest.skip('Node.js required for request policy regression')
    source=Path(__file__).resolve().parents[1]/'backend/app/static/cloud-request.js'
    script=r'''
require(process.argv[1]);
const request=globalThis.CommanderCloudRequest.request;
(async()=>{
 let calls=0;
 const lost=async()=>{calls++;throw new TypeError('Failed to fetch');};
 try{await request('launch',{},lost);throw Error('missing rejection');}catch(e){if(!e.uncertain||!e.message.includes('billable'))throw e;}
 if(calls!==1)throw Error('Paid action retried');
 const response=(status,detail)=>async()=>({ok:status<400,status,json:async()=>({detail})});
 try{await request('launch',{},response(422,[{loc:['body','count'],msg:'must be positive'}]));throw Error('missing rejection');}catch(e){if(e.uncertain||!e.message.includes('count: must be positive'))throw e;}
 try{await request('launch',{},response(503,'unavailable'));throw Error('missing rejection');}catch(e){if(!e.uncertain||!e.message.includes('provider console'))throw e;}
 try{await request('launch',{},async()=>({ok:false,status:503,json:async()=>({detail:'Manager did not start',allocation_started:false})}));throw Error('missing rejection');}catch(e){if(e.uncertain||!e.message.includes('Manager did not start'))throw e;}
 try{await request('launch',{},async()=>({status:502,json:async()=>{throw Error('HTML');}}));throw Error('missing rejection');}catch(e){if(!e.uncertain||!e.message.includes('HTTP 502'))throw e;}
 for(const body of [null,{},[],{status:'requested',rentals:[]}]) {
  try{await request('launch',{},async()=>({ok:true,status:202,json:async()=>body}));throw Error('missing rejection');}catch(e){if(!e.uncertain)throw e;}
 }
 try{await request('offers',{},lost);throw Error('missing rejection');}catch(e){if(e.uncertain||!e.message.includes('server connection'))throw e;}
 const partial=await request('launch',{},async()=>({ok:true,status:202,json:async()=>({status:'partial',rentals:['rental-1']})}));
 if(partial.rentals[0]!=='rental-1')throw Error('Receipt lost');
})().catch(error=>{console.error(error);process.exit(1);});
'''
    subprocess.run([node,'-e',script,str(source)],check=True,capture_output=True,text=True)
