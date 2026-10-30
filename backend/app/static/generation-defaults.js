(() => {
  const fields = new Set(['aspect-ratio', 'megapixels', 'duration']);
  const builtin = Object.freeze({aspect_ratio:'9:16',megapixels:0.5,duration:5});
  function apply(find, preferences=builtin) {
    const values={...builtin,...preferences};
    const aspect=find('#aspect-ratio');
    aspect.value=[...aspect.options].find(option=>option.value.split(' ')[0]===values.aspect_ratio)?.value || aspect.options[0].value;
    find('#megapixels').value=values.megapixels;
    find('#duration').value=values.duration;
  }
  // Automatic draft recovery keeps the creative work, but current defaults own
  // the initial canvas. Explicit Edit / rerun still restores exact job settings.
  function restoreValue(id,current,saved) {return fields.has(id)?current:saved;}
  globalThis.CommanderGenerationDefaults={builtin,apply,restoreValue};
})();
