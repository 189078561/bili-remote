/* Read-only overview of real files, downloads and task artifacts. No publication drafts. */
(() => {
  const $=id=>document.getElementById(id);
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const status={queued:'排队中',running:'进行中',completed:'已完成',failed:'失败',cancelled:'已取消',auth_required:'需要登录'};
  const types={v2_media_probe:'媒体探测',v2_media_report:'媒体报告',torrent_build:'种子制作',screenshot:'截图',screenshot_collage:'拼图',image_upload:'图床上传',v2_rename_apply:'重命名',v2_rename_undo:'撤销重命名'};
  let pending,results=[],list='recent';
  function action(label,run){const b=node('button',label,'button ghost small');b.type='button';b.onclick=async()=>{b.disabled=true;try{await run();}catch(e){AppNotice.show(e.message,true);}finally{b.disabled=false;}};return b;}
  function icon(name,cls='home-resource-icon') {const holder=node('span',null,cls),svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');svg.setAttribute('class','ws-icon');svg.setAttribute('aria-hidden','true');use.setAttribute('href',`/static/v2/shared/icons.svg#${name}`);svg.append(use);holder.append(svg);return holder;}
  function renderResources() {
    const host=$('home-resources');host.replaceChildren();
    const result=results[list==='recent'?0:2];
    if(!result)return;
    if(result.status==='rejected'){host.append(node('p',`资源读取失败：${result.reason.message}`,'home-state home-error'));return;}
    const items=list==='recent'?(result.value.items||[]).filter(x=>x.is_dir||x.kind==='video').slice(0,6):result.value.filter(x=>x.status==='completed').slice(0,6);
    if(!items.length){host.append(node('p',list==='recent'?'还没有可处理的视频或目录。打开文件管理浏览资源，或添加一次下载。':'暂无已完成的下载。完成后可以在这里打开资源。','home-state'));return;}
    for(const item of items){
      const download=list==='downloads',row=node('article',null,'home-resource'),copy=node('div',null,'home-resource-copy');
      const date=download?(item.finished_at||item.created_at):item.mtime_ns?new Date(Number(item.mtime_ns)/1e6).toLocaleDateString('zh-CN'):'';
      copy.append(node('strong',download?(item.title||`下载 #${item.id}`):item.name),node('small',download?`下载完成${date?' · '+date:''}`:[item.is_dir?'目录':formatBytes(item.size_bytes),date].filter(Boolean).join(' · ')));
      const actions=node('div',null,'home-resource-actions');
      actions.append(action('进入工作台',()=>download?AppResources.openDownload(item):AppResources.openWorkbench([{...item,root_key:'downloads'}])));
      const open=action('打开文件',()=>download?AppResources.openDownload(item,'files'):AppResources.openFiles({root_key:'downloads',relative_path:item.relative_path,kind:item.is_dir?'directory':'file'}));
      open.classList.add('home-open-file');actions.append(open);row.append(icon(item.is_dir?'folder':'film'),copy,actions);host.append(row);
    }
  }
  function renderTasks(result) {
    const host=$('home-tasks'),artifacts=$('home-artifacts');host.replaceChildren();artifacts.replaceChildren();
    if(result.status==='rejected'){host.append(node('p',`任务读取失败：${result.reason.message}`,'home-state home-error'));artifacts.append(node('p','产物暂不可用','home-state'));return;}
    const tasks=result.value.items||[],active=tasks.filter(t=>['queued','running','failed','auth_required'].includes(t.status)).slice(0,5);
    if(!active.length)host.append(node('p','当前没有进行中或需要处理的任务。','home-state'));
    for(const task of active){
      const row=node('article',null,'home-task'),copy=node('div',null,'home-task-copy');row.dataset.status=task.status;
      const title=node('div',null,'home-task-title');title.append(node('strong',task.title||`${task.kind==='download'?'下载':types[task.task_type]||'媒体任务'} #${task.id}`),node('small',status[task.status]||task.status));copy.append(title);
      if(task.status==='running'){const progress=node('progress');progress.max=100;progress.setAttribute('aria-label','任务进度');if(task.progress!=null)progress.value=Math.min(100,Math.max(0,task.progress));copy.append(progress);if(task.progress!=null)copy.append(node('small',`${Math.round(task.progress)}%`));}
      if(task.error)copy.append(node('p',task.error,'home-error'));
      const actions=node('div',null,'home-task-actions');
      if(task.retry_allowed)actions.append(action('重试',async()=>{await api(`/api/v2/tasks/${task.kind}/${task.id}/retry`,{method:'POST'});await load();}));
      actions.append(action('查看任务',()=>document.querySelector('#task-center-toggle').click()));row.append(copy,actions);host.append(row);
    }
    const seen=new Set();
    for(const task of tasks.filter(t=>t.status==='completed')) for(const artifact of task.artifact_refs||[]) {
      const key=`${artifact.kind}:${artifact.id}`;if(seen.has(key)||seen.size>=5)continue;
      let url;try{url=new URL(artifact.url,location.href);if(!artifact.url||!['http:','https:'].includes(url.protocol)||url.username||url.password)continue;}catch{continue;}
      seen.add(key);const row=node('a',null,'home-artifact');row.href=url.href;row.target='_blank';row.rel='noopener noreferrer';
      const copy=node('span');copy.append(node('strong',artifact.resource?.relative_path?.split('/').at(-1)||({report:'媒体报告',torrent:'种子文件',screenshot:'截图',collage:'缩略图',upload:'图床链接'})[artifact.kind]||'工具产物'),node('small',`任务 #${task.id}`));row.append(icon(['screenshot','collage','upload'].includes(artifact.kind)?'image':'file','home-artifact-icon'),copy);artifacts.append(row);
    }
    if(!seen.size)artifacts.append(node('p','还没有可用的工具产物。','home-state'));
  }
  async function load(){
    if(pending)return pending;
    if(!$('home-resources'))return;
    const refresh=$('home-refresh');refresh.disabled=true;
    pending=(async()=>{
      results=await Promise.allSettled([api('/api/files/browse?root_key=downloads&page_size=30&sort=mtime&direction=desc'),api('/api/v2/tasks?limit=100'),api('/api/jobs?limit=100')]);
      renderResources();renderTasks(results[1]);
      $('home-system-status').textContent=results.every(x=>x.status==='fulfilled')?'目录与任务服务可用':'部分状态暂不可用，请重试';
      $('home-updated').textContent=`更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    })().finally(()=>{pending=null;refresh.disabled=false;});return pending;
  }
  document.querySelectorAll('[data-home-list]').forEach(button=>button.onclick=()=>{list=button.dataset.homeList;document.querySelectorAll('[data-home-list]').forEach(tab=>tab.setAttribute('aria-selected',String(tab===button)));renderResources();});
  document.addEventListener('app:tasks',event=>{
    const {kind,items}=event.detail||{};
    if(['processing','download'].includes(kind) && results[1]?.status==='fulfilled'){
      const retained=(results[1].value.items||[]).filter(task=>task.kind!==kind);
      results[1]={status:'fulfilled',value:{items:[...items.map(task=>({...task,kind})),...retained].sort((a,b)=>String(b.updated_at||b.created_at||'').localeCompare(String(a.updated_at||a.created_at||'')))}};
      renderTasks(results[1]);
    }
  });
  window.V2Home={load};
})();
