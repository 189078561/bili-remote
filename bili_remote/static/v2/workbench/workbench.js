(function (global) {
  "use strict";
  const instances = new WeakMap();
  const terminal = task => ["completed", "failed", "cancelled"].includes(task.status);
  const val = field => field?.value ?? "";
  const key = task => `${task.kind}:${task.id}`;
  const node = (tag, text, className) => { const el = document.createElement(tag); if (text != null) el.textContent = text; if (className) el.className = className; return el; };
  function safeURL(value, local = false) {
    try { const url = new URL(value, global.location.href); return ["http:", "https:"].includes(url.protocol) && (!local || url.origin === global.location.origin) && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  const signature = (resources, fingerprints = true) => JSON.stringify(resources.map(item => [item.root_key, item.relative_path, item.kind, ...(fingerprints ? [item.fingerprint] : [])]).sort());
  const ref = item => {
    if (!["downloads", "torrents", "images", "release_info"].includes(item.root_key) || !item.relative_path || item.relative_path.startsWith("/") || item.relative_path.includes("\\") || item.relative_path.split("/").some(x => ["..", ".", ""].includes(x)) || item.relative_path.includes("\0") || !item.fingerprint) throw new Error("资源引用无效，请在文件浏览器中重新选择");
    return {root_key: item.root_key, relative_path: item.relative_path, kind: item.is_dir || item.kind === "directory" ? "directory" : "file", fingerprint: item.fingerprint};
  };
  class Workbench {
    constructor(host, {api = new global.BiliWorkbenchAPI(), FileBrowser = global.FileBrowser, pollInterval = 2000} = {}) {
      if (!host) throw new Error("工作台缺少挂载节点");
      this.host = host; this.api = api; this.FileBrowser = FileBrowser; this.pollInterval = pollInterval;
      this.context = null; this.resources = []; this.pendingSelection = []; this.selectionSignature = null; this.selectionVersion = 0; this.origins = []; this.epoch = 0; this.versions = {}; this.dirty = new Set(); this.editVersion = 0;
      this.fields = {release_title: "", subtitle: "", overview: ""}; this.preview = null; this.previewKey = null; this.previewUndo = false; this.renameBatch = null; this.episodeOverrides = {}; this.content = ""; this.images = []; this.artifacts = []; this.links = [];
      this.fieldVersions = {}; this.tasks = new Map(); this.jobs = new Map(); this.notified = new Set(); this.applied = new Set(); this.pending = new Set(); this.timers = new Set(); this.abort = new AbortController(); this.destroyed = false;
      this.listen(); this.setTab("content"); this.setupLayout();
    }
    el(name) { return this.host.querySelector(`[data-wb="${name}"]`); }
    button(action) { return this.host.querySelector(`[data-action="${action}"]`); }
    async init() {
      if (this.FileBrowser) {
        this.browser = new this.FileBrowser(this.el("browser"), {
          mode: "select-file", displayMode: "picker", scope: "browse",
          rootKeys: ["downloads", "torrents", "images", "release_info"], selectionType: "both", allowMultiple: true,
          onSelectionChange: items => this.stageSelection(items),
          onLoadStart: () => this.stageSelection(this.browser?.selectedItems() || []),
          onConfirm: async items => { this.selecting = true; try { const accepted = await this.select(items); if (accepted) this.openPanel(null); return accepted; } finally { this.selecting = false; } }, confirmLabel: "使用所选资源", title: "资源文件"
        });
        try { await this.browser.init(); } catch (error) { this.el("browser").textContent = error.message; this.notice(error.message, true); }
      } else this.el("browser").textContent = "统一文件浏览器尚未加载，请由主页面加载共享组件。";
      await this.refreshTasks(true);
      return this;
    }
    listen() {
      const opts = {signal: this.abort.signal};
      this.host.addEventListener("click", event => {
        const layout = event.target.closest("[data-layout]");
        if (layout) this.openPanel(layout.dataset.layout === "close" || layout.dataset.layout === "edit" ? null : layout.dataset.layout);
        const format = event.target.closest("[data-format]");
        if (format) {
          const editor = this.host.querySelector('[data-edit="overview"]'), start=editor.selectionStart, end=editor.selectionEnd;
          const selected=editor.value.slice(start,end), tag=format.dataset.format;
          let text = `[${tag}]${selected}[/${tag}]`;
          if (tag === 'url') { const url=global.prompt('链接地址', selected.startsWith('http') ? selected : 'https://'); if (!url) return; if (!safeURL(url)) { this.notice('请输入有效的 HTTP 或 HTTPS 地址',true); return; } text=`[url=${url}]${selected || url}[/url]`; }
          editor.setRangeText(text,start,end,'select'); editor.dispatchEvent(new Event('input',{bubbles:true})); editor.focus();
        }
        const previewMode = event.target.closest("[data-preview-mode]");
        if (previewMode) {
          const source = previewMode.dataset.previewMode === "source";
          this.el("source").hidden = !source; this.el("effect").hidden = source;
          this.host.querySelectorAll("[data-preview-mode]").forEach(button => button.setAttribute("aria-pressed", String(button === previewMode)));
        }
        const tab = event.target.closest("[data-tab]"); if (tab) this.setTab(tab.dataset.tab);
        const copy = event.target.closest("[data-copy]"); if (copy) this.copy(copy.dataset.copy);
        const action = event.target.closest("[data-action]");
        if (action && !action.disabled) this.run(action.dataset.action, action);
      }, opts);
      this.host.addEventListener("input", event => {
        const name = event.target.dataset.edit;
        if (name) { this.fieldVersions[name] = (this.fieldVersions[name] || 0) + 1; this.dirty.add(name); this.setField(name, event.target.value); this.editVersion++; this.scheduleCompose(); }
        if (["naming-rule", "subtitle-rule", "title-rule", "cast-limit", "selected-title", "season", "episode", "scope", "naming-type", "source-rule", "group-rule", "episode-mode", "start-episode", "episode-step", "episode-direction", "episode-offset"].includes(event.target.dataset.wb)) this.scheduleAdjustment(event.target.dataset.wb);
        if (["year", "resolution", "video-codec", "audio-codec"].includes(event.target.dataset.wb)) this.scheduleNamingOverride();
      }, opts);
      const imageControls=()=>{
        const collage=this.el('image-mode').value==='collage',percent=this.el('range-mode').value==='percent';
        this.el('image-columns').disabled=!collage;this.el('image-rows').disabled=!collage;
        this.el('start-percent').disabled=!percent;this.el('end-percent').disabled=!percent;
        this.el('skip-start').disabled=percent;this.el('skip-end').disabled=percent;
        if(collage && this.el('image-rows').value)this.el('image-count').value=Number(this.el('image-columns').value)*Number(this.el('image-rows').value);
      };
      for(const key of ['image-mode','image-columns','image-rows','range-mode'])this.el(key).addEventListener('input',imageControls,opts);
      imageControls();
      this.el("query").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.run("search", this.button("search")); } }, opts);
      this.el("query-inline").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.run("search-inline", this.button("search-inline")); } }, opts);
      this.el("format").addEventListener("change", () => { this.editVersion++; this.scheduleCompose(); }, opts);
      this.el("title-choice").addEventListener("change", () => {
        const title = this.context?.titles?.find(item => item.id === this.el("title-choice").value);
        if (title) { this.el("selected-title").value = title.text; this.scheduleAdjustment("selected-title"); }
      }, opts);
    }
    // Only presentation state lives here. A sheet never mounts a new form/browser.
    setTab(name) {
      this.host.querySelectorAll("[data-panel]").forEach(el => el.hidden = el.dataset.panel !== "content" && el.dataset.panel !== name);
      this.el("tool-heading").textContent = {images:"缩略图与图床",report:"媒体报告",torrent:"种子制作"}[name] || "辅助工具";
      this.openPanel(name === "content" ? null : "tools");
    }
    setupLayout() {
      this.host.addEventListener("keydown", event => {
        if (!this.activePanel || document.querySelector('#modal:not(.hidden)') || document.querySelector('dialog[open]')) return;
        if (event.key === "Escape") { event.preventDefault(); this.openPanel(null); }
        if (event.key === "Tab") {
          const sheet = this.host.querySelector(`[data-sheet="${this.activePanel}"]`);
          const nodes = [...sheet.querySelectorAll('button,input,select,textarea,summary,a[href],[tabindex="0"]')].filter(el => !el.disabled && el.getClientRects().length);
          if (event.shiftKey && document.activeElement === nodes[0]) { event.preventDefault(); nodes.at(-1)?.focus(); }
          else if (!event.shiftKey && document.activeElement === nodes.at(-1)) { event.preventDefault(); nodes[0]?.focus(); }
        }
      }, {signal:this.abort.signal});
    }
    setLayoutView(view) { this.openPanel(view === "preview" ? "preview" : null); }
    setResourceDrawer(open) { this.openPanel(open ? "resources" : null); }
    openPanel(name) {

      if (name && !this.host.querySelector(`[data-sheet="${name}"]`)) return;
      if (!this.activePanel && name) this.panelReturnFocus = document.activeElement;
      this.activePanel = name; this.drawerOpen = name === "resources";
      this.host.classList.toggle("wb-panel-open", !!name);
      this.host.querySelector('.wb-backdrop').hidden = !name;
      this.host.querySelector('.wb-surface').inert = !!name;
      this.host.querySelectorAll('[data-sheet]').forEach(el => el.hidden = el.dataset.sheet !== name);
      this.host.querySelector('[data-layout="resources"]').setAttribute("aria-expanded", String(this.drawerOpen));
      if (name) this.host.querySelector(`[data-sheet="${name}"] button`)?.focus();
      else { this.panelReturnFocus?.focus(); this.panelReturnFocus = null; }
    }
    scheduleAdjustment(field) {
      this.cancelTimer(this.adjustmentTimer);
      if (field === "naming-type") {
        this.namingType = this.el("naming-type").value;
        this.el("naming-rule").value = this.context?.effective_rules?.naming_rule?.templates?.[this.namingType] || "";
      }
      if (["selected-title","season","episode","scope"].includes(field)) this.pendingAdjustmentEpoch = this.epoch;
      if (["naming-rule","subtitle-rule","title-rule","cast-limit","naming-type","source-rule","group-rule","episode-mode","start-episode","episode-step","episode-direction","episode-offset"].includes(field)) this.rememberRules();
      this.invalidateRename(); this.versions.context = (this.versions.context || 0) + 1;
      const ticket = this.adjustmentVersion = (this.adjustmentVersion || 0) + 1, epoch = this.epoch;
      this.contextChanged(); // Also invalidates in-flight advanced naming requests.
      this.el("adjustment-status").textContent = "正在更新命名预览…";
      this.adjustmentTimer = this.later(async () => {
        if (ticket !== this.adjustmentVersion || epoch !== this.epoch || !this.context) return;
        try {
          if (this.pendingAdjustmentEpoch === this.epoch) await this.resolve(null, this.overrides());
          else await this.applyRules();
          if (ticket === this.adjustmentVersion && epoch === this.epoch) this.el("adjustment-status").textContent = "未执行改名 · 预览已更新";
        } catch(error) {
          if (ticket === this.adjustmentVersion && epoch === this.epoch) { this.el("adjustment-status").textContent = error.message; this.notice(error.message,true); }
        }
      }, 450);
    }
    renderPresentation() {
      const ctx = this.context, identity = ctx?.identity, files=ctx?.files||[];
      const measured=files.length>0 && files.every(file=>file.probe&&!file.probe.probe_error&&val(file.probe.video_codec));
      const common=key=>{const values=files.map(file=>val(file.probe?.[key]));return values.length&&values.every(value=>value!==''&&String(value)===String(values[0]))?values[0]:'';};
      this.el("resource-status").textContent = !this.resources.length ? "尚未选择资源" : this.busy("resolve") ? "正在识别" : !ctx ? "等待识别结果" : `${measured?'媒体已探测':'媒体参数待补充'} · ${identity?.confirmed?'影视身份已确认':'影视身份待确认'}`;
      this.el("resource-media").textContent = [common('resolution'),common('video_codec'),common('audio_codec'),common('audio_channels'),ctx?.episode?.season ? `第 ${val(ctx.episode.season)} 季` : '',ctx?.episode?.scope==='season'?'整季':ctx?.episode?.episode?`第 ${val(ctx.episode.episode)} 集`:'',ctx?`${files.length} 个媒体文件`:''].filter(Boolean).join(' · ');
      const identified=files.filter(file=>Number(val(file.episode?.episode))>0).length;
      this.el('season-summary').textContent=ctx?.episode?.scope==='season'?`共 ${files.length} 个媒体文件 · 已识别集号 ${identified} · 待确认 ${files.length-identified} · 命名冲突 ${this.preview?.conflicts?.length??ctx?.conflicts?.length??0}`:'';
      this.el("identity-title").textContent = "影视匹配";
      this.el("identity-name").textContent = identity ? (ctx.titles?.find(title=>title.source !== 'manual')?.text || val(ctx.metadata?.title) || '已匹配作品') : '尚未匹配影视资料';
      this.el("match-status").textContent = identity?.confirmed ? '已确认' : identity ? '待确认' : '待匹配';
      this.el("match-status").classList.toggle('confirmed',Boolean(identity?.confirmed));
      if (this.resources.length) { this.el("resource").textContent = val(ctx?.selected_title) || this.resources[0].relative_path.split("/").at(-1); this.el("context-summary").textContent = this.resources.length === 1 ? this.resources[0].relative_path : `${this.resources.length} 项工作资源 · ${this.resources[0].relative_path} 等`; }
      this.el("provider-status").textContent = ctx ? [val(ctx.metadata?.year),({movie:"电影",series:"剧集",anime:"动画"})[identity?.media_type],identity?.tmdb_id ? `TMDB #${identity.tmdb_id}` : "TMDB 未匹配", identity?.bangumi_id ? `Bangumi #${identity.bangumi_id}` : "Bangumi 未匹配", identity?.confirmed ? "身份已确认" : "可搜索资料或手动输入名称"].filter(Boolean).join(" · ") : "TMDB / Bangumi：等待识别";
      if (this.preview?.stale) this.el("adjustment-status").textContent = "命名预览已过期，请刷新";
      else if (this.preview?.context_revision === ctx?.revision && this.preview) this.el("adjustment-status").textContent = "未执行改名 · 预览已更新";
      else if (this.advancedPreview) this.el("adjustment-status").textContent = "高级预览已更新；请在命名详情中确认执行。";
      this.el("resource").title = this.resources.map(item => item.relative_path).join("\n");
      const first = this.el("rename-list").firstElementChild;
      let summary = null;
      if (first) {
        summary = node("div", null, "wb-rename-row");
        const source = first.querySelector(".wb-rename-source")?.textContent || first.childNodes[0]?.textContent || "";
        const target = first.querySelector(".wb-rename-target")?.textContent || first.childNodes[1]?.textContent || "";
        summary.append(node("span", source, "wb-rename-source"), node("strong", target, "wb-rename-target"));
      }
      this.el("rename-summary").replaceChildren(...(summary ? [...summary.childNodes] : []));
      this.el("change-count").textContent = `查看全部文件名 · ${this.el("rename-list").children.length} 项`;
      this.el("naming-status").hidden = !!first && !this.el("rename").hidden;
      const resultRows = this.el("artifact-summary"); resultRows.replaceChildren();
      for (const artifact of this.artifacts) {
        const item=node('button',null,'wb-artifact'); item.type='button';
        const name=({report:'媒体报告',torrent:'种子文件',screenshot:'截图',collage:'缩略图',upload:'图床链接'})[artifact.kind] || '工具产物';
        item.append(node('strong',name),node('span',artifact.resource?.relative_path?.split('/').at(-1) || `#${artifact.id}`));
        item.onclick=()=>this.setTab(artifact.kind === 'report' ? 'report' : artifact.kind === 'torrent' ? 'torrent' : 'images'); resultRows.append(item);
      }
      for (const [name,count] of [["images",this.images.length],["report",this.artifacts.filter(x=>x.kind==="report").length],["torrent",this.el("torrents").children.length]]) this.el(`${name}-count`).textContent = count ? `${count} 项产物` : "";
    }
    notice(message, error = false) {
      if (this.destroyed) return;
      if ([...this.el("notices").children].some(item => item.querySelector("span")?.textContent === message)) return;
      const item = node("div", null, `wb-notice${error ? " wb-error" : ""}`); item.append(node("span", message));
      const close = node("button", "关闭"); close.type = "button"; close.setAttribute("aria-label", "关闭通知"); close.onclick = () => item.remove(); item.append(close); this.el("notices").append(item);
      while (this.el("notices").children.length > 3) this.el("notices").firstChild.remove();
      if (!error) this.later(() => item.remove(), 4500);
    }
    later(fn, delay) { const timer = setTimeout(() => { this.timers.delete(timer); if (!this.destroyed) fn(); }, delay); this.timers.add(timer); return timer; }
    cancelTimer(timer) { clearTimeout(timer); this.timers.delete(timer); }
    stamp(channel) { const version = (this.versions[channel] || 0) + 1; this.versions[channel] = version; const epoch = this.epoch; return () => !this.destroyed && epoch === this.epoch && version === this.versions[channel]; }
    busy(action) { return this.pending.has(`${this.epoch}:${action}`); }
    contextChanged(reason = "update") { this.host.dispatchEvent(new CustomEvent("v2:context", {detail: {context: this.context, epoch: this.epoch, reason}})); }
    async run(action, button) {
      if (this.busy(action)) return;
      if (["overrides","rules","naming","resolve"].includes(action)) this.cancelTimer(this.adjustmentTimer);
      const epoch = this.epoch, pendingKey = `${epoch}:${action}`;
      this.pending.add(pendingKey); if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
      try {
        const handlers = {resolve: () => this.resolve(), search: () => this.search(), "search-inline": () => this.searchInline(), overrides: () => this.resolve(null, this.overrides()), rules: () => this.applyRules(), naming: () => this.naming(), undo: () => this.showUndoPreview(), apply: () => this.applyRename(), title: () => this.generate("release_title", true), subtitle: () => this.generate("subtitle", true), probe: () => this.probe(), screenshots: () => this.screenshots(), report: () => this.report(), torrent: () => this.torrent(), upload: () => this.upload()};
        if (["rules", "naming"].includes(action) && this.pendingAdjustmentEpoch === this.epoch) await this.resolve(null, this.overrides());
        else await handlers[action]?.();
      } catch (error) { if (epoch === this.epoch) this.notice(error.message, true); }
      finally { this.pending.delete(pendingKey); if (!this.busy(action)) button?.removeAttribute("aria-busy"); this.controls(); }
    }
    controls() {
      if (this.destroyed) return;
      this.renderPresentation();
      const has = this.resources.length > 0, ctx = !!this.context;
      for (const action of ["resolve", "overrides", "probe", "screenshots"]) this.button(action).disabled = !has || this.busy(action);
      for (const action of ["naming", "title", "subtitle", "rules"]) this.button(action).disabled = !ctx || this.busy(action);
      for (const action of ["report", "torrent"]) this.button(action).disabled = this.resources.length !== 1 || this.busy(action);
      this.button("upload").disabled = !this.images.length || this.busy("upload");
      this.button("search").disabled = this.busy("search");
      this.button("search-inline").disabled = !this.resources.length || this.busy("search-inline");
      this.host.querySelectorAll('[data-action="apply"]').forEach(el => el.disabled = !this.applyable() || this.busy("apply"));
      this.host.querySelectorAll('[data-action="undo"]').forEach(el => el.disabled = !this.renameBatch || this.busy("undo"));
      this.host.querySelectorAll("[data-copy]").forEach(el => el.disabled = !(el.dataset.copy === "content" ? this.content : this.fields[el.dataset.copy]));
    }
    applyable() {
      const preview = this.preview;
      if (preview?.stale) return false;
      if (!preview?.can_apply || !preview.preview_token || !preview.operations?.length || preview.conflicts?.length > 0 || !(Date.parse(preview.expires_at) > Date.now())) return false;
      if (preview.undo) return true;
      return preview.context_revision === this.context?.revision;
    }
    renameMetrics(preview = this.preview) {
      if (!preview) return {changed: 0, unchanged: 0, conflict: 0, manual: 0};
      const operations = preview.operations || [];
      const changed = Number.isInteger(preview.changed_count) ? preview.changed_count : operations.filter(op => op.source?.relative_path !== op.target_relative_path).length;
      const unchanged = Number.isInteger(preview.unchanged_count) ? preview.unchanged_count : Math.max(0, operations.length - changed);
      const conflict = Number.isInteger(preview.conflict_count) ? preview.conflict_count : (preview.conflicts || []).length;
      const manual = Number.isInteger(preview.manual_confirmation_count) ? preview.manual_confirmation_count : (preview.manual_confirmations || []).length;
      return {changed, unchanged, conflict, manual};
    }
    renderRenameMetrics(preview = this.preview) {
      const metrics = this.renameMetrics(preview);
      const parts = [["待修改", metrics.changed, false], ["无需修改", metrics.unchanged, false], ["冲突", metrics.conflict, metrics.conflict > 0], ["待人工确认", metrics.manual, metrics.manual > 0]];
      const render = container => {
        if (!container) return;
        container.replaceChildren();
        for (const [label, count, warn] of parts) {
          const item = node("span", `${label} ${count}`, warn ? "wb-metric-warn" : null);
          item.setAttribute("aria-label", `${label} ${count}`);
          container.append(item);
        }
      };
      render(this.el("rename-metrics"));
      render(this.el("rename-metrics-sheet"));
    }
    invalidateRename() {
      this.advancedPreview = false;
      this.versions.naming = (this.versions.naming || 0) + 1;
      // Any in-flight naming/preview request now describes a stale context and
      // must not render after a parameter or selection change.
      this.previewKey = null;
      this.previewUndo = false;
      this.preview = null;
      this.el("rename").hidden = true;
      this.el("rename-warning").textContent = "";
      this.el("naming-status").textContent = this.context ? "命名预览待更新；不会自动修改文件。" : "选择资源后，将显示服务返回的目标文件名。";
      this.renderRenameMetrics();
      this.controls();
    }
    invalidateContent() {
      this.content = ""; this.el("output").textContent = ""; this.el("rich-preview").replaceChildren(); this.el("html-preview").hidden = true; this.el("html-preview").removeAttribute("srcdoc");
      this.el("validation").textContent = this.context ? "正在更新预览…" : "等待识别服务返回上下文"; this.controls();
    }
    stageSelection(items) {
      this.pendingSelection = items.slice();
      this.el("selection-status").textContent = `已勾选 ${items.length} 项，点击后正式切换工作资源。`;
    }
    async select(items, {context = null, origins = []} = {}) {
      // Validate candidates before touching the active resource or any edited content.
      let resources;
      try {
        if (!Array.isArray(items) || !items.length || items.length > 1000) throw new Error("请选择 1 至 1000 项资源");
        if (items.some(item => !item.is_dir && !["directory", "video", "file"].includes(item.kind))) throw new Error("请选择视频文件或整季目录");
        const candidates = items.map(ref);
        resources = candidates.filter((item,index) => candidates.findIndex(other=>other.root_key===item.root_key && other.relative_path===item.relative_path) === index && !candidates.some(parent=>parent.kind==='directory' && parent.root_key===item.root_key && item.relative_path.startsWith(parent.relative_path + '/')));
      } catch (error) { this.notice(error.message, true); return false; }
      const selectedSignature = signature(resources), selectionVersion = ++this.selectionVersion, previousEpoch = this.epoch;
      const includesDirectory = resources.some(item => item.kind === "directory");
      if (!includesDirectory && (selectedSignature === signature(this.resources) || selectedSignature === this.selectionSignature)) return true;
      if (includesDirectory || signature(resources, false) === signature(this.resources, false)) {
        // Browse uses quick fingerprints; handoffs use canonical V2 references. Compare
        // through the existing reference service before treating the same path as new.
        try {
          resources = await Promise.all(resources.map(resource => this.api.request("/api/v2/resources/reference", resource)));
        } catch (error) {
          if (selectionVersion === this.selectionVersion && previousEpoch === this.epoch) this.notice(error.message, true);
          return false;
        }
        if (selectionVersion !== this.selectionVersion || previousEpoch !== this.epoch || this.destroyed) return false;
        if (signature(resources) === signature(this.resources)) { this.selectionSignature = selectedSignature; return true; }
      }
      if (this.dirty.size && !global.confirm("当前资源有人工编辑的标题、副标题或简介。切换资源将清空这些编辑及当前发布产物。是否确认切换？取消可继续编辑和复制当前内容。")) return false;
      const epoch = ++this.epoch;
      this.versions = {}; this.resources = resources; this.selectionSignature = selectedSignature;
      this.context = null; this.origins = origins; this.dirty.clear(); this.editVersion++;
      this.cancelTimer(this.composeTimer); this.contextChanged("selection");
      this.images = []; this.artifacts = []; this.links = []; this.episodeOverrides = {}; this.pendingReplacement = null; this.probedResources = null; this.invalidateRename();
      for (const field of Object.keys(this.fields)) this.setField(field, "");
      ["images", "reports", "torrents", "upload-links", "issues", "candidates", "media"].forEach(name => this.el(name).replaceChildren());
      ["naming-rule", "subtitle-rule", "title-rule", "selected-title", "season", "episode", "year", "resolution", "video-codec", "audio-codec"].forEach(name => this.el(name).value = "");
      this.el("episode-mode").value = "detected";
      this.el("start-episode").value = "";
      this.el("episode-step").value = "1";
      this.el("episode-direction").value = "increment";
      this.el("episode-offset").value = "";
      this.el("naming-type").value = "movie";
      this.namingType = "movie";
      this.el("candidates-inline").replaceChildren();
      this.el("query-inline").value = resources[0].relative_path.split("/").at(-1).replace(/\.[^.]+$/, "").replace(/[._\-]+/g, " ").trim();
      this.el("scope").value = resources.some(item => item.kind === "directory") || resources.length > 1 ? "season" : "single";
      this.el("resource").textContent = `${resources.length} 项资源 · ${resources[0].relative_path.split("/").at(-1)}`;
      this.el("context-summary").textContent = "资料和参数尚未识别";
      this.invalidateContent();
      try {
        if (context && signature(context.resources) === signature(resources)) {
          this.context = context; this.contextChanged(); this.renderContext(); await this.generateAll();
          if (epoch === this.epoch && this.context) await this.naming();
        } else await this.resolve();
      } catch (error) {
        if (epoch === this.epoch) { this.el("issues").textContent = error.message; this.notice(error.message, true); }
      }
      this.controls();
      return !this.destroyed && epoch === this.epoch;
    }
    overrides() {
      const result = {episode: {scope: this.el("scope").value}};
      for (const name of ["season", "episode"]) {
        const input = this.el(name).value;
        if (input !== "") { const number = Number(input); if (!Number.isInteger(number) || number < (name === "season" ? 0 : 1)) throw new Error("季号和集号必须是有效整数"); result.episode[name] = number; }
      }
      if (this.el("selected-title").value.trim()) result.selected_title = this.el("selected-title").value.trim();
      return result;
    }
    namingOverrides() {
      const result = {};
      if (Object.keys(this.episodeOverrides).length) result.episode_overrides = {...this.episodeOverrides};
      const fileValues = {};
      const year = this.el("year").value.trim();
      const resolution = this.el("resolution").value.trim();
      const videoCodec = this.el("video-codec").value.trim();
      const audioCodec = this.el("audio-codec").value.trim();
      if (year !== "") {
        if (!/^\d{4}$/.test(year)) throw new Error("年份须为四位数字，例如 2020");
        fileValues.year = Number(year);
      }
      if (resolution !== "") fileValues.resolution = resolution;
      if (videoCodec !== "") fileValues.video_codec = videoCodec;
      if (audioCodec !== "") fileValues.audio_codec = audioCodec;
      if (Object.keys(fileValues).length && this.context?.files?.length) {
        result.file_values = Object.fromEntries(this.context.files.map(file => [file.ref.relative_path, {...fileValues}]));
      }
      return result;
    }
    scheduleNamingOverride() {
      if (this.destroyed) return;
      this.cancelTimer(this.overrideTimer);
      this.invalidateRename();
      this.overrideTimer = this.later(() => { if (this.context && !this.destroyed) this.naming().catch(error => this.notice(error.message, true)); }, 320);
    }
    async resolve(selection = null, manual_overrides = {}) {
      if (!this.resources.length) throw new Error("请先选择资源");
      if (this.pendingAdjustmentEpoch === this.epoch) { manual_overrides = {...this.overrides(), ...manual_overrides}; this.rememberRules(); }
      this.invalidateRename(); this.invalidateContent(); const current = this.stamp("context");
      const payload = {resources: this.resources, origins: this.origins, selection, context: this.context, manual_overrides};
      const previous = this.resolveRequest, epoch = this.epoch;
      const request = async () => {
        try { return await this.api.resolve(payload); }
        catch (error) {
          // Resolve advances a server revision. If an earlier response is still
          // in transit, use that revision only as the base for the latest input;
          // never render the older fields or reset the user's publication text.
          if (error.code !== "stale_context" || !previous || previous.epoch !== epoch || previous.contextId !== payload.context?.context_id) throw error;
          const older = await previous.promise.catch(() => null);
          if (!older?.context || older.context.context_id !== payload.context?.context_id || signature(older.context.resources) !== signature(payload.resources)) throw error;
          if (!current()) return older;
          return this.api.resolve({...payload, context:older.context});
        }
      };
      const pending = {epoch, contextId:payload.context?.context_id, promise:request()};
      this.resolveRequest = pending;
      const response = await pending.promise;
      if (!current()) return;
      if (!response.context || !Number.isInteger(response.context.revision)) throw new Error("识别服务未返回有效上下文");
      this.context = response.context;
      if (this.localNamingRule?.epoch === this.epoch) this.context.effective_rules = {...this.context.effective_rules, naming_rule:this.localNamingRule.rule, ...(this.localNamingRule.subtitle ? {subtitle_rule:this.localNamingRule.subtitle} : {})};
      this.pendingAdjustmentEpoch = null;
      this.resources = response.context.resources; this.contextChanged();
      for (const name of ["release_title", "subtitle"]) if (!this.dirty.has(name)) this.setField(name, "");
      this.renderContext(); await this.generateAll();
      const probeKey = JSON.stringify(this.resources);
      if (current() && this.probedResources !== probeKey && (!this.context.files?.length || this.context.files.some(file => !file.probe))) {
        this.probedResources = probeKey;
        try { await this.probe(); } catch (error) { if (current()) this.notice(`媒体参数：${error.message}`, true); }
      }
      if (current() && this.context) await this.naming();
    }
    renderContext() {
      const ctx = this.context; if (!ctx) return;
      this.el("context-summary").textContent = [val(ctx.selected_title) || "未确认影视身份", ctx.episode?.scope === "season" ? "整季" : "单集 / 单视频", `修订 ${ctx.revision}`, `${ctx.files?.length || 0} 个媒体文件`].join(" · ");
      this.el("selected-title").value = val(ctx.selected_title); this.el("season").value = val(ctx.episode?.season); this.el("episode").value = val(ctx.episode?.episode); this.el("scope").value = ctx.episode?.scope || this.el("scope").value;
      const probeValue = key => { const values = (ctx.files || []).map(file => val(file.probe?.[key])); return values.length && values.every(value => value !== "" && String(value) === String(values[0])) ? values[0] : ""; };
      const autofill = {year: val(ctx.metadata?.year), resolution: probeValue("resolution"), "video-codec": probeValue("video_codec"), "audio-codec": probeValue("audio_codec")};
      for (const [name, value] of Object.entries(autofill)) if (value !== "" && this.el(name).value.trim() === "") this.el(name).value = value;
      this.el("issues").textContent = [...new Set([...(ctx.warnings || []), ...(ctx.conflicts || [])].map(x => x.message))].join("；");
      this.namingType = ctx.effective_rules?.naming_rule?.template_id || ctx.identity?.media_type || "movie";
      this.namingType = {anime_single:"anime",tv:"series"}[this.namingType] || this.namingType;
      this.el("naming-type").value = this.namingType;
      const templateSource = ctx.effective_rules?.sources?.["naming_rule.template_id"];
      const sourceLabels = {
        explicit_rule: "规则来源：本次人工规则",
        remembered_rule: "规则来源：已记住的系列或季度规则",
        confirmed_identity: "规则来源：已确认影视身份",
        directory_scope: "规则来源：整季目录",
        multi_file_scope: "规则来源：多文件资源",
        movie_fallback: "规则来源：单视频默认"
      };
      const sourceName = templateSource?.source_ref ? String(templateSource.source_ref).split("/").at(-1) : "";
      this.el("template-source").textContent = templateSource?.reason === "filename_episode"
        ? `规则来源：文件名${sourceName ? ` ${sourceName}` : ""}`
        : (sourceLabels[templateSource?.reason] || "");
      this.el("naming-rule").value = ctx.effective_rules?.naming_rule?.templates?.[this.namingType] || "";
      for(const key of ['source','group']){const rule=ctx.effective_rules?.naming_rule?.[key];this.el(`${key}-rule`).value=(typeof rule==='object'?rule?.value:rule)||(key==='source'?'WEB-DL':'HDVWEB');}
      const batch = ctx.effective_rules?.naming_rule || {};
      this.el("episode-mode").value = batch.episode_mode || "detected";
      this.el("start-episode").value = batch.start_episode ?? 1;
      this.el("episode-step").value = batch.episode_step ?? 1;
      this.el("episode-direction").value = batch.episode_direction || "increment";
      this.el("episode-offset").value = ctx.effective_rules?.episode_rules?.offset ?? "";
      this.el("subtitle-rule").value = ctx.effective_rules?.subtitle_rule?.template_id || "";
      this.el("cast-limit").value = String(ctx.effective_rules?.subtitle_rule?.cast_limit || 3);
      if (!this.dirty.has("overview")) this.setField("overview", val(ctx.metadata?.overview));
      const choices = this.el("title-choice"); choices.replaceChildren(node("option", "选择中文、英文或其他译名")); choices.firstChild.value = "";
      for (const title of ctx.titles || []) { const option = node("option", `${title.text} · ${title.language} / ${title.kind}`); option.value = title.id; option.selected = title.text === val(ctx.selected_title); choices.append(option); }
      choices.disabled = !(ctx.titles || []).length;
      this.el("media").replaceChildren();
      for (const file of ctx.files || []) {
        const row = node("div", null, "wb-report"), probe = file.probe || {};
        row.append(node("strong", file.ref.relative_path));
        for (const [name, label] of [["resolution", "分辨率"], ["video_codec", "视频"], ["audio_codec", "音频"], ["audio_channels", "声道"], ["hdr", "HDR"]]) {
          const field = probe[name], value = val(field);
          row.append(node("span", ` · ${label}：${Array.isArray(value) ? value.join(" / ") || "未知" : value || "未知"}${field?.source ? `（${field.source}）` : ""}`));
        }
        this.el("media").append(row);
      }
      this.controls();
    }
    readRule(name, fallback = {}) {
      const raw = this.el(name).value.trim();
      if (name === "title-rule") return raw ? {template: raw} : fallback;
      if (name === "naming-rule") {
        const rule = this.context?.effective_rules?.naming_rule || fallback, kind = this.el("naming-type").value || this.namingType;
        const templates = {...rule.templates};
        if (raw) templates[kind] = raw; else delete templates[kind];
        const values={};
        for(const key of ['source','group']){const value=this.el(`${key}-rule`).value.trim(),old=rule[key];values[key]=value===(typeof old==='object'?old?.value:old)?old:{value,source:'manual',manual:true,confirmed:true};}
        const episodeMode = this.el("episode-mode").value || rule.episode_mode || "detected";
        const startEpisode = this.el("start-episode").value.trim();
        const episodeStep = this.el("episode-step").value.trim();
        const episodeDirection = this.el("episode-direction").value || rule.episode_direction || "increment";
        const episodeOffset = this.el("episode-offset").value.trim();
        const episodeRules = {};
        if (episodeMode === "offset") {
          const offset = episodeOffset === "" ? 0 : Number(episodeOffset);
          if (!Number.isInteger(offset) || Math.abs(offset) > 100000) throw new Error("集数偏移必须是整数，且绝对值不超过 100000");
          episodeRules.offset = offset;
        }
        return {...rule, ...values, template_id:kind, templates, episode_mode: episodeMode,
          start_episode: startEpisode === "" ? (rule.start_episode ?? 1) : Number(startEpisode),
          episode_step: episodeStep === "" ? (rule.episode_step ?? 1) : Number(episodeStep),
          episode_direction: episodeDirection,
          ...(Object.keys(episodeRules).length ? {episode_rules: episodeRules} : {})};
      }
      const rule = this.context?.effective_rules?.subtitle_rule || fallback;
      return {...rule, ...(raw ? {template_id: raw} : {}), cast_limit: this.el("cast-limit").value === "all" ? "all" : Number(this.el("cast-limit").value)};
    }
    rememberRules() {
      if (!this.context) return;
      const naming = this.readRule("naming-rule"), subtitle = this.readRule("subtitle-rule"); this.readRule("title-rule");
      if (this.pendingAdjustmentEpoch === this.epoch && this.el("selected-title").value.trim()) {
        naming.custom_title = this.el("selected-title").value.trim(); delete naming.selected_title_id;
      }
      this.localNamingRule = {epoch:this.epoch, rule:naming, subtitle};
      this.context = {...this.context, effective_rules: {...this.context.effective_rules, naming_rule: naming, subtitle_rule: subtitle}};
    }
    async applyRules() {
      if (!this.context) return;
      this.rememberRules(); this.contextChanged();
      const epoch = this.epoch;
      this.invalidateRename(); this.invalidateContent(); await this.naming(); if (epoch === this.epoch) await this.generateAll();
    }
    async search() {
      const query = this.el("query").value.trim(); if (!query) throw new Error("请输入作品名称"); const current = this.stamp("search"), text = query;
      const result = await this.api.search(query, this.el("media-type").value); if (!current() || text !== this.el("query").value.trim()) return;
      const container = this.el("candidates"); container.replaceChildren();
      if(result.warnings?.length) container.append(node('p',result.warnings.map(x=>x.message).join('；'),'wb-note'));
      if (!result.items.length) container.append(node("p", "未找到候选，可以调整名称后重试。", "wb-note"));
      result.items.forEach(candidate => {
        const button = node("button", null, 'wb-candidate');
        const info=node('span',null,'wb-candidate-copy');info.append(node('strong',candidate.title||candidate.name||candidate.id),node('small',[candidate.original_title,candidate.year,({movie:'电影',series:'剧集',anime:'动画'})[candidate.media_type],candidate.provider?`${candidate.provider.toUpperCase()} #${candidate.id}`:null].filter(Boolean).join(' · ')));button.append(info);
        const poster=safeURL(candidate.poster_url);if(candidate.poster_url&&poster){const image=node('img');image.alt='';image.src=poster;image.referrerPolicy='no-referrer';image.onerror=()=>image.remove();button.prepend(image);}
        button.type = "button"; button.disabled = !this.resources.length;
        button.onclick = () => this.withButton(button, async () => { const epoch = this.epoch; await this.resolve({provider: candidate.provider, id: String(candidate.id), media_type: candidate.media_type}); if (epoch === this.epoch && this.context?.identity) this.openPanel(null); }); container.append(button);
      });
    }
    async searchInline() {
      const query = this.el("query-inline").value.trim(); if (!query) throw new Error("请输入影视名称"); const current = this.stamp("search-inline"), text = query;
      const result = await this.api.search(query, this.el("media-type-inline").value); if (!current() || text !== this.el("query-inline").value.trim()) return;
      const container = this.el("candidates-inline"); container.replaceChildren();
      if(result.warnings?.length) container.append(node('p',result.warnings.map(x=>x.message).join('；'),'wb-note'));
      if (!result.items.length) container.append(node("p", "未找到候选，可以调整名称后重试。", "wb-note"));
      result.items.forEach(candidate => {
        const button = node("button", null, 'wb-candidate');
        const info=node('span',null,'wb-candidate-copy');info.append(node('strong',candidate.title||candidate.name||candidate.id),node('small',[candidate.original_title,candidate.year,({movie:'电影',series:'剧集',anime:'动画'})[candidate.media_type],candidate.provider?`${candidate.provider.toUpperCase()} #${candidate.id}`:null].filter(Boolean).join(' · ')));button.append(info);
        const poster=safeURL(candidate.poster_url);if(candidate.poster_url&&poster){const image=node('img');image.alt='';image.src=poster;image.referrerPolicy='no-referrer';image.onerror=()=>image.remove();button.prepend(image);}
        button.type = "button"; button.disabled = !this.resources.length;
        button.onclick = () => this.withButton(button, async () => { const epoch = this.epoch; await this.resolve({provider: candidate.provider, id: String(candidate.id), media_type: candidate.media_type}); if (epoch === this.epoch && this.context?.identity) this.openPanel(null); });
        container.append(button);
      });
    }
    setField(name, value) {
      this.fields[name] = String(value ?? ""); this.host.querySelector(`[data-edit="${name}"]`).value = this.fields[name]; this.host.querySelector(`[data-lock="${name}"]`).textContent = this.dirty.has(name) ? "人工编辑 · 自动识别会保留此内容" : ""; this.controls();
    }
    async generateAll() {
      const epoch = this.epoch;
      const results = await Promise.allSettled([this.generate("release_title"), this.generate("subtitle")]);
      if (epoch !== this.epoch || this.destroyed) return;
      results.forEach(result => { if (result.status === "rejected") this.notice(result.reason.message, true); }); this.scheduleCompose();
    }
    async generate(name, explicit = false) {
      if (!this.context || (!explicit && this.dirty.has(name))) return;
      if (explicit && this.dirty.has(name)) {
        // Explicit per-field replacement, confirmed inline without a modal.
        const confirmKey = `replace-${name}`;
        if (!this.pendingReplacement || this.pendingReplacement !== confirmKey) { this.pendingReplacement = confirmKey; this.notice("此区域有人工编辑。再次点击生成将替换该区域内容。"); return; }
        this.pendingReplacement = null;
      }
      const current = this.stamp(name), revision = this.context.revision, edit = this.fields[name], fieldVersion = this.fieldVersions[name] || 0, wasDirty = this.dirty.has(name);
      const result = name === "release_title" ? await this.api.title(this.context, this.readRule("title-rule")) : await this.api.subtitle(this.context, this.readRule("subtitle-rule", this.context.effective_rules?.subtitle_rule || {}));
      if (!current() || this.context?.revision !== revision || result.context_revision !== revision || this.fields[name] !== edit || fieldVersion !== (this.fieldVersions[name] || 0) || (!wasDirty && this.dirty.has(name))) return;
      this.dirty.delete(name); this.setField(name, result.text); this.editVersion++; this.scheduleCompose();
      const warning = this.host.querySelector(`[data-lock="${name}"]`);
      if (result.warnings?.length) warning.textContent = result.warnings.map(x => x.message).join("；");
    }
    scheduleCompose() {
      if (this.destroyed) return;
      this.cancelTimer(this.composeTimer); this.invalidateContent(); if (!this.context) return;
      this.composeTimer = this.later(() => this.compose(), 220);
    }
    async compose() {
      if (!this.context) return; const current = this.stamp("compose"), edit = this.editVersion, revision = this.context.revision;
      try {
        const format = this.el("format").value;
        const result = await this.api.compose({context: this.context, ...this.fields, poster: val(this.context.metadata?.poster_url) && safeURL(val(this.context.metadata?.poster_url)) ? {kind:'upload',id:'identity-poster',url:safeURL(val(this.context.metadata.poster_url))} : null, reports: this.artifacts.filter(x => x.kind === "report"), screenshots: this.artifacts.filter(x => ["screenshot", "collage", "upload"].includes(x.kind) && /^https?:\/\//i.test(x.url || "") && safeURL(x.url)), links: this.links, format});
        if (!current() || edit !== this.editVersion || revision !== this.context?.revision || result.context_revision !== revision) return;
        const localImages=this.el('local-images-preview');localImages.replaceChildren();
        const unpublished=this.images.length && !this.artifacts.some(x=>x.kind==='upload' && safeURL(x.url));
        localImages.hidden=!unpublished;
        if(unpublished){
          localImages.append(node('p','缩略图仅本地可见。生成图床链接后才会加入可复制的发布内容。','wb-note'));
          for(const image of this.images){const img=node('img');img.src='/api/files/image?'+new URLSearchParams({root_key:'images',path:image.path});img.alt=image.path;localImages.append(img);}
        }
        this.content = result.content; this.el("output").textContent = result.content;
        this.el("rich-preview").hidden = format === "html";
        if (format !== "html") global.BiliWorkbenchPreview?.render(this.el("rich-preview"), result.content, format);
        const messages = [...(result.validation.errors || []), ...(result.validation.warnings || []), ...(result.validation.notices || [])].map(x => typeof x === "string" ? x : x.message);
        this.el("validation").textContent = (result.validation.ready ? "发布内容已就绪" : "内容尚有缺项，可预览复制") + (messages.length ? ` · ${messages.join("；")}` : "") + (unpublished ? " · 缩略图待上传图床" : "");
        if (format === "html") { this.el("html-preview").srcdoc = '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src https: http:; style-src &#39;unsafe-inline&#39;"><style>body{font:14px/1.6 system-ui;overflow-wrap:anywhere;color-scheme:light dark;color:CanvasText;background:Canvas}img{max-width:100%}pre{white-space:pre-wrap}</style>' + result.content; this.el("html-preview").hidden = false; }
        this.controls();
      } catch (error) { if (current() && edit === this.editVersion) { this.el("validation").textContent = error.message; this.notice(error.message, true); } }
    }
    showAdvancedPreview(result) {
      if (!result) { if (this.advancedPreview) this.invalidateRename(); return; }
      // Display the actual advanced result; execution/undo remains owned by that
      // component, with its original token, confirmation and task scope guards.
      this.preview = null; this.advancedPreview = true;
      this.el("rename-list").replaceChildren();
      for (const op of result.operations || []) {
        const row = node("div", null, "wb-rename-row"); row.append(node("span", op.source.relative_path), node("strong", `→ ${op.target_relative_path}`)); this.el("rename-list").append(row);
      }
      this.el("rename-warning").textContent = [result.undo ? "撤销预览：在命名详情中确认执行。" : "高级命名预览：在命名详情中确认执行。", ...(result.conflicts || []).map(x=>x.message)].join("；");
      this.el("rename").hidden = false; this.renderRenameMetrics(result); this.controls();
    }
    newKey() {
      if (global.crypto?.randomUUID) return global.crypto.randomUUID();
      const bytes = global.crypto?.getRandomValues ? global.crypto.getRandomValues(new Uint8Array(24)) : null;
      return bytes ? Array.from(bytes, x => x.toString(16).padStart(2, "0")).join("") : `naming-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
    renderPreview(preview) {
      this.el("rename-list").replaceChildren();
      const files = new Map((this.context?.files || []).map(file => [file.ref.relative_path, file]));
      const editable = !preview.undo && this.context?.episode?.scope !== "single";
      for (const op of preview.operations || []) {
        const row = node("div", null, "wb-rename-row");
        row.append(node("span", op.source.relative_path, "wb-rename-source"), node("strong", `→ ${op.target_relative_path}`, "wb-rename-target"));
        if (editable && !op.sidecar_of) {
          const path = op.source.relative_path;
          const file = files.get(path);
          const label = node("label", null, "wb-rename-episode"); label.append(node("span", "集号"));
          const input = node("input"); input.type = "number"; input.min = "1"; input.step = "1";
          input.value = Object.prototype.hasOwnProperty.call(this.episodeOverrides, path) ? this.episodeOverrides[path] : val(file?.episode?.episode);
          input.addEventListener("input", () => {
            const raw = input.value.trim();
            if (raw === "") delete this.episodeOverrides[path];
            else {
              const number = Number(raw);
              if (!Number.isInteger(number) || number < 1) { this.notice("集号必须是正整数", true); return; }
              this.episodeOverrides[path] = number;
            }
            this.scheduleNamingOverride();
          });
          label.append(input); row.append(label);
        }
        this.el("rename-list").append(row);
      }
      const undo = !!preview.undo;
      const conflicts = (preview.conflicts || []).map(x => x.message).join("；");
      this.el("rename-warning").textContent = (undo ? "撤销预览：确认后将恢复最近一次命名。" : "") + (conflicts || (undo ? "" : "请核对全部目标路径。确认后由服务端复核指纹与冲突，再执行原地改名。"));
      this.el("rename").hidden = false; this.renderRenameMetrics(); this.controls();
    }
    async naming() {
      if (!this.context) return;
      const current = this.stamp("naming"), revision = this.context.revision;
      const preview = await this.api.naming(this.context, this.namingOverrides(), this.readRule("naming-rule", this.context.effective_rules?.naming_rule || {}));
      if (!current() || this.context?.revision !== revision || preview.context_revision < revision) return;
      this.context = {...this.context, revision: preview.context_revision}; this.contextChanged();
      this.invalidateContent(); this.scheduleCompose();
      this.preview = {...preview, undo: false}; this.previewKey = this.newKey(); this.previewUndo = false;
      this.renderPreview(this.preview);
      const token = preview.preview_token, delay = Date.parse(preview.expires_at) - Date.now();
      if (Number.isFinite(delay) && delay > 0) this.later(() => { if (this.preview?.preview_token === token) { this.invalidateRename(); this.notice("命名预览已过期，请重新预览"); } }, Math.min(delay, 2147483647));
    }
    async showUndoPreview() {
      if (!this.renameBatch) throw new Error("还没有可撤销的命名批次，请先执行一次重命名。");
      const current = this.stamp("undo");
      const preview = await this.api.undoPreview(this.renameBatch);
      if (!current()) return;
      this.preview = {...preview, undo: true}; this.previewKey = this.newKey(); this.previewUndo = true;
      this.renderPreview(this.preview);
      const token = preview.preview_token, delay = Date.parse(preview.expires_at) - Date.now();
      if (Number.isFinite(delay) && delay > 0) this.later(() => { if (this.preview?.preview_token === token) { this.invalidateRename(); this.notice("撤销预览已过期，请重新预览"); } }, Math.min(delay, 2147483647));
    }
    async applyRename() {
      if (!this.applyable()) throw new Error(this.preview?.undo ? "撤销预览已失效，请重新预览" : "命名预览已失效，请重新预览");
      const preview = this.preview, epoch = this.epoch, resources = this.resources;
      const response = this.previewUndo ? await this.api.undoApply(preview, this.previewKey) : await this.api.apply(preview, this.previewKey);
      this.track(response.task, {type: "rename", epoch, resources, operations: preview.operations, undo: this.previewUndo});
      if (epoch === this.epoch && response.batch_id) {
        this.renameBatch = response.batch_id;
        this.host.dispatchEvent(new CustomEvent("v2:naming-accepted", {detail: {batchId: response.batch_id}}));
      }
      if (this.preview === preview) this.invalidateRename();
    }
    async probe() {
      const epoch = this.epoch, response = await this.api.probe({resources: this.resources, expected_fingerprints: Object.fromEntries(this.resources.map(x => [`${x.root_key}/${x.relative_path}`, x.fingerprint]))});
      this.track(response.task, {type: "probe", epoch});
    }
    sources() { return this.resources.map(({root_key, relative_path}) => ({root_key, relative_path})); }
    async screenshots() {
      const total = Number(this.el("image-count").value), columns = Number(this.el("image-columns").value);
      if (!Number.isInteger(total) || total < 1 || total > 120 || !Number.isInteger(columns) || columns < 1 || columns > 10) throw new Error("张数需在 1–120 之间，列数需在 1–10 之间");
      const epoch = this.epoch, skipStart = Number(this.el("skip-start").value), skipEnd = Number(this.el("skip-end").value);
      if (![skipStart, skipEnd].every(value => Number.isFinite(value) && value >= 0 && value <= 86400)) throw new Error("跳过时长必须在 0–86400 秒之间");
      const extra={};
      const rows=this.el('image-rows').value;if(rows){if(!Number.isInteger(Number(rows))||Number(rows)<1||Number(rows)>12)throw new Error('行数需在 1–12 之间');extra.rows=Number(rows);}
      if(this.el('range-mode').value==='percent'){const start=Number(this.el('start-percent').value),end=Number(this.el('end-percent').value);if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>100||start>=end)throw new Error('百分比范围需要满足 0 ≤ 开始 < 结束 ≤ 100');extra.start_percent=start;extra.end_percent=end;}
      if(this.el('image-format').value)extra.format=this.el('image-format').value;
      if(this.el('image-style').value)extra.style=this.el('image-style').value;
      const result = await this.api.request("/api/files/screenshots", {...extra, sources: this.sources(), total, columns, output_mode: this.el("image-mode").value, auto_upload: false, content_type: this.context?.identity?.media_type || "custom", skip_start_seconds: skipStart, skip_end_seconds: skipEnd});
      this.track(result.task, {type: "screenshot", session: result.session_id, epoch});
    }
    async report() {
      const epoch=this.epoch, source=this.resources[0];
      const result=await this.api.request('/api/v2/reports',source);
      if (epoch === this.epoch) this.el('reports').textContent='正在后台生成媒体报告…';
      this.track(result.task,{type:'report',epoch});
    }
    async torrent() {
      const source = this.resources[0], epoch = this.epoch;
      const trackers = this.el("trackers").value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      const result = await this.api.request("/api/torrents/jobs", {source_root_key: source.root_key, source_relative_path: source.relative_path, private: this.el("private").checked, overwrite: false, ...(this.el("piece-length").value ? {piece_length:this.el("piece-length").value} : {}), ...(trackers.length ? {trackers} : {})});
      this.track(result.task, {type: "torrent", epoch});
    }
    async upload() {
      if (this.images.length > 50) throw new Error("每次最多上传 50 张图片，请减少截取数量");
      const epoch = this.epoch, result = await this.api.request("/api/uploads/jobs", {items: this.images.map(x => ({root_key: "images", relative_path: x.path}))});
      this.track(result.task, {type: "upload", epoch});
    }
    track(task, job) {
      const normalized = task.kind ? task : this.api.legacyTask(task); this.tasks.set(key(normalized), normalized); this.jobs.set(key(normalized), job); this.renderTasks();
      this.notice("任务已提交，完成后会通知"); this.refreshTasks();
    }
    async refreshTasks(initial = false) {
      if (this.refreshing || this.destroyed) return; this.refreshing = true; this.cancelTimer(this.pollTimer);
      try {
        const tasks = await this.api.tasks(); if (this.destroyed) return;
        const listed = new Set(tasks.map(key));
        for (const id of this.jobs.keys()) {
          const previous = this.tasks.get(id);
          if (!listed.has(id) && previous && !terminal(previous)) {
            const detail = await this.api.request(previous.legacy ? `/api/tasks/${previous.id}` : `/api/v2/tasks/${previous.kind}/${previous.id}`);
            tasks.push(previous.legacy ? this.api.legacyTask(detail) : detail);
          }
        }
        for (const task of tasks) {
          const id = key(task), previous = this.tasks.get(id); this.tasks.set(id, task);
          if (terminal(task)) {
            const notification = `${id}:${task.status}:${task.finished_at || ""}`;
            if (!global.AppTaskCenter && !initial && !this.notified.has(notification) && (this.jobs.has(id) || (previous && !terminal(previous)))) { this.notice(`${task.task_type}：${{completed: "已完成", failed: "失败", cancelled: "已取消"}[task.status]}${task.error ? ` · ${task.error}` : ""}`, task.status === "failed"); }
            this.notified.add(notification);
            if (task.status === "completed" && this.jobs.has(id) && !this.applied.has(id)) { try { await this.applyTask(task, this.jobs.get(id)); this.applied.add(id); } catch (error) { this.notice(`产物读取失败：${error.message}`, true); } }
          }
        }
        this.el("task-status").textContent = this.api.tasksMode === "legacy" ? "已连接现有工具队列；统一下载任务视图待接入。刷新仅恢复任务，不恢复发布内容。" : "刷新仅恢复任务进度和实际产物，不恢复发布内容。";
        this.renderTasks();
      } catch (error) { if (!this.destroyed) this.el("task-status").textContent = `${error.message}；稍后自动重试`; }
      finally { this.refreshing = false; if (!this.destroyed) this.pollTimer = this.later(() => this.refreshTasks(), this.pollInterval); }
    }
    async applyTask(task, job) {
      if (job.epoch !== this.epoch) return;
      if (job.type === "rename") {
        // An in-place rename keeps task ownership, but invalidates every outstanding
        // read/preview. Increment existing counters rather than reusing old versions.
        for (const channel of Object.keys(this.versions)) this.versions[channel]++;
        this.selectionVersion++; this.selectionSignature = null;
        this.context = null; this.resources = []; this.contextChanged("rename"); this.invalidateRename(); this.invalidateContent();
        this.el("issues").textContent = "重命名已完成，正在核对新路径与指纹…";
        const current = this.stamp("renamed");
        try {
          const resources = [];
          for (const source of job.resources) {
            const operation = job.operations.find(item => item.source.root_key === source.root_key && item.source.relative_path === source.relative_path);
            const path = operation?.target_relative_path || source.relative_path;
            const item = await this.api.request("/api/files/meta?" + new URLSearchParams({root_key: source.root_key, path}));
            if (!current()) return;
            resources.push(ref({...item, root_key: source.root_key}));
          }
          if (!current()) return;
          this.resources = resources; this.probedResources = null;
          this.el("resource").textContent = `${resources.length} 项资源 · ${resources[0].relative_path.split("/").at(-1)}`;
          await this.browser?.loadDirectory({clearSelection: true});
          if (current()) {
            await this.resolve();
            if (!current()) return;
            this.host.dispatchEvent(new CustomEvent('v2:renamed'));
            document.dispatchEvent(new CustomEvent('app:files-refresh'));
          }
        } catch (error) {
          if (current()) { this.el("issues").textContent = `重命名已完成，重新识别未完成：${error.message}。可重新选择资源后继续。`; this.notice(this.el("issues").textContent, true); }
        }
        return;
      }
      if (job.type === "probe") { await this.resolve(); return; }
      let detail = task;
      if (!task.result) detail = await this.api.request(task.legacy ? `/api/tasks/${task.id}` : `/api/v2/tasks/${task.kind}/${task.id}`);
      if (job.epoch !== this.epoch) return;
      if (job.type === "screenshot") {
        const session = await this.api.request(`/api/files/screenshots/${encodeURIComponent(job.session)}`); if (job.epoch !== this.epoch) return;
        const outputs = Object.values(session.final_outputs || {});
        const selected = outputs.length ? outputs : (session.videos || []).flatMap(v => v.items || []);
        this.images = selected.filter(x => x.status === "completed" && x.available !== false).map(x => ({id: String(x.id || x.video_id), path: x.output_relative_path || x.relative_path, kind: session.params?.output_mode === "collage" ? "collage" : "screenshot"})).filter(x => x.path);
        this.artifacts = this.artifacts.filter(x => !["screenshot", "collage", "upload"].includes(x.kind)); this.links = []; this.el("upload-links").replaceChildren();
        if (!this.images.length) throw new Error("任务未返回可读取的截图，请检查任务日志");
        this.renderImages();
      }
      if (job.type === "upload") {
        const uploads = detail.result?.uploads || [];
        const completed = uploads.filter(x => x.status === "completed" && safeURL(x.remote_url));
        this.links = [...new Set([...this.links, ...completed.map(x => x.remote_url)])];
        this.artifacts = [...this.artifacts.filter(x => x.kind !== "upload"), ...completed.map(x => ({kind: "upload", id: String(x.id), resource: null, url: x.remote_url}))];
        this.renderUploadLinks();
        if (completed.length < uploads.length) this.notice("部分图片上传失败，请检查任务日志后重试", true);
      }
      if (detail.artifact_refs?.length) {
        const references = detail.artifact_refs.filter(artifact => artifact && ["screenshot", "collage", "report", "torrent", "upload"].includes(artifact.kind) && typeof artifact.id === "string" && (!artifact.url || safeURL(artifact.url)));
        this.artifacts = [...this.artifacts, ...references.filter(artifact => !this.artifacts.some(x => x.kind === artifact.kind && x.id === artifact.id))];
        this.links = [...new Set([...this.links, ...references.filter(artifact => artifact.kind === "upload" && artifact.url).map(artifact => artifact.url)])];
        this.renderUploadLinks();
      }
      if (job.type === "report") {
        this.el('reports').replaceChildren();
        for (const artifact of detail.artifact_refs || []) {
          if (artifact.kind !== 'report' || !artifact.resource) continue;
          const report=await this.api.request('/api/files/text?'+new URLSearchParams({root_key:artifact.resource.root_key,path:artifact.resource.relative_path}));
          if (job.epoch !== this.epoch) return;
          const text=report.content || report.text || '', copy=node('button','复制报告'), download=node('a','下载报告');
          copy.type='button'; copy.onclick=()=>this.copyValue(text);
          download.href=(artifact.url && safeURL(artifact.url)) || `/api/v2/reports/${encodeURIComponent(artifact.id)}/file`;
          download.setAttribute('download','');
          const card=node('div',null,'wb-report-card');
          card.append(copy,download,node('pre',text));
          this.el('reports').append(card);
        }
      }
      if (job.type === "torrent") {
        if (detail.result?.record) this.renderTorrent(detail.result.record);
        else for (const artifact of detail.artifact_refs || []) if (artifact.kind === 'torrent') this.renderTorrent({id:artifact.id});
      }
      this.editVersion++; this.scheduleCompose(); this.controls();
    }
    renderUploadLinks() {
      this.el("upload-links").replaceChildren();
      this.links.forEach(url => {
        const row = node("div", null, "wb-upload-link"), link = node("a", url);
        link.href = safeURL(url); link.target = "_blank"; link.rel = "noopener noreferrer";
        const copyUrl = node("button", "复制链接"); copyUrl.type = "button"; copyUrl.onclick = () => this.copyValue(url);
        const copyBBCode = node("button", "复制 BBCode"); copyBBCode.type = "button"; copyBBCode.onclick = () => this.copyValue(`[img]${url}[/img]`);
        row.append(link, copyUrl, copyBBCode); this.el("upload-links").append(row);
      });
    }
    renderImages() {
      this.el("images").replaceChildren();
      this.images.forEach(image => {
        const url = "/api/files/image?" + new URLSearchParams({root_key: "images", path: image.path});
        const figure = node("figure", null, "wb-image"), img = node("img"), caption = node("figcaption"), download = node("a", "下载缩略图"); img.src = url; img.alt = image.path; img.loading = "lazy"; download.href = `${url}&download=true`; caption.append(download); figure.append(img, caption); this.el("images").append(figure);
      });
    }
    renderTorrent(record) {
      const card = node("article", null, "wb-task"), link = node("a", "下载种子");
      link.href = `/api/torrents/${encodeURIComponent(record.id)}/file`;
      card.append(node("strong", record.name || record.filename || ".torrent"), node("p", record.info_hash || ""), link);
      this.el("torrents").append(card);
    }
    renderTasks() {
      const signature = JSON.stringify([...this.tasks.values()].map(task => [key(task), task.status, task.progress, task.error, task.cancel_allowed, task.retry_allowed]));
      if (signature === this.taskSignature) return;
      this.taskSignature = signature;
      this.el("task-count").textContent = String([...this.tasks.values()].filter(task => !terminal(task)).length); this.el("tasks").replaceChildren();
      [...this.tasks.values()].slice(0, 30).forEach(task => {
        const card = node("article", null, "wb-task"); card.append(node("strong", `${task.task_type} · ${{queued: "排队中", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", auth_required: "需要登录"}[task.status] || task.status}`));
        const progress = node("progress"); progress.max = 100; if (typeof task.progress === "number" && Number.isFinite(task.progress)) progress.value = Math.max(0, Math.min(100, task.progress)); else if (task.status === "completed") progress.value = 100; progress.setAttribute("aria-label", "任务进度"); card.append(progress);
        if (task.error) card.append(node("p", task.error, "wb-note"));
        const actions = node("div", null, "wb-task-actions");
        for (const [action, label, allowed] of [["cancel", "取消", task.cancel_allowed], ["retry", "重试", task.retry_allowed && !/rename/.test(task.task_type)]]) if (allowed) {
          const button = node("button", label); button.type = "button"; button.onclick = () => this.withButton(button, async () => { const result = await this.api.taskAction(task, action); this.tasks.set(key(result), result); if (action === "retry") this.applied.delete(key(result)); this.renderTasks(); await this.refreshTasks(); }); actions.append(button);
        }
        if (task.status === "completed" && ["screenshot", "screenshot_collage", "image_upload", "torrent_build"].includes(task.task_type)) {
          const show = node("button", "查看产物"); show.type = "button";
          show.onclick = () => this.withButton(show, () => this.showTaskArtifacts(task, card)); actions.append(show);
        }
        const logs = node("button", "日志"); logs.type = "button";
        logs.onclick = () => this.withButton(logs, async () => {
          const result = await this.api.request(task.kind === "download" ? `/api/jobs/${task.id}/log?lines=100` : `/api/tasks/${task.id}/logs?lines=100`);
          let pre = card.querySelector("pre"); if (!pre) { pre = node("pre"); card.append(pre); }
          const lines = result.content || result.lines || result.logs || result.log || "暂无日志"; pre.textContent = Array.isArray(lines) ? lines.map(x => typeof x === "string" ? x : x.message || JSON.stringify(x)).join("\n") : String(lines);
        }); actions.append(logs); card.append(actions); this.el("tasks").append(card);
      });
    }
    async showTaskArtifacts(task, card) {
      card.querySelector(".wb-task-artifacts")?.remove();
      const output = node("div", null, "wb-task-artifacts");
      const detail = await this.api.request(task.legacy ? `/api/tasks/${task.id}` : `/api/v2/tasks/${task.kind}/${task.id}`);
      if (this.destroyed || !card.isConnected) return;
      if (task.task_type === "torrent_build" && detail.result?.record?.id) {
        const download = node("a", "下载种子"); download.href = `/api/torrents/${encodeURIComponent(detail.result.record.id)}/file`; output.append(download);
      }
      if (["screenshot", "screenshot_collage"].includes(task.task_type) && detail.input?.session_id) {
        const session = await this.api.request(`/api/files/screenshots/${encodeURIComponent(detail.input.session_id)}`);
        const final = Object.values(session.final_outputs || {});
        for (const item of final.length ? final : (session.videos || []).flatMap(video => video.items || [])) {
          const path = item.output_relative_path || item.relative_path;
          if (item.status !== "completed" || !path || item.available === false) continue;
          const link = node("a", "下载缩略图"); link.href = "/api/files/image?" + new URLSearchParams({root_key: "images", path, download: "true"}); output.append(link);
        }
      }
      for (const upload of detail.result?.uploads || []) {
        const url = safeURL(upload.remote_url); if (upload.status !== "completed" || !url) continue;
        const link = node("a", "图床链接"); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; output.append(link);
      }
      for (const artifact of detail.artifact_refs || []) {
        const url = artifact.url && safeURL(artifact.url);
        if (url) { const link = node("a", ({torrent:"下载种子",report:"下载报告",screenshot:"下载截图",collage:"下载缩略图",upload:"图床链接"})[artifact.kind] || `${artifact.kind} · ${artifact.id}`); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; output.append(link); }
      }
      if (!output.childNodes.length) output.textContent = "服务尚未提供可读取的产物引用，请查看任务日志。";
      card.append(output);
    }
    async withButton(button, action) { if (button.disabled) return; button.disabled = true; try { await action(); } catch (error) { this.notice(error.message, true); } finally { if (button.isConnected) button.disabled = false; } }
    async copy(name) { await this.copyValue(name === "content" ? this.content : this.fields[name]); }
    async copyValue(text) {
      if (!text) return; try { await navigator.clipboard.writeText(text); this.notice("已复制"); }
      catch { const input = node("textarea"); input.value = text; input.style.position = "fixed"; input.style.opacity = "0"; this.host.append(input); input.select(); const ok = document.execCommand("copy"); input.remove(); this.notice(ok ? "已复制" : "无法访问剪贴板，请手动选择文本复制", !ok); }
    }
    destroy() { this.destroyed = true; this.epoch++; this.abort.abort(); this.timers.forEach(clearTimeout); this.timers.clear(); this.browser?.destroy(); this.context = null; this.resources = []; this.fields = {release_title: "", subtitle: "", overview: ""}; this.content = ""; this.tasks.clear(); this.jobs.clear(); }
  }
  global.BiliWorkbench = {mount: async (host, options) => { instances.get(host)?.destroy(); const app = new Workbench(host, options); instances.set(host, app); return app.init(); }, Workbench, resourceRef: ref};
})(window);
