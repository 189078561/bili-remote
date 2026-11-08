/* Read-only workspace overview; all resource actions use the shared browser/context. */
(() => {
  const $=id=>document.getElementById(id);
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;};
  const status={queued:'排队中',running:'进行中',completed:'已完成',failed:'失败',cancelled:'已取消',auth_required:'需要登录'};
  let pending;
  function action(label,run){const b=node('button',label,'button secondary small');b.type='button';b.onclick=async()=>{b.disabled=true;try{await run();}catch(e){AppNotice.show(e.message,true);}finally{b.disabled=false;}};return b;}
  function renderResources(result) {
    const host=$('home-resources');host.replaceChildren();
    if(result.status==='rejected'){host.append(node('p',`目录读取失败：${result.reason.message}`,'home-state home-error'));return;}
    const items=(result.value.items||[]).filter(x=>x.is_dir||x.kind==='video').slice(0,6);
    if(!items.length){host.append(node('p','下载目录还没有可处理的视频或目录。可以开始下载，或在文件管理中浏览其他目录。','home-state'));return;}
    for(const item of items){
      const row=node('article',null,'home-resource'),copy=node('div',null,'home-resource-copy');
      copy.append(node('strong',item.name),node('small',item.is_dir?'目录 · 可选择整季资源':formatBytes(item.size_bytes)));
      row.append(copy,action(item.is_dir?'浏览目录':'进入工作台',()=>item.is_dir?AppResources.openFiles({root_key:'downloads',relative_path:item.relative_path,kind:'directory'}):AppResources.openWorkbench([{...item,root_key:'downloads'}])));
      host.append(row);
    }
  }
  function renderTasks(result) {
    const host=$('home-tasks');host.replaceChildren();
    if(result.status==='rejected'){host.append(node('p',`任务读取失败：${result.reason.message}`,'home-state home-error'));return;}
    const tasks=result.value.items||[];
    const active=tasks.filter(t=>['queued','running','failed','auth_required'].includes(t.status)).slice(0,5);
    if(!active.length){host.append(node('p','当前没有进行中或需要处理的任务。制作任务会在后台运行，你可以继续编辑发布内容。','home-state'));return;}
    for(const task of active){
      const row=node('article',null,'home-task'),copy=node('div',null,'home-task-copy');
      const types={v2_media_probe:'媒体探测',v2_media_report:'媒体报告',torrent_build:'种子制作',screenshot:'截图',screenshot_collage:'拼图',image_upload:'图床上传',v2_rename_apply:'重命名',v2_rename_undo:'撤销重命名'};
      copy.append(node('strong',task.title||`${task.kind==='download'?'下载':types[task.task_type]||'媒体任务'} #${task.id}`),node('small',status[task.status]||task.status));
      if(task.status==='running'){const p=node('progress');p.max=100;p.setAttribute('aria-label','任务进度');if(task.progress!=null)p.value=Math.min(99,Math.max(0,task.progress));copy.append(p);}
      if(task.error)copy.append(node('small',task.error,'home-error'));
      row.append(copy);host.append(row);
    }
  }
  async function load(){
    if(pending)return pending;
    if(!$('home-resources'))return;
    const refresh=$('home-refresh');refresh.disabled=true;
    pending=(async()=>{
      const results=await Promise.allSettled([api('/api/files/browse?root_key=downloads&page_size=30&sort=mtime&direction=desc'),api('/api/v2/tasks?limit=100')]);
      renderResources(results[0]);renderTasks(results[1]);
      $('home-system-status').textContent=results.every(x=>x.status==='fulfilled')?'目录与任务服务可用':'部分状态暂不可用，请重试';
      $('home-updated').textContent=`更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
    })().finally(()=>{pending=null;refresh.disabled=false;});
    return pending;
  }
  window.V2Home={load};
})();
