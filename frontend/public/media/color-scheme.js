/* Display-only bridge; only the embedding window can select a supported scheme. */
(()=>{
  const apply=value=>{
    if(!['light','dark','grey'].includes(value))return;
    document.documentElement.dataset.colorScheme=value;
    document.documentElement.style.colorScheme=value==='light'?'light':'dark';
    document.documentElement.classList.toggle('dark',value!=='light');
  };
  addEventListener('message',event=>{
    if(event.source!==parent||!event.data||typeof event.data!=='object')return;
    const d=event.data;
    if(['sterling.radio.hydrate','sterling.tv.hydrate'].includes(d.type))apply(d.appearance);
    if(['sterling.radio.command','sterling.tv.command'].includes(d.type)&&d.action==='appearance')apply(d.value);
  });
})();
