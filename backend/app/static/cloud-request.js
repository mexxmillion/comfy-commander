/* Keep uncertain paid-action outcomes distinct from rejected requests. */
(() => {
  async function request(path, options={}, fetcher=fetch) {
    const launch=path==='launch';
    let response;
    try { response=await fetcher(`/api/v1/cloud/${path}`,{credentials:'same-origin',...options}); }
    catch {
      const error=new Error(launch
        ? 'Rental response was lost. GPUs may already be starting or billable. Check Dispatcher → Rentals and your provider console before renting again. No automatic retry was sent.'
        : 'Cannot reach Commander. Check the server connection and try again.');
      error.uncertain=launch;throw error;
    }
    let data;
    try {
      data=await response.json();
      if(!data || typeof data!=='object' || Array.isArray(data))throw new Error('Invalid response object');
      if(launch && response.ok && (!Array.isArray(data.rentals) || !data.rentals.length))throw new Error('Missing rental receipts');
    }
    catch {
      const error=new Error(`Commander returned an unreadable response (HTTP ${response.status}).${launch?' Check Rentals and the provider console before retrying; allocation may have started.':''}`);
      error.status=response.status;error.uncertain=launch;throw error;
    }
    if(!response.ok) {
      const detail=typeof data.detail==='string'?data.detail:Array.isArray(data.detail)?data.detail.map(item=>`${item.loc?.slice(1).join('.') || 'Request'}: ${item.msg}`).join('; '):'Cloud request failed';
      const error=new Error(`${detail} (HTTP ${response.status})`);
      error.status=response.status;error.uncertain=launch && response.status>=500 && data.allocation_started!==false;
      if(error.uncertain)error.message+=' Check Rentals and the provider console before retrying; some capacity may have started.';
      throw error;
    }
    return data;
  }
  globalThis.CommanderCloudRequest={request};
})();
