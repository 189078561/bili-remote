(function () {
  "use strict";

  const els = {
    view: document.querySelector("#settings-view"),
    content: document.querySelector("#settings-content"),
    groupNav: document.querySelector("#settings-group-nav"),
    groupSelect: document.querySelector("#settings-group-select"),
    save: document.querySelector("#settings-save"),
    cancel: document.querySelector("#settings-cancel"),
    reset: document.querySelector("#settings-reset-group"),
    dirty: document.querySelector("#settings-dirty-indicator"),
    status: document.querySelector("#settings-save-status"),
    savebarTitle: document.querySelector("#settings-savebar-title"),
    savebarDetail: document.querySelector("#settings-savebar-detail"),
    back: document.querySelector("#settings-back")
  };

  let schema = null;
  let settings = null;
  let savedValues = {};
  let draftValues = {};
  let savedDirectories = {};
  let draftDirectories = {};
  let activeGroup = null;
  let loaded = false;
  let loadingPromise = null;
  let saving = false;

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function directoryMap(items) {
    return Object.fromEntries((items || []).map(item => [item.key, {
      mount_key: item.mount_key,
      relative_path: item.relative_path
    }]));
  }

  function fieldsForGroup(group) {
    return group.sections.flatMap(section => section.fields || []);
  }

  function editableFields() {
    return (schema?.groups || []).flatMap(fieldsForGroup).filter(
      field => !field.read_only && field.control !== "directory"
    );
  }

  function changedValues() {
    const changed = {};
    for (const field of editableFields()) {
      const before = savedValues[field.key];
      const after = draftValues[field.key];
      if (JSON.stringify(before) !== JSON.stringify(after)) changed[field.key] = after;
    }
    return changed;
  }

  function changedDirectories() {
    const changed = {};
    for (const [key, value] of Object.entries(draftDirectories)) {
      if (JSON.stringify(value) !== JSON.stringify(savedDirectories[key])) changed[key] = value;
    }
    return changed;
  }

  function dirtyCount() {
    return Object.keys(changedValues()).length + Object.keys(changedDirectories()).length;
  }

  function setStatus(text, kind = "") {
    els.status.textContent = text || "";
    els.status.classList.toggle("error", kind === "error");
    els.status.classList.toggle("success", kind === "success");
  }

  function updateDirtyState() {
    const count = dirtyCount();
    const dirty = count > 0;
    els.dirty.textContent = dirty ? `${count} 项未保存` : "已保存";
    els.dirty.classList.toggle("dirty", dirty);
    els.save.disabled = !dirty || saving;
    els.cancel.disabled = !dirty || saving;
    if (window.BILI_V2) {
      document.querySelector('#settings-savebar').hidden = !dirty;
      els.view.classList.toggle('settings-has-changes',dirty);
    }
    els.savebarTitle.textContent = dirty ? "有未保存的设置" : "设置已保存";
    els.savebarDetail.textContent = dirty
      ? "保存后会更新服务端配置；运行中的解析或下载不会受影响。"
      : "所有修改均已写入服务端。";
  }

  function markChanged() {
    setStatus("");
    updateDirtyState();
  }

  function renderNavigation() {
    els.groupNav.replaceChildren();
    els.groupSelect.replaceChildren();
    for (const group of schema.groups) {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "settings-group-button";
      item.dataset.group = group.key;
      item.textContent = group.title;
      if (window.BILI_V2) {
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg'),use=document.createElementNS('http://www.w3.org/2000/svg','use');
        svg.setAttribute('class','ws-icon');svg.setAttribute('aria-hidden','true');
        use.setAttribute('href','/static/v2/shared/icons.svg#'+({general:'home',files:'folder',downloads:'download',rename:'film',subtitle:'file',collage:'image',image_host:'image',torrent:'file',release:'film',maintenance:'settings'})[group.key]);
        svg.append(use);item.prepend(svg);
      }
      item.classList.toggle("active", group.key === activeGroup);
      item.setAttribute("aria-current", group.key === activeGroup ? "page" : "false");
      item.addEventListener("click", () => selectGroup(group.key));
      els.groupNav.append(item);

      const option = document.createElement("option");
      option.value = group.key;
      option.textContent = group.title;
      els.groupSelect.append(option);
    }
    els.groupSelect.value = activeGroup;
  }

  function renderSection(group, section) {
    const card = document.createElement("article");
    card.className = window.BILI_V2 ? "settings-section settings-inspector" : "card settings-card settings-section";
    card.dataset.section = section.key;
    const heading = document.createElement("div");
    heading.className = "card-heading";
    const copy = document.createElement("div");
    copy.append(element(window.BILI_V2 ? "h3" : "h2", null, section.title));
    if (section.description) copy.append(element("p", "muted", section.description));
    heading.append(copy);
    if (section.advanced) heading.append(element("span", "settings-badge", "高级"));
    card.append(heading);

    if (section.renderer === "v2-rules" && window.V2RuleSettings) {
      const host = element("div"); card.append(host);
      V2RuleSettings.mount(host, section.ruleKind);
      return card;
    }
    if (section.renderer === "naming-profiles") {
      if (!window.NamingProfilesEditor) {
        card.append(element("p", "form-error", "命名预设组件未加载"));
      } else {
        const host = element("div", "naming-profiles-editor");
        card.append(host);
        new window.NamingProfilesEditor(host);
      }
      return card;
    }

    if(group.key === "collage" && section.key === "layout") {
      const presets=element("div","button-row layout-presets");
      for(const [label,columns,rows] of [["九宫格 3×3",3,3],["5×4",5,4],["1×4",1,4],["自动排布",null,null]]) {
        const preset=element("button","button secondary small",label);preset.type="button";
        preset.addEventListener("click",()=>{
          draftValues.collage_default_layout_mode=columns ? "grid" : "count";
          if(columns){draftValues.collage_default_columns=columns;draftValues.collage_default_rows=rows;}
          markChanged();renderGroup();
        });presets.append(preset);
      }
      card.append(presets);
    }
    const grid = element("div", section.advanced ? "settings-field-grid advanced-grid" : "settings-field-grid");
    for (const field of section.fields) {
      const context = {
        directories: window.BILI_V2 ? Object.fromEntries(Object.entries(draftDirectories).map(([key,value])=>{
          const original=(settings?.directories||[]).find(item=>item.key===key)||{};
          const mount=(settings?.mount_roots||[]).find(item=>item.key===value.mount_key);
          return [key,{...original,...value,path:[mount?.path,value.relative_path].filter(Boolean).join('/'),pending:JSON.stringify(value)!==JSON.stringify(savedDirectories[key])}];
        })) : draftDirectories,
        mountRoots: settings?.mount_roots || [],
        secretsConfigured: settings?.secrets_configured || {},
        onChange: value => {
          draftValues[field.key] = value;
          markChanged();
          updateMediaFields();
        },
        onDirectoryChange: (key, value) => {
          draftDirectories[key] = value;
          markChanged();
        },
        onError: message => setStatus(message, "error")
      };
      grid.append(window.SettingsFields.renderField(field, draftValues[field.key], context));
    }
    if (section.advanced) {
      const details = document.createElement("details");
      details.className = "settings-advanced";
      const summary = document.createElement("summary");
      summary.textContent = "展开高级参数";
      details.append(summary, grid);
      card.append(details);
    } else {
      card.append(grid);
    }
    return card;
  }

  function updateMediaFields() {
    if(activeGroup !== "collage")return;
    const collage=draftValues.screenshot_output_mode === "collage";
    const grid=collage && draftValues.collage_default_layout_mode !== "count";
    const timestamps=draftValues.screenshot_default_sampling === "timestamps";
    const visibility={
      collage_default_layout_mode:collage,collage_default_columns:collage,collage_default_rows:grid,
      collage_default_width:collage,collage_default_gap:collage,collage_default_total:!grid && !timestamps,
      screenshot_default_timestamps:timestamps,screenshot_default_style:!collage,
      screenshot_upload_variant:!collage,
    };
    for(const [key,visible] of Object.entries(visibility)) {
      const field=els.content.querySelector(`[data-setting-key="${key}"]`);
      if(field)field.hidden=!visible;
    }
    els.content.querySelectorAll(".layout-presets").forEach(n=>n.hidden=!collage);
  }
  function renderGroup() {
    const group = schema.groups.find(item => item.key === activeGroup) || schema.groups[0];
    if (!group) return;
    activeGroup = group.key;
    els.content.replaceChildren();
    const heading = element("div", "settings-group-heading");
    heading.append(element("p", "eyebrow", "SETTINGS"), element("h2", null, group.title));
    if (group.description) heading.append(element("p", "muted", group.description));
    if (window.BILI_V2) heading.append(els.dirty);
    els.content.append(heading);
    const sourceValues = element("datalist");
    sourceValues.id = "settings-source-values";
    const customSources = Array.isArray(draftValues.rename_custom_sources)
      ? draftValues.rename_custom_sources
      : [];
    for (const source of [...new Set([...(schema.source_defaults || []), ...customSources])]) {
      const option = document.createElement("option");
      option.value = source;
      sourceValues.append(option);
    }
    els.content.append(sourceValues);
    for (const section of group.sections) els.content.append(renderSection(group, section));
    if(window.BILI_V2){const foot=element('div','settings-inspector-footer');foot.append(els.status,els.reset);els.content.append(foot);}
    updateMediaFields();
    renderNavigation();
    updateDirtyState();
  }

  function selectGroup(key) {
    activeGroup = key;
    renderGroup();
    const url = new URL(window.location.href);
    url.searchParams.set("group", key);
    window.history.replaceState(window.history.state, "", url);
  }

  async function load() {
    if (loaded) return;
    if (loadingPromise) return loadingPromise;
    loadingPromise = (async () => {
      setStatus("正在读取设置…");
      try {
        const [nextSchema, nextSettings] = await Promise.all([
          api("/api/settings/schema"),
          api("/api/settings")
        ]);
        schema = nextSchema;
        if (window.BILI_V2) {
          const downloads=schema.groups.find(g=>g.key==='downloads');
          const defaults=downloads.sections.find(s=>s.key==='defaults');
          defaults.fields.sort((a,b)=>(b.key==='download_default_output_dir')-(a.key==='download_default_output_dir'));
          defaults.title='保存与命名';defaults.description='';
          const engine=downloads.sections.find(s=>s.key==='engine'), common=['aria2_enabled','aria2_connections','aria2_resume_retries'];
          downloads.sections.splice(downloads.sections.indexOf(engine),0,{...engine,key:'engine-common',title:'下载引擎',description:'',advanced:false,fields:engine.fields.filter(f=>common.includes(f.key))});
          engine.fields=engine.fields.filter(f=>!common.includes(f.key));engine.title='线路探测与超时';
          const rename=schema.groups.find(g=>g.key==='rename');
          rename.title='智能命名规则';
          rename.sections.unshift({key:'v2-naming',title:'V2 全局命名规则',renderer:'v2-rules',ruleKind:'naming_rule',fields:[]});
          const release=schema.groups.find(g=>g.key==='release');
          if(release) release.title='影视资料与发布';
          schema.groups.splice(schema.groups.indexOf(rename)+1,0,{key:'subtitle',title:'副标题模板',description:'规则可以记住，生成的发布文本只在当前页面保留。',sections:[{key:'v2-subtitle',title:'全局副标题规则',renderer:'v2-rules',ruleKind:'subtitle_rule',fields:[]}]});
        }
        settings = nextSettings;
        savedValues = {...nextSettings.values};
        draftValues = clone(savedValues);
        savedDirectories = directoryMap(nextSettings.directories);
        draftDirectories = clone(savedDirectories);
        const requestedGroup = new URL(window.location.href).searchParams.get("group");
        activeGroup = schema.groups.some(group => group.key === requestedGroup)
          ? requestedGroup
          : schema.groups[0]?.key;
        loaded = true;
        renderGroup();
        setStatus("设置已载入", "success");
      } catch (error) {
        els.content.replaceChildren(element("p", "form-error", `设置读取失败：${error.message}`));
        setStatus("设置读取失败", "error");
      } finally {
        loadingPromise = null;
      }
    })();
    return loadingPromise;
  }

  async function save() {
    const values = changedValues();
    const directories = changedDirectories();
    if (!Object.keys(values).length && !Object.keys(directories).length) {
      setStatus("没有需要保存的修改", "success");
      return;
    }
    saving = true;
    setStatus("正在保存设置…");
    els.save.classList.add("is-busy");
    els.save.setAttribute("aria-busy", "true");
    updateDirtyState();
    try {
      const payload = {};
      if (Object.keys(values).length) payload.values = values;
      if (Object.keys(directories).length) payload.directories = directories;
      settings = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      savedValues = {...settings.values};
      draftValues = clone(savedValues);
      savedDirectories = directoryMap(settings.directories);
      draftDirectories = clone(savedDirectories);
      renderGroup();
      setStatus("设置已保存，后续操作将使用新配置", "success");
      document.dispatchEvent(new CustomEvent("settings-updated", {detail: settings}));
      document.dispatchEvent(new CustomEvent("rename-settings-updated", {
        detail: {
          rename_default_season: settings.rename_default_season,
          rename_default_source: settings.rename_default_source,
          rename_custom_sources: settings.rename_custom_sources
        }
      }));
    } catch (error) {
      setStatus(error.message, "error");
    } finally {
      saving = false;
      els.save.classList.remove("is-busy");
      els.save.setAttribute("aria-busy", "false");
      updateDirtyState();
    }
  }

  function cancel() {
    if (!dirtyCount()) return;
    draftValues = clone(savedValues);
    draftDirectories = clone(savedDirectories);
    renderGroup();
    setStatus("已取消未保存的修改");
  }

  function resetGroup() {
    const group = schema.groups.find(item => item.key === activeGroup);
    if (!group || !dirtyCount() || confirm(`恢复“${group.title}”的默认值？当前未保存的修改将被替换，仍需点击保存。`)) {
      if (!group) return;
      for (const field of fieldsForGroup(group)) {
        if (field.read_only) continue;
        if (field.control === "directory") {
          const key = field.key.replace(/^directories\./, "");
          draftDirectories[key] = clone(field.default);
        } else {
          draftValues[field.key] = clone(field.default);
        }
      }
      renderGroup();
      setStatus("已载入本节默认值，尚未保存");
    }
  }

  function hasUnsavedChanges() {
    return dirtyCount() > 0;
  }

  function confirmLeave() {
    if (!hasUnsavedChanges()) return true;
    return confirm("有未保存的设置，确定离开设置页面吗？");
  }

  document.querySelector('#settings-search')?.addEventListener('click',()=>{
    const dialog=element('dialog','settings-search-dialog'),head=element('div','settings-search-heading');
    dialog.setAttribute('aria-label','查找设置');const input=document.createElement('input');input.type='search';input.placeholder='名称、用途或配置项';input.setAttribute('aria-label','查找设置');
    const close=element('button','button ghost small','关闭');close.type='button';close.onclick=()=>dialog.close();head.append(input,close);
    const results=element('div','settings-search-results');dialog.append(head,results);document.body.append(dialog);
    function search(){results.replaceChildren();const query=input.value.trim().toLocaleLowerCase();
      for(const group of schema?.groups||[]){if(query && group.title.toLocaleLowerCase().includes(query)){const item=element('button','settings-search-result',group.title);item.type='button';item.onclick=()=>{selectGroup(group.key);dialog.close();};results.append(item);}}
      for(const group of schema?.groups||[])for(const field of fieldsForGroup(group)){
        if(!query||![field.label,field.title,field.key,field.description].filter(Boolean).join(' ').toLocaleLowerCase().includes(query))continue;
        const item=element('button','settings-search-result',`${group.title} · ${field.label||field.title||field.key}`);item.type='button';
        item.onclick=()=>{selectGroup(group.key);dialog.close();const row=[...els.content.querySelectorAll('[data-setting-key]')].find(n=>n.dataset.settingKey===field.key);if(row){for(let p=row.parentElement;p&&p!==els.content;p=p.parentElement)if(p.tagName==='DETAILS')p.open=true;row.scrollIntoView({block:'center'});row.querySelector('input,select,textarea,summary,button')?.focus();}};
        results.append(item);
      }
      if(!results.children.length)results.append(element('p','home-state',query?'没有匹配设置':'输入关键词查找真实配置项'));
    }
    input.oninput=search;dialog.addEventListener('close',()=>{dialog.remove();document.querySelector('#settings-search').focus();});search();dialog.showModal();input.focus();
  });
  els.save.addEventListener("click", save);
  els.cancel.addEventListener("click", cancel);
  els.reset.addEventListener("click", resetGroup);
  els.groupSelect.addEventListener("change", () => selectGroup(els.groupSelect.value));
  document.addEventListener("app:view", event => {
    if (event.detail?.view === "settings") load();
  });
  window.addEventListener("beforeunload", event => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s" && !els.view.classList.contains("hidden")) {
      event.preventDefault();
      if (dirtyCount() && !saving) save();
    }
  });

  window.SettingsPage = {load, hasUnsavedChanges, confirmLeave};
})();
