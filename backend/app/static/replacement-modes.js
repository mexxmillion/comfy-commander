/* Experimental scene-edit routing; targeted person replacement keeps SAM3. */
((root) => {
  const prompts = {
    all: 'Replace the person in <Video 1> with the character from <Picture 1>, preserving the character identity, hairstyle and clothing. Replace the entire original setting with the location from <Picture 2>. Use the video for movement, poses, timing and camera framing only. Match lighting, shadows and natural foot contact to the new location. Preserve the original soundtrack and keep one continuous shot. No text or extra people.'
  };
  const api = {
    prompts,
    swapStrength(value) {
      const strength=Number(value);
      if(value==='' || !Number.isFinite(strength) || strength<0 || strength>2)throw new Error('Character Swap strength must be between 0 and 2.');
      return strength;
    },
    mode: refs => refs.filter(r=>r.kind==='image').length>=2?'all':'person',
    replaceSlot(refs, slot, ref) {
      const images=refs.filter(r=>r.kind==='image'),videos=refs.filter(r=>r.kind==='video');
      if(slot==='location' && !images.length)throw new Error('Choose the character image before the optional location.');
      if(slot==='video')return [...images,ref];
      images[slot==='location'?1:0]=ref;
      return [...images,...videos];
    },
    jobType: mode => mode === 'person' ? 'replacement' : 'standard',
    validate(mode, refs) {
      const videos=refs.filter(r=>r.kind==='video').length,images=refs.filter(r=>r.kind==='image').length;
      if(videos!==1 || refs.length!==videos+images)throw new Error('Choose exactly one video and image references only.');
      if(mode==='person' && images!==1)throw new Error('Person replacement requires exactly one video and one character image.');
      if(mode==='all' && images!==2)throw new Error('Person + scene requires one video, Picture 1 for the character, and Picture 2 for the location.');
      if(!['person','all'].includes(mode))throw new Error('Unknown replacement mode.');
    }
  };
  root.CommanderReplacementModes=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
