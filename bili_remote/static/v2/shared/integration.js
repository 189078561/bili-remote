/* Shared page context and real module mounts. No publication draft storage. */
(async () => {
  "use strict";
  let workbench;
  try {
    await loadAppSettings();
    workbench = await BiliWorkbench.mount(document.querySelector('[data-workbench]'));
    window.V2Workbench = workbench;
    const style=document.createElement('link'); style.rel='stylesheet'; style.href='/static/v2/naming/naming.css'; document.head.append(style);
    const namingModule=await import('/static/v2/naming/naming.js');
    const naming=namingModule.mount(workbench.el('naming-panel'), {
      request: api,
      selectResources: async () => {
        const items=await FileBrowser.openPicker({mode:'select-file',scope:'browse',rootKeys:['downloads'],selectionType:'both',allowMultiple:true,title:'选择命名资源'});
        return (Array.isArray(items)?items:items?[items]:[]).map(x=>({root_key:x.root_key,relative_path:x.relative_path,kind:x.is_dir?'directory':'file',fingerprint:x.fingerprint}));
      },
      onContextChange: async context => {
        workbench.context=context; workbench.resources=context.resources; workbench.invalidateRename();
        workbench.renderContext(); await workbench.generateAll();
      },
      onTask: (task, plan) => workbench.track(task,{type:'rename',epoch:workbench.epoch,...plan}),
    });
    workbench.host.addEventListener('v2:renamed',()=>{ if(workbench.context) naming.setContext(workbench.context); });
    workbench.el('naming-advanced').addEventListener('toggle',()=>{
      if(workbench.el('naming-advanced').open && workbench.context) naming.setContext(workbench.context);
    });
    const open = async context => {
      openBusinessView('publish');
      await workbench.select(context.resources, {context, origins: context.origins || []});
      await workbench.naming();
    };
    workbench.el('save-rules').onclick=async () => {
      try {
        const memory=await api('/api/v2/naming/memory');
        await api('/api/v2/naming/memory',{method:'PUT',body:JSON.stringify({scope:'global',expected_revision:memory.revision.global || 0,
          naming_rule:workbench.readRule('naming-rule'),subtitle_rule:workbench.readRule('subtitle-rule')})});
        AppNotice.show('规则已保存；发布文本仅保留在当前页面');
      } catch(error) { AppNotice.show(error.message,true); }
    };
    const adapter = new FileBrowserAdapter({
      getContext: () => workbench.context,
      setContext: context => { workbench.context = context; },
      openWorkbench: open, openNaming: open,
    });
    mediaBrowser = adapter.mount(document.querySelector('#file-browser-host'), {
      title: '本地文件',
      onTool: async (kind, items, root) => {
        await adapter.handoff('workbench', items, root);
        workbench.setTab(({screenshots:'images',collage:'images','release-info':'report',torrent:'torrent',torrents:'torrent'})[kind] || 'content');
      },
      onCollage: async (items, root) => { await adapter.handoff('workbench',items,root); workbench.setTab('images'); },
    });
    await mediaBrowser.init();
    document.addEventListener('app:files-refresh', () => mediaBrowser.loadDirectory({clearSelection:false}).catch(error=>AppNotice.show(error.message,true)));
    BiliDownloadV2.mount(document.querySelector('.dv2'), {
      singleTemplate: downloadDefaults.single, playlistTemplate: downloadDefaults.playlist,
      onLogin: mode => openQrLogin(mode),
      onChooseDirectory: async () => {
        const chosen=await FileBrowser.openPicker({mode:'select-directory',scope:'browse',rootKeys:['downloads'],title:'选择保存目录'});
        return chosen?.relative_path || '';
      },
      onOpenFile: async job => {
        const proposal=await api(`/api/v2/downloads/${job.id}/recognize`,{method:'POST',body:'{}'});
        await open(proposal.context);
      },
      onCompleted: job => { if(!window.AppTaskCenter) AppNotice.show(`下载 #${job.id} 已完成，可打开工作台识别并预览命名`); },
    });
  } catch (error) { AppNotice.show(`模块挂载失败：${error.message}`,true); }
})();
