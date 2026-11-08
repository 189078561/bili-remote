/* Only appearance is persisted; never resource context or publication text. */
(() => {
  const modes = ['system','light','dark'], labels = {system:'跟随系统',light:'浅色',dark:'深色'};
  let preference = 'system';
  try { const saved = localStorage.getItem('bili-theme'); if(modes.includes(saved)) preference = saved; } catch {}
  const apply = () => {
    if(preference === 'system') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.dataset.theme = preference;
    const button=document.querySelector('#theme-toggle');
    if(button) {button.title=`主题：${labels[preference]}，点击切换`;button.setAttribute('aria-label',button.title);button.querySelector('span').textContent=labels[preference];}
  };
  apply();
  document.addEventListener('DOMContentLoaded',()=>{
    apply();
    document.querySelector('#theme-toggle')?.addEventListener('click',()=>{
      preference=modes[(modes.indexOf(preference)+1)%modes.length];
      try {localStorage.setItem('bili-theme',preference);} catch {}
      apply();
    });
  });
})();
