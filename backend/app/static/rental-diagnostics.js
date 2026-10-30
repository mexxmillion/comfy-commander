(() => {
  const $=selector=>document.querySelector(selector),dialog=$('#rental-diagnostics');
  if(!dialog)return;
  let selected=null,report=null,updating=false;
  const acknowledged=new Set();
  try {for(const id of JSON.parse(localStorage.getItem('commander-rental-alerts')||'[]'))acknowledged.add(id);}catch(_){}
  window.syncRentalAlerts=()=>{
    const banner=$('#rental-attention');if(!banner)return;
    const rows=[...banner.querySelectorAll('[data-rental-alert]')];
    const pending=rows.filter(row=>!acknowledged.has(row.dataset.rentalAlert));
    for(const row of rows)row.hidden=row!==pending[0];
    banner.hidden=!pending.length;
    banner.querySelector('strong').textContent=`Rental / worker needs attention${pending.length>1?' · '+pending.length+' alerts':''}`;
  };
  document.addEventListener('click',event=>{
    const retry=event.target.closest('[data-resume-rental]');
    if(retry){
      retry.disabled=true;retry.textContent='Resuming same GPU…';
      fetch(`/api/v1/cloud/resume/${encodeURIComponent(retry.dataset.resumeRental)}`,{method:'POST',credentials:'same-origin'})
        .then(async response=>{if(!response.ok)throw new Error((await response.json()).detail||'Retry unavailable');retry.textContent='Resume requested · deadline unchanged';})
        .catch(error=>{retry.textContent=error.message;retry.disabled=false;});
    }
    const button=event.target.closest('[data-rental-diagnostics]');
    if(button){selected=button.dataset.rentalDiagnostics;$('#rental-log-stage').value='all';report=null;$('#rental-diagnostics-title').textContent=`Rental ${selected}`;$('#rental-diagnostics-log').textContent='Loading retained diagnostics…';dialog.showModal();void refresh();}
    if(event.target.closest('#ack-rental-alerts')) {
      for(const row of $('#rental-attention').querySelectorAll('[data-rental-alert]'))acknowledged.add(row.dataset.rentalAlert);
      try{localStorage.setItem('commander-rental-alerts',JSON.stringify([...acknowledged].slice(-200)));}catch(_){}
      window.syncRentalAlerts();
    }
  });
  async function refresh() {
    if(!selected||updating)return;updating=true;
    try {
      const response=await fetch(`/api/v1/cloud/diagnostics/${encodeURIComponent(selected)}`,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
      if(!response.ok)throw new Error(response.status===401?'Sign in to view rental diagnostics.':'Diagnostics unavailable; the retained rental record was not removed.');
      report=await response.json();
      $('#rental-diagnostics-summary').textContent=`${report.provider} · ${report.profile} · ${report.status} · ${report.cleanup_status}${report.failed_stage?' · failed at '+report.failed_stage:''}${report.failure_message?'\n'+report.failure_message:''}`;
      const stage=$('#rental-log-stage').value;
      if(stage==='all')$('#rental-diagnostics-log').textContent=report.log;
      else {
        const log=await fetch(`/api/v1/cloud/logs/${stage}?rental_id=${encodeURIComponent(selected)}`,{credentials:'same-origin',cache:'no-store',signal:AbortSignal.timeout(8000)});
        $('#rental-diagnostics-log').textContent=log.ok?await log.text():log.status===404?'No output for this stage yet. The lifecycle record is retained.':'Could not load this stage log.';
      }
    }catch(error){$('#rental-diagnostics-summary').textContent=error.message+' Previous log output is kept.';}
    finally{updating=false;}
  }
  $('#close-rental-diagnostics').onclick=()=>dialog.close();
  $('#refresh-rental-diagnostics').onclick=refresh;
  $('#rental-log-stage').onchange=refresh;
  $('#download-rental-diagnostics').onclick=()=>{
    if(!report)return;
    const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=`${selected}-diagnostics.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  setInterval(()=>{if(dialog.open&&!document.hidden)void refresh();},4000);
  const navigation=$('#workspace-navigation-stack');
  if(navigation)new ResizeObserver(()=>document.documentElement.style.setProperty('--workspace-navigation-height',navigation.getBoundingClientRect().height+'px')).observe(navigation);
  window.syncRentalAlerts();
})();
