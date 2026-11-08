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
    const signature = refs => JSON.stringify((refs || []).map(ref => [ref.root_key,ref.relative_path,ref.kind,ref.fingerprint]).sort());
    const naming=namingModule.mount(workbench.el('naming-panel'), {
      request: api,
      selectResources: async ({kind} = {}) => {
        const epoch = workbench.epoch;
        const items=await FileBrowser.openPicker({mode:'select-file',displayMode:'picker',scope:'browse',rootKeys:['downloads'],selectionType:kind === 'sidecar' ? 'file' : 'both',allowMultiple:true,title:kind === 'sidecar' ? '选择关联字幕或音轨' : '选择命名资源'});
        const selected = Array.isArray(items)?items:items?[items]:[];
        if (!selected.length || epoch !== workbench.epoch) return [];
        if (kind === 'sidecar') return selected.map(x=>({root_key:x.root_key,relative_path:x.relative_path,kind:'file',fingerprint:x.fingerprint}));
        // The page commits resources and synchronizes this panel through v2:context.
        // Returning no local replacement avoids discarding the resolved shared context.
        await workbench.select(selected);
        return [];
      },
      getTaskScope: () => ({epoch: workbench.epoch}),
      onContextChange: async (context, scope) => {
        if (scope?.epoch !== workbench.epoch || !workbench.context ||
            context.context_id !== workbench.context.context_id || signature(context.resources) !== signature(workbench.resources)) return false;
        const epoch = workbench.epoch;
        workbench.versions.context = (workbench.versions.context || 0) + 1;
        workbench.cancelTimer(workbench.adjustmentTimer); workbench.pendingAdjustmentEpoch = null;
        workbench.localNamingRule = {epoch, rule:context.effective_rules?.naming_rule || {}};
        workbench.context=context; workbench.resources=context.resources; workbench.invalidateRename();
        workbench.renderContext(); await workbench.generateAll();
        return epoch === workbench.epoch && workbench.context === context;
      },
      onTask: (task, plan) => workbench.track(task,{type:'rename',...plan}),
      onPreview: (preview, scope) => { if (scope?.epoch === workbench.epoch) workbench.showAdvancedPreview(preview); },
    });
    workbench.host.addEventListener('v2:context', event => {
      const {context, reason} = event.detail;
      naming.setContext(context, {resourceSwitch: reason === 'selection'});
    });
    workbench.host.addEventListener('v2:naming-accepted',event=>naming.setBatch(event.detail.batchId));
    naming.setContext(workbench.context);
    const open = async context => {
      if (!await workbench.select(context.resources, {context, origins: context.origins || []})) return false;
      const epoch = workbench.epoch;
      openBusinessView('publish');
      workbench.setLayoutView('edit');
      if (workbench.context && !workbench.preview) await workbench.naming();
      return epoch === workbench.epoch;
    };
    workbench.el('save-rules').onclick=async () => {
      try {
        const memory=await api('/api/v2/naming/memory');
        const globalNaming={...workbench.readRule('naming-rule')};
        for(const key of ['custom_title','selected_title_id','episode_overrides'])delete globalNaming[key];
        await api('/api/v2/naming/memory',{method:'PUT',body:JSON.stringify({scope:'global',expected_revision:memory.revision.global || 0,
          naming_rule:globalNaming,subtitle_rule:workbench.readRule('subtitle-rule')})});
        AppNotice.show('规则已保存；发布文本仅保留在当前页面');
      } catch(error) { AppNotice.show(error.message,true); }
    };
    const adapter = new FileBrowserAdapter({
      getContext: () => workbench.context,
      getContextVersion: () => workbench.epoch,
      openWorkbench: open, openNaming: open,
    });
    mediaBrowser = adapter.mount(document.querySelector('#file-browser-host'), {
      title: '本地文件',
      onTool: async (kind, items, root) => {
        if (kind === 'upload') return BiliModules.runFileTool('upload', items, root);
        if (!await adapter.handoff('workbench', items, root)) return;
        workbench.setTab(({screenshots:'images',collage:'images',report:'report','release-info':'report',torrent:'torrent',torrents:'torrent'})[kind] || 'content');
      },
      onCollage: async (items, root) => { if (await adapter.handoff('workbench',items,root)) workbench.setTab('images'); },
    });
    await mediaBrowser.init();
    document.addEventListener('app:files-refresh', () => mediaBrowser.loadDirectory({clearSelection:false}).catch(error=>AppNotice.show(error.message,true)));
    let downloadSelection = 0;
    const recognizeDownload = async job => {
      const epoch=workbench.epoch, selection=++downloadSelection;
      const proposal=await api(`/api/v2/downloads/${job.id}/recognize`,{method:'POST',body:'{}'});
      if(epoch!==workbench.epoch || selection!==downloadSelection) return null;
      if(!proposal.context?.resources?.length) throw new Error('下载文件不可用，请查看任务详情');
      return proposal.context;
    };
    window.AppResources = {
      openWorkbench: items => adapter.handoff('workbench',items,items[0]?.root_key || 'downloads'),
      openFiles: async ref => {
        if(!ref)throw new Error('没有可打开的资源');
        const directory=ref.kind==='directory'?ref.relative_path:ref.relative_path.split('/').slice(0,-1).join('/');
        openBusinessView('files');
        await mediaBrowser.navigate(ref.root_key,directory,{focus:ref.kind==='directory'?null:ref.relative_path});
      },
      openDownload: async (job, destination='workbench') => {
        const context=await recognizeDownload(job);if(!context)return false;
        if(destination==='files')return AppResources.openFiles(context.resources[0]);
        return open(context);
      }
    };
    BiliDownloadV2.mount(document.querySelector('.dv2'), {
      singleTemplate: downloadDefaults.single, playlistTemplate: downloadDefaults.playlist,
      onLogin: mode => openQrLogin(mode),
      onChooseDirectory: async () => {
        const chosen=await FileBrowser.openPicker({mode:'select-directory',scope:'browse',rootKeys:['downloads'],title:'选择保存目录'});
        return chosen?.relative_path || '';
      },
      onOpenFile: job => AppResources.openDownload(job),
      onOpenDirectory: job => AppResources.openDownload(job,'files'),
      onCompleted: job => { if(!window.AppTaskCenter) AppNotice.show(`下载 #${job.id} 已完成，可打开工作台识别并预览命名`); },
    });
  } catch (error) { AppNotice.show(`模块挂载失败：${error.message}`,true); }
})();
