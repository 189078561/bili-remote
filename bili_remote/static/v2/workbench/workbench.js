(function (global) {
  "use strict";
  const instances = new WeakMap();
  const terminal = task => ["completed", "failed", "cancelled"].includes(task.status);
  const taskLabels = {queued: "排队中", running: "运行中", completed: "已完成", failed: "失败", cancelled: "已取消", auth_required: "需要登录"};
  const tools = {screenshots: ["screenshot", "images"], report: ["report", "report"], bdinfo: ["bdinfo", "bdinfo"], torrent: ["torrent", "torrent"], upload: ["upload", "upload"]};
  const toolTypes = {screenshot:"screenshot", report:"v2_media_report", bdinfo:"v2_media_report", torrent:"torrent_build", upload:"image_upload"};
  const receiptKey = "bili:workbench:tool-receipts:v1";
  const val = field => field?.value ?? "";
  const key = task => `${task.kind}:${task.id}`;
  const node = (tag, text, className) => { const el = document.createElement(tag); if (text != null) el.textContent = text; if (className) el.className = className; return el; };
  const parentPath = value => { const parts = String(value || "").split("/").filter(part => part && part !== "." && part !== ".."); parts.pop(); return parts.join("/"); };
  const resourceKind = item => item?.is_dir || item?.kind === "directory" ? "目录" : ({video:"视频", image:"图片", torrent:"种子", text:"文本"}[item?.kind] || "文件");
  function safeURL(value, local = false) {
    try { const url = new URL(value, global.location.href); return ["http:", "https:"].includes(url.protocol) && (!local || url.origin === global.location.origin) && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  const signature = (resources, fingerprints = true) => JSON.stringify(resources.map(item => [item.root_key, item.relative_path, item.kind, ...(fingerprints ? [item.fingerprint] : [])]).sort());
  const ref = item => {
    if (!["downloads", "torrents", "images", "release_info"].includes(item.root_key) || !item.relative_path || item.relative_path.startsWith("/") || item.relative_path.includes("\\") || item.relative_path.split("/").some(x => ["..", ".", ""].includes(x)) || item.relative_path.includes("\0") || !item.fingerprint) throw new Error("资源引用无效，请在文件浏览器中重新选择");
    return {root_key: item.root_key, relative_path: item.relative_path, kind: item.is_dir || item.kind === "directory" ? "directory" : "file", fingerprint: item.fingerprint};
  };
  const formatSize = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KiB` : n < 1073741824 ? `${(n / 1048576).toFixed(1)} MiB` : `${(n / 1073741824).toFixed(1)} GiB`;
  // Workbench-only selection surface. The existing Files API remains the
  // authority for allowed paths, metadata, pagination and search.
  class MediaPicker {
    constructor(host, api, {choose, change, error}) {
      this.host = host; this.api = api; this.choose = choose; this.change = change; this.error = error;
      this.roots = []; this.rootKey = null; this.path = ""; this.page = 1; this.ticket = 0;
      this.checked = new Map(); this.abort = new AbortController();
      this.crumbs = node("nav", null, "wb-picker-path");
      this.crumbs.setAttribute("aria-label", "当前位置");
      this.list = node("div", null, "wb-picker-list");
      this.status = node("p", null, "wb-note");
      this.status.setAttribute("role", "status");
      const toolbar = node("div", null, "wb-picker-toolbar"), footer = node("div", null, "wb-picker-tools");
      this.up = this.button("返回上级", () => this.navigate(this.rootKey, parentPath(this.path)));
      this.up.dataset.resourceAction = "up";
      this.refresh = this.button("刷新", () => this.loadDirectory({page: this.page, clearSelection: false}));
      this.search = node("input"); this.search.type = "search"; this.search.setAttribute("aria-label", "搜索当前目录");
      this.search.placeholder = "搜索当前目录";
      this.search.addEventListener("input", () => {
        clearTimeout(this.debounce);
        // In-flight old searches must not redraw during the debounce window.
        this.ticket++; this.highlight = null; this.blockList();
        this.debounce = setTimeout(() => this.loadDirectory(), 180);
      }, {signal:this.abort.signal});
      toolbar.append(this.up, this.refresh, this.search);
      this.open = this.button("打开目录", () => this.highlight && this.activate(this.highlight));
      this.open.dataset.resourceAction = "open";
      this.use = this.button("使用文件", () => this.highlight && this.commit([this.highlight]));
      this.use.dataset.resourceAction = "select";
      this.useDir = this.button("使用当前目录", () => this.commitDirectory());
      this.commitChecked = this.button("使用所选资源", () => this.commit([...this.checked.values()]));
      this.prev = this.button("上一页", () => this.loadDirectory({page:this.page-1, clearSelection:false}));
      this.next = this.button("下一页", () => this.loadDirectory({page:this.page+1, clearSelection:false}));
      this.pager = node("div", null, "wb-picker-tools");
      this.pager.append(this.prev, this.next);
      footer.append(this.open, this.use, this.useDir, this.commitChecked);
      host.replaceChildren(this.crumbs, toolbar, this.status, this.list, this.pager, footer);
      this.sync();
    }
    button(label, fn) {
      const button = node("button", label);
      button.type = "button";
      button.onclick = async () => { try { await fn(); } catch (error) { if (!this.abort.signal.aborted) this.error(error); } };
      return button;
    }
    selectedItems() { return [...this.checked.values()]; }
    root(key = this.rootKey) { return this.roots.find(root => root.key === key); }
    selectable(item) { return item?.allowed && (item.is_dir || item.kind === "video"); }
    async init() {
      try {
        const roots = await this.api.request("/api/files/roots?scope=browse");
        if (this.abort.signal.aborted) return;
        this.roots = roots.filter(root => root.key === "downloads");
        const first = this.roots.find(root => root.available !== false);
        if (!first) throw new Error("资源下载目录不可用");
        await this.navigate(first.key, "");
      } catch (error) { if (!this.abort.signal.aborted) this.status.textContent = error.message; }
    }
    async navigate(rootKey, path) {
      if (!this.root(rootKey) || this.committing || this.abort.signal.aborted) return;
      clearTimeout(this.debounce);
      this.rootKey = rootKey; this.path = path;
      this.search.value = "";
      await this.loadDirectory();
    }
    blockList() {
      this.host.setAttribute("aria-busy", "true");
      this.status.textContent = "正在读取目录…";
      this.list.inert = true; this.sync();
    }
    async loadDirectory({page = 1, clearSelection = true} = {}) {
      if (!this.rootKey || this.abort.signal.aborted) return;
      clearTimeout(this.debounce);
      const ticket = ++this.ticket, root_key = this.rootKey, path = this.path, search = this.search.value.trim();
      const current = () => ticket === this.ticket && !this.abort.signal.aborted;
      this.directoryReady = false;
      if (clearSelection) this.checked.clear();
      this.highlight = null; this.highlightedRow = null;
      this.blockList(); this.renderNavigation(); this.notify();
      try {
        const query = new URLSearchParams({root_key, path, search, page, page_size:100, scope:"browse", sort:"natural"});
        const data = await this.api.request("/api/files/browse?" + query);
        if (!current()) return;
        this.page = data.page; this.hasMore = data.has_more; this.directoryReady = true;
        const items = data.items.map(item => ({...item, root_key}));
        // Revalidate visible staged refs against the refreshed listing.
        if (!clearSelection) for (const item of items) if (this.checked.has(item.relative_path)) {
          if (this.selectable(item)) this.checked.set(item.relative_path, item);
          else this.checked.delete(item.relative_path);
        }
        this.list.replaceChildren(...items.map(item => this.row(item)));
        this.list.classList.remove("wb-list-ready");
        this.status.textContent = !items.length ? (search ? "没有匹配的文件" : "当前目录为空") : `${data.total} 项 · 第 ${this.page} 页`;
        requestAnimationFrame(() => { if (current()) this.list.classList.add("wb-list-ready"); });
      } catch (error) {
        if (!current()) return;
        this.list.replaceChildren(); this.checked.clear(); this.hasMore = false; this.page = 1;
        this.status.textContent = error.message || "读取目录失败";
      } finally {
        if (current()) { this.host.removeAttribute("aria-busy"); this.list.inert = false; this.sync(); this.notify(); }
      }
    }
    renderNavigation() {
      const root = this.root(), parts = this.path.split("/").filter(Boolean);
      this.crumbs.replaceChildren(this.button(root.label, () => this.navigate(root.key, "")));
      parts.forEach((part, i) => {
        this.crumbs.append(node("span", "/"), this.button(part, () => this.navigate(root.key, parts.slice(0,i+1).join("/"))));
      });
    }
    row(item) {
      const row = node("div", null, "wb-picker-row file-row");
      row.dataset.path = item.relative_path; row.tabIndex = item.allowed ? 0 : -1;
      row.classList.toggle("blocked", !item.allowed);
      row.setAttribute("aria-label", item.name);
      const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      icon.setAttribute("class", "ws-icon"); icon.setAttribute("aria-hidden","true");
      const use = document.createElementNS(icon.namespaceURI,"use");
      use.setAttribute("href", "/static/v2/shared/icons.svg#" + (item.is_dir ? "folder" : item.kind === "video" ? "film" : "file"));
      icon.append(use);
      row.append(icon, node("span",item.name,"wb-picker-name"), node("small",item.is_dir || !Number.isFinite(item.size_bytes) ? "" : formatSize(item.size_bytes)));
      if (this.selectable(item)) {
        const check = node("input",null,"file-check"); check.type = "checkbox"; check.checked = this.checked.has(item.relative_path);
        check.setAttribute("aria-label", `加入选择：${item.name}`);
        check.onchange = () => {
          if (check.checked) this.checked.set(item.relative_path,item); else this.checked.delete(item.relative_path);
          row.classList.toggle("selected",check.checked); this.sync(); this.notify();
        };
        row.classList.toggle("selected",check.checked);
        row.append(check);
      }
      const highlight = () => {
        this.highlightedRow?.classList.remove("wb-file-active");
        this.highlightedRow?.removeAttribute("aria-current");
        this.highlight = item; this.highlightedRow = row;
        row.classList.add("wb-file-active"); row.setAttribute("aria-current","true");
        this.sync(); this.notify();
      };
      row.onclick = event => { if (item.allowed && event.target.tagName !== "INPUT") { row.focus({preventScroll:true}); highlight(); } };
      row.ondblclick = event => { if (item.allowed && event.target.tagName !== "INPUT") this.activate(item).catch(this.error); };
      row.onkeydown = event => {
        if (event.target !== row || !item.allowed || !["Enter"," "].includes(event.key)) return;
        event.preventDefault(); highlight();
        if (event.key === "Enter") this.activate(item).catch(this.error);
      };
      return row;
    }
    async activate(item) {
      if (this.list.inert || !item.allowed) return;
      if (item.is_dir) await this.navigate(item.root_key,item.relative_path);
      else if (this.selectable(item)) await this.commit([item]);
    }
    async commit(items) {
      if (!items.length || this.committing || this.list.inert || !this.directoryReady) return;
      this.committing = true; this.list.inert = true; this.sync();
      try {
        if (await this.choose(items)) {
          this.checked.clear();
          for (const check of this.list.querySelectorAll(".file-check:checked")) {
            check.checked = false; check.closest(".wb-picker-row").classList.remove("selected");
          }
          this.notify();
        }
      } finally { this.committing = false; this.list.inert = false; this.sync(); }
    }
    async commitDirectory() {
      if (!this.path || this.list.inert) return;
      const ticket = this.ticket, root_key = this.rootKey;
      const item = await this.api.request("/api/files/meta?" + new URLSearchParams({root_key, path:this.path}));
      if (!this.abort.signal.aborted && ticket === this.ticket && item.is_dir && item.allowed) await this.commit([{...item, root_key}]);
    }
    notify() {
      this.change([...this.checked.values()], this.highlight ? `已高亮：${this.highlight.name}` : "");
    }
    sync() {
      const busy = this.list.inert || this.committing;
      this.up.disabled = !this.path || this.committing;
      this.refresh.disabled = !this.rootKey || this.committing;
      this.search.disabled = this.committing;
      this.open.hidden = !this.highlight?.is_dir;
      this.open.disabled = busy;
      this.use.hidden = !this.selectable(this.highlight);
      this.use.disabled = busy;
      this.use.textContent = this.highlight?.is_dir ? "使用此文件夹" : "使用文件";
      this.useDir.disabled = busy || !this.path || !this.directoryReady;
      this.commitChecked.hidden = !this.checked.size;
      this.commitChecked.disabled = busy;
      this.pager.hidden = this.page === 1 && !this.hasMore;
      this.prev.disabled = busy || this.page <= 1; this.next.disabled = busy || !this.hasMore;
    }
    destroy() { clearTimeout(this.debounce); this.abort.abort(); this.ticket++; }
  }
  class Workbench {
    constructor(host, {api = new global.BiliWorkbenchAPI(), pollInterval = 2000} = {}) {
      if (!host) throw new Error("工作台缺少挂载节点");
      this.host = host; this.api = api; this.pollInterval = pollInterval;
      this.context = null; this.resources = []; this.pendingSelection = []; this.selectionSignature = null; this.selectionVersion = 0; this.origins = []; this.epoch = 0; this.versions = {}; this.dirty = new Set(); this.editVersion = 0;
      this.fields = {release_title: "", subtitle: "", overview: ""}; this.preview = null; this.previewKey = null; this.previewUndo = false; this.renameBatch = null; this.episodeOverrides = {}; this.episodeEdits = new Set(); this.content = ""; this.images = []; this.artifacts = []; this.links = [];
      this.fieldVersions = {}; this.tasks = new Map(); this.jobs = new Map(); this.notified = new Set(); this.applied = new Set(); this.pending = new Set(); this.timers = new Set(); this.abort = new AbortController(); this.destroyed = false;
      this.cancelPending = new Set(); this.stopAcks = new Set(); this.actionErrors = new Map();
      this.toolTasks = new Map(); this.toolErrors = new Map(); this.resultErrors = new Map();
      this.buttonLabels = new WeakMap(); this.copyFeedback = new WeakMap(); this.dialogState = new WeakMap(); this.torrentIds = new Set();
      this.listen();
    }
    el(name) { return this.host.querySelector(`[data-wb="${name}"]`); }
    button(action) { return this.host.querySelector(`[data-action="${action}"]`); }
    async init() {
      this.syncCollapses();
      // The embedded advanced naming module may also be used elsewhere;
      // suppress only its duplicate resource-picker entry in this host.
      const advanced = this.el("naming-panel");
      const hidePicker = () => {
        const pick = advanced.querySelector(".v2-naming__toolbar > button");
        if (pick) { pick.hidden = true; observer.disconnect(); }
      };
      const observer = new MutationObserver(hidePicker);
      observer.observe(advanced, {childList:true});
      this.abort.signal.addEventListener("abort", () => observer.disconnect(), {once:true});
      this.browser = new MediaPicker(this.el("browser"), this.api, {
        choose: items => this.chooseMedia(items),
        change: (items, hint) => this.stageSelection(items, hint),
        error: error => this.notice(error.message, true)
      });
      this.controls();
      await this.browser.init();
      await this.refreshTasks(true);
      return this;
    }
    async chooseMedia(items) {
      if (this.selecting) return false;
      this.selecting = true;
      try { return await this.select(items); }
      finally { this.selecting = false; }
    }
    listen() {
      const opts = {signal: this.abort.signal};
      for (const dialog of [this.el("image-dialog"), this.el("task-log-dialog")]) {
        let onBackdrop = false;
        dialog.addEventListener("pointerdown", event => { onBackdrop = event.target === dialog; }, opts);
        dialog.addEventListener("click", event => {
          if ((onBackdrop && event.target === dialog) || event.target.closest("[data-image-close],[data-log-close]")) this.closeDialog(dialog);
          onBackdrop = false;
        }, opts);
        dialog.addEventListener("cancel", event => { event.preventDefault(); this.closeDialog(dialog); }, opts);
      }
      this.host.addEventListener("click", event => {
        if (event.target.closest("[data-tool-settings]")) {
          const entry = document.getElementById("settings-entry");
          if (entry) { event.preventDefault(); entry.click(); }
          return;
        }
        const collapse = event.target.closest("[data-collapse]");
        if (collapse) { event.preventDefault(); this.setCollapse(collapse.dataset.collapse, collapse.getAttribute("aria-expanded") !== "true"); return; }
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
        const copy = event.target.closest("[data-copy]"); if (copy) this.copy(copy.dataset.copy, copy);
        const action = event.target.closest("[data-action]");
        if (action && !action.disabled) this.run(action.dataset.action, action);
      }, opts);
      this.host.addEventListener("input", event => {
        const name = event.target.dataset.edit;
        if (name) { this.fieldVersions[name] = (this.fieldVersions[name] || 0) + 1; this.dirty.add(name); this.setField(name, event.target.value); this.editVersion++; this.scheduleCompose(); }
        if (["season", "episode"].includes(event.target.dataset.wb)) this.episodeEdits.add(event.target.dataset.wb);
        if (["episode-numbering", "naming-rule", "subtitle-rule", "title-rule", "cast-limit", "selected-title", "season", "episode", "scope", "naming-type", "source-rule", "group-rule", "episode-mode", "start-episode", "episode-step", "episode-direction", "episode-offset"].includes(event.target.dataset.wb)) this.scheduleAdjustment(event.target.dataset.wb);
        if (["title-zh", "title-en", "title-combination"].includes(event.target.dataset.wb)) {
          this.nameEdits?.add(event.target.dataset.wb);
          this.el("selected-title").value = this.combinedTitle();
          this.scheduleAdjustment("selected-title");
        }
        if (["year", "resolution", "video-codec", "audio-codec"].includes(event.target.dataset.wb)) {
          this.nameEdits?.add(event.target.dataset.wb); this.scheduleNamingOverride();
        }
      }, opts);
      this.el("query").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.run("search", this.button("search")); } }, opts);
      this.host.addEventListener("v2:naming-edited", () => {
        this.cancelTimer(this.adjustmentTimer);
        this.cancelTimer(this.overrideTimer);
        this.invalidateRename();
      }, opts);
      this.el("format").addEventListener("change", () => { this.editVersion++; this.scheduleCompose(); }, opts);
      this.el("title-choice").addEventListener("change", () => {
        const title = this.context?.titles?.find(item => item.id === this.el("title-choice").value);
        if (title) { this.el("selected-title").value = title.text; this.scheduleAdjustment("selected-title"); }
      }, opts);
    }
    // Existing file/download handoffs can still focus an action, but never switch views.
    setTab(name) {
      const action = {images:"screenshots", report:"report", torrent:"torrent"}[name];
      if (action) this.button(action)?.scrollIntoView({block:"nearest"});
    }
    syncCollapses() {
      this.host.querySelectorAll("[data-collapse]").forEach(button =>
        this.setCollapse(button.dataset.collapse, button.getAttribute("aria-expanded") === "true"));
    }
    setCollapse(name, open) {
      const button = this.host.querySelector(`[data-collapse="${name}"]`);
      const panel = this.host.querySelector(`#wb-collapse-${name}`);
      if (!open && panel.contains(document.activeElement)) button.focus({preventScroll: true});
      button.setAttribute("aria-expanded", String(open));
      panel.classList.toggle("open", open);
      panel.setAttribute("aria-hidden", String(!open));
      panel.inert = !open;
    }
    setLayoutView(view) { if (view === "preview") this.openPanel(view); }
    renderSelection() {
      const items = this.pendingSelection;
      const list = this.el("selection-list");
      list?.replaceChildren();
      for (const item of items) {
        const row = node("div", null, "wb-selection-item"); row.setAttribute("role", "listitem");
        const path = item.relative_path || "";
        const name = path.split("/").filter(Boolean).pop() || "根目录";
        const rootLabel = this.browser?.root(item.root_key)?.label || item.root_key || "资源";
        const copy = node("div", null, "wb-selection-copy");
        copy.append(node("strong", name), node("small", `${rootLabel} / ${path || "根目录"}`));
        row.append(copy, node("span", resourceKind(item), "wb-selection-type"));
        list?.append(row);
      }
      const count = this.el("selection-count");
      count.hidden = !items.length;
      count.textContent = items.length ? `已勾选 ${items.length} 项` : "";
      const status = this.el("selection-status");
      if (status) status.textContent = items.length
        ? `已勾选 ${items.length} 项；${this.resources.length ? "确认后将切换当前资源，人工编辑会先请求确认。" : "确认前不会改变当前资源。"}`
        : this.selectionHint || "单击高亮；双击打开目录或直接使用视频。";
    }
    openPanel(name) {
      const target = {resources:"wb-resources", preview:"wb-publish-preview", naming:"wb-naming", changes:"wb-naming-files", identity:"wb-naming"}[name];
      if (!target) return;
      if (["naming", "changes"].includes(name)) this.setCollapse("naming", true);
      const section = this.host.querySelector(`#${target}`);
      if (name === "changes") section.open = true;
      section.scrollIntoView({block:"start"});
    }
    combinedTitle() {
      const zh = this.el("title-zh").value.trim(), en = this.el("title-en").value.trim(), mode = this.el("title-combination").value;
      return mode === "zh" ? zh : mode === "en" ? en : [...new Set([zh, en].filter(Boolean))].join(" ");
    }
    scheduleAdjustment(field) {
      this.cancelTimer(this.adjustmentTimer);
      this.cancelTimer(this.overrideTimer);
      this.invalidateRename();
      this.versions.context = (this.versions.context || 0) + 1;
      if (field === "selected-title") this.manualNaming = true;
      if (field === "naming-type") {
        this.namingType = this.el("naming-type").value;
        this.el("naming-rule").value = this.context?.effective_rules?.naming_rule?.templates?.[this.namingType] || "";
      }
      if (["selected-title","season","episode","scope"].includes(field)) this.pendingAdjustmentEpoch = this.epoch;
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
      this.el("identity-name").textContent = identity ? (ctx.titles?.find(title=>title.source !== 'manual')?.text || val(ctx.metadata?.title) || '已匹配作品') : '尚未匹配影视资料';
      this.el("match-status").textContent = identity?.confirmed ? '已确认' : identity ? '待确认' : '待匹配';
      this.el("match-status").classList.toggle('confirmed',Boolean(identity?.confirmed));
      if (this.resources.length) { this.el("resource").textContent = this.resources.length === 1 ? this.resources[0].relative_path.split("/").at(-1) : `${this.resources.length} 项媒体 · ${this.resources[0].relative_path.split("/").at(-1)} 等`; this.el("context-summary").textContent = this.resources.length === 1 ? this.resources[0].relative_path : `${this.resources.length} 项工作资源 · ${this.resources[0].relative_path} 等`; }
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
    setButtonBusy(button, busy) {
      if (!button) return;
      // Only replace a label node, never remove icons or structured candidate rows.
      const label = button.querySelector("[data-button-label]") || (button.childElementCount ? null : button);
      if (busy) {
        if (label && !this.buttonLabels.has(button)) this.buttonLabels.set(button, label.textContent);
        if (label) label.textContent = "处理中…";
        button.setAttribute("aria-busy", "true");
        button.disabled = true;
      } else {
        if (label && this.buttonLabels.has(button)) label.textContent = this.buttonLabels.get(button);
        this.buttonLabels.delete(button);
        button.removeAttribute("aria-busy");
      }
    }
    toolActive(action) {
      const task = this.tasks.get(this.toolTasks.get(tools[action]?.[0]));
      return Boolean(task && ((!terminal(task) && task.status !== "auth_required") || task.status === "completed" && !this.applied.has(key(task))));
    }
    async run(action, button) {
      if (button?.disabled || this.busy(action) || this.toolActive(action)) return;
      if (["overrides","rules","naming","resolve"].includes(action)) this.cancelTimer(this.adjustmentTimer);
      const epoch = this.epoch, pendingKey = `${epoch}:${action}`;
      this.pending.add(pendingKey);
      this.toolErrors.delete(action);
      this.setButtonBusy(button, true);
      if (tools[action]) this.setCollapse("output", true);
      this.renderToolProgress();
      try {
        const handlers = {resolve: () => this.resolve(), search: () => this.search(), overrides: () => this.resolve(null, this.overrides()), rules: () => this.applyRules(), naming: () => this.naming(), undo: () => this.showUndoPreview(), apply: () => this.applyRename(), title: () => this.generate("release_title", true), subtitle: () => this.generate("subtitle", true), probe: () => this.probe(), screenshots: () => this.screenshots(), report: () => this.report(), bdinfo: () => this.report("bdinfo"), torrent: () => this.torrent(), upload: () => this.upload()};
        if (["rules", "naming"].includes(action) && this.pendingAdjustmentEpoch === this.epoch) await this.resolve(null, this.overrides());
        else await handlers[action]?.();
      } catch (error) {
        if (epoch === this.epoch) {
          if (tools[action]) this.toolErrors.set(action, error.message);
          else this.notice(error.message, true);
        }
      } finally {
        this.pending.delete(pendingKey);
        if (!this.busy(action) && !this.toolActive(action)) this.setButtonBusy(button, false);
        if (!this.destroyed) {
          this.controls();
          this.renderToolProgress();
        }
      }
    }
    controls() {
      if (this.destroyed) return;
      this.renderPresentation();
      const has = this.resources.length > 0, ctx = !!this.context;
      for (const action of ["resolve", "overrides", "probe"]) this.button(action).disabled = !has || this.busy(action);
      for (const action of ["naming", "title", "subtitle", "rules"]) this.button(action).disabled = !ctx || this.busy(action);
      this.syncToolButtons();
      this.button("search").disabled = !has || this.busy("search");
      this.host.querySelectorAll('[data-action="apply"]').forEach(el => el.disabled = !this.applyable() || this.busy("apply"));
      this.host.querySelectorAll('[data-action="undo"]').forEach(el => el.disabled = !this.renameBatch || this.busy("undo"));
      this.host.querySelectorAll("[data-copy]").forEach(el => el.disabled = !(el.dataset.copy === "content" ? this.content : this.fields[el.dataset.copy]));
    }
    syncToolButtons() {
      const has = this.resources.length > 0;
      const help = this.el("tool-help");
      const text = !has ? "请先选择工作媒体。" : this.resources.length > 1 ? "已选择多个文件；报告或制种请选择单文件或整个目录。" :
        this.images.length ? "操作使用当前媒体。图床复用下方已有截图。" : "操作使用当前媒体。生成截图后可上传图床。";
      const hint = this.resources.length === 1 && this.resources[0].kind !== "directory" ? `${text} BDInfo 需要蓝光目录。` : text;
      if (help.textContent !== hint) help.textContent = hint;
      for (const action of Object.keys(tools)) {
        const button = this.button(action), active = this.busy(action) || this.toolActive(action);
        const reportBusy = action === "report" && (this.busy("bdinfo") || this.toolActive("bdinfo")) || action === "bdinfo" && (this.busy("report") || this.toolActive("report"));
        if (active !== button.hasAttribute("aria-busy")) this.setButtonBusy(button, active);
        button.disabled = active || this.restoring === this.epoch || reportBusy ||
          (action === "bdinfo" && this.resources[0]?.kind !== "directory") || (action === "upload" ? !this.images.length : action === "screenshots" ? !has : this.resources.length !== 1);
      }
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
      this.versions.undo = (this.versions.undo || 0) + 1;
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
      this.content = ""; this.el("output").textContent = ""; this.el("rich-preview").replaceChildren(); this.el("local-images-preview").replaceChildren(); this.el("local-images-preview").hidden = true; this.el("html-preview").hidden = true; this.el("html-preview").removeAttribute("srcdoc");
      this.el("validation").textContent = this.context ? "正在更新预览…" : "等待识别服务返回上下文"; this.controls();
    }
    stageSelection(items, hint = "") {
      this.selectionHint = hint; this.pendingSelection = items.slice();
      this.renderSelection();
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
      const edited = this.dirty.size || this.nameEdits?.size || this.manualNaming;
      const active = Object.keys(tools).some(action => this.busy(action) || this.toolActive(action));
      if ((edited || active) && !global.confirm(edited
        ? "当前媒体有人工编辑。切换后将清除本页编辑，已提交任务仍按后台规则运行。确定切换？"
        : "当前媒体正在处理。切换后任务仍会运行，结果不会显示在新资源下。确定切换？")) return false;
      const epoch = ++this.epoch;
      this.toolTasks.clear(); this.toolErrors.clear(); this.torrentIds.clear(); this.linkSignature = null;
      this.closeDialog(this.el("image-dialog"), false);
      this.closeDialog(this.el("task-log-dialog"), false);
      this.nameEdits = new Set(); this.manualNaming = false; this.namesIdentity = null; this.namesInitialized = false;
      this.versions = {}; this.resources = resources; this.selectionSignature = selectedSignature;
      this.setCollapse("naming", false);
      this.setCollapse("overview", false);
      this.context = null; this.origins = origins; this.dirty.clear(); this.renameBatch = null; this.el("rename-list").replaceChildren(); this.editVersion++;
      this.cancelTimer(this.composeTimer); this.contextChanged("selection");
      this.imageTask = null; this.images = []; this.artifacts = []; this.links = []; this.renderToolProgress(); this.episodeOverrides = {}; this.episodeEdits.clear(); this.pendingReplacement = null; this.probedResources = null; this.invalidateRename();
      for (const field of Object.keys(this.fields)) this.setField(field, "");
      ["images", "reports", "torrents", "upload-links", "issues", "candidates", "media"].forEach(name => this.el(name).replaceChildren());
      ["naming-rule", "subtitle-rule", "title-rule", "selected-title", "season", "episode", "year", "resolution", "video-codec", "audio-codec"].forEach(name => this.el(name).value = "");
      this.el("episode-numbering").value = "auto";
      this.el("episode-mode").value = "detected";
      this.el("start-episode").value = "";
      this.el("episode-step").value = "1";
      this.el("episode-direction").value = "increment";
      this.el("episode-offset").value = "";
      this.el("naming-type").value = "movie";
      this.namingType = "movie";
      this.el("title-zh").value = ""; this.el("title-en").value = ""; this.el("title-combination").value = "both";
      this.el("query").value = resources[0].relative_path.split("/").at(-1).replace(/\.[^.]+$/, "").replace(/[._\-]+/g, " ").trim();
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
      if (this.context && epoch === this.epoch && !this.destroyed) await this.restoreTools(epoch);
      this.controls();
      return !this.destroyed && epoch === this.epoch;
    }
    overrides() {
      const result = {episode: {scope: this.el("scope").value}};
      for (const name of this.episodeEdits) {
        const input = this.el(name);
        if (!input.validity.valid) throw new Error("季号须为非负整数，集号须为正整数");
        result.episode[name] = input.value === "" ? null : Number(input.value);
      }
      if (this.el("selected-title").value.trim()) result.selected_title = this.el("selected-title").value.trim();
      return result;
    }
    namingOverrides() {
      const result = {};
      if ([...this.el("rename-list").querySelectorAll('input[type="number"]')].some(input => !input.validity.valid)) {
        throw new Error("逐集季号须为非负整数，集号须为正整数");
      }
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
      this.contextChanged();
      this.overrideTimer = this.later(async () => {
        if (!this.context || this.destroyed || this.timers.has(this.adjustmentTimer)) return;
        try {
          if (this.pendingAdjustmentEpoch === this.epoch) await this.resolve(null, this.overrides());
          else { await this.naming(); await this.generate("release_title"); }
        } catch(error) { this.notice(error.message, true); }
      }, 320);
    }
    async resolve(selection = null, manual_overrides = {}, candidateButton = null) {
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
      this.renderContext();
      const candidates = this.el("candidates");
      if (selection && candidates.contains(candidateButton)) {
        const restoreFocus = candidates.contains(document.activeElement) || document.activeElement === document.body;
        candidates.replaceChildren();
        this.setCollapse("naming", true);
        this.setCollapse("overview", true);
        if (restoreFocus) this.el("title-zh").focus({preventScroll: true});
      }
      await this.generateAll();
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
      const identityKey = ctx.identity?.tmdb_id || ctx.identity?.bangumi_id || null;
      if (this.namesIdentity !== identityKey || this.namesIdentity === null && !this.namesInitialized) {
        this.namesIdentity = identityKey; this.namesInitialized = true;
        for (const [field, lang] of [["title-zh","zh"], ["title-en","en"]]) {
          if (this.nameEdits?.has(field)) continue;
          const candidates = (ctx.titles || []).filter(title => new RegExp(`^${lang}(?:-|$)`, "i").test(title.language));
          const preferred = candidates.find(title => title.language === (lang === "zh" ? "zh-CN" : "en-US")) || candidates[0];
          this.el(field).value = val(ctx.metadata?.[lang === "zh" ? "title_zh" : "title_en"]) || preferred?.text || "";
        }
      }
      if (!this.manualNaming) this.el("selected-title").value = this.combinedTitle() || val(ctx.selected_title); this.el("season").value = val(ctx.episode?.season); this.el("episode").value = val(ctx.episode?.episode); this.el("scope").value = ctx.episode?.scope || this.el("scope").value;
      const probeValue = key => { const values = (ctx.files || []).map(file => val(file.probe?.[key])); return values.length && values.every(value => value !== "" && String(value) === String(values[0])) ? values[0] : ""; };
      const autofill = {year: val(ctx.metadata?.year), resolution: probeValue("resolution"), "video-codec": probeValue("video_codec"), "audio-codec": probeValue("audio_codec")};
      for (const [name, value] of Object.entries(autofill)) if (!this.nameEdits?.has(name) && (name === "year" || value !== "" && this.el(name).value.trim() === "")) this.el(name).value = value;
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
      this.el("episode-numbering").value = batch.episode_numbering || "auto";
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
        return {...rule, ...values, ...(this.el("selected-title").value.trim() ? {custom_title: this.el("selected-title").value.trim(), selected_title_id: undefined} : {}), template_id:kind, templates, episode_numbering: this.el("episode-numbering").value, episode_mode: episodeMode,
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
      const container = this.el("candidates");
      container.replaceChildren();
      if (result.items.length) {
        this.setCollapse("naming", false);
        this.setCollapse("overview", false);
      }
      if(result.warnings?.length) container.append(node('p',result.warnings.map(x=>x.message).join('；'),'wb-note'));
      if (!result.items.length) container.append(node("p", "未找到候选，可以调整名称后重试。", "wb-note"));
      result.items.forEach(candidate => {
        const button = node("button", null, 'wb-candidate');
        const info=node('span',null,'wb-candidate-copy');info.append(node('strong',candidate.title||candidate.name||candidate.id),node('small',[candidate.original_title,candidate.year,({movie:'电影',series:'剧集',anime:'动画'})[candidate.media_type],candidate.provider?`${candidate.provider.toUpperCase()} #${candidate.id}`:null].filter(Boolean).join(' · ')));button.append(info);
        const poster=safeURL(candidate.poster_url);if(candidate.poster_url&&poster){const image=node('img');image.alt='';image.src=poster;image.referrerPolicy='no-referrer';image.onerror=()=>image.remove();button.prepend(image);}
        button.type = "button"; button.disabled = !this.resources.length;
        button.onclick = () => this.withButton(button, () => this.resolve({provider: candidate.provider, id: String(candidate.id), media_type: candidate.media_type}, {}, button)); container.append(button);
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
      const titleRule = this.readRule("title-rule");
      const titleOverrides = Object.fromEntries([["year", "Year"], ["resolution", "Resolution"], ["video-codec", "VideoCodec"], ["audio-codec", "AudioCodec"]]
        .filter(([control]) => this.nameEdits?.has(control) && this.el(control).value.trim())
        .map(([control, field]) => [field, this.el(control).value.trim()]));
      const result = name === "release_title" ? await this.api.title(this.context, {...titleRule, ...(Object.keys(titleOverrides).length ? {overrides: titleOverrides} : {})}) : await this.api.subtitle(this.context, this.readRule("subtitle-rule", this.context.effective_rules?.subtitle_rule || {}));
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
          if (file) for (const [key, caption, minimum] of [["season", "季号", 0], ["episode", "集号", 1]]) {
            if (key === "season" && this.el("episode-numbering").value === "continuous") continue;
            const label = node("label", null, "wb-rename-episode"); label.append(node("span", caption));
            const input = node("input"); input.type = "number"; input.min = String(minimum); input.step = "1";
            const proposed = preview.file_episodes?.[path];
            input.value = this.episodeOverrides[path]?.[key] ?? (proposed ? proposed[key] ?? "" : val(file.episode?.[key]));
            input.addEventListener("input", () => {
              const values = {...this.episodeOverrides[path]};
              if (input.validity.valid) {
                if (input.value === "") delete values[key];
                else values[key] = Number(input.value);
              }
              if (Object.keys(values).length) this.episodeOverrides[path] = values;
              else delete this.episodeOverrides[path];
              this.scheduleNamingOverride();
            });
            label.append(input); row.append(label);
          }
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
      const rule = this.readRule("naming-rule", this.context.effective_rules?.naming_rule || {});
      const preview = await this.api.naming(this.context, this.namingOverrides(), rule);
      if (!current() || this.context?.revision !== revision || preview.context_revision < revision) return;
      this.context = {...this.context, revision: preview.context_revision,
        episode: preview.episode || this.context.episode,
        effective_rules: {...this.context.effective_rules, naming_rule: rule}};
      this.contextChanged();
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
      const preview = this.preview, epoch = this.epoch, resources = this.resources, undo = preview.undo;
      const response = undo ? await this.api.undoApply(preview, this.previewKey) : await this.api.apply(preview, this.previewKey);
      this.track(response.task, {type: "rename", epoch, resources, operations: preview.operations, undo});
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
    async toolScope() {
      const epoch = this.epoch, resources = this.resources;
      if (!resources.length) throw new Error("请先选择工作媒体");
      const canonical = this.context ? resources : await Promise.all(resources.map(resource => this.api.request("/api/v2/resources/reference", resource)));
      if (this.epoch !== epoch || this.destroyed) return null;
      return {epoch, scope: signature(canonical)};
    }
    async screenshots() {
      const scope = await this.toolScope();
      if (!scope) return;
      const {values} = await this.api.request("/api/settings");
      if (this.epoch !== scope.epoch || this.destroyed) return;
      const output_mode = values?.screenshot_output_mode === "collage" ? "collage" : "single";
      const result = await this.api.request("/api/files/screenshots", {sources: this.sources(), output_mode, auto_upload: false});
      this.track(result.task, {type: "screenshot", session: result.session_id, ...scope});
    }
    async report(type = "report") {
      if (this.resources.length !== 1) throw new Error("请选择一个视频或一个目录");
      const epoch = this.epoch, source = this.resources[0];
      if (type === "bdinfo" || source.kind === "directory") {
        if (source.kind !== "directory") throw new Error("BDInfo 需要选择蓝光目录");
        const optical = await this.api.request("/api/media/optical?" + new URLSearchParams(this.sources()[0]));
        if (type === "bdinfo" && optical.kind !== "bluray") throw new Error("当前目录不是蓝光目录");
        if (type === "report" && optical.kind === "bluray") throw new Error("当前为蓝光目录，请使用生成 BDInfo");
        if (epoch !== this.epoch) return;
      }
      const canonical = this.context ? source : await this.api.request("/api/v2/resources/reference", source);
      if (epoch !== this.epoch || this.destroyed) return;
      const result = await this.api.request("/api/v2/reports", canonical);
      this.track(result.task, {type, epoch, scope: signature([canonical])});
    }
    async torrent() {
      const source = this.resources[0], scope = await this.toolScope();
      if (!scope) return;
      const result = await this.api.request("/api/torrents/jobs", {
        source_root_key: source.root_key, source_relative_path: source.relative_path, overwrite: false
      });
      this.track(result.task, {type: "torrent", ...scope});
    }
    async upload() {
      if (this.images.length > 50) throw new Error("每次最多上传 50 张图片，请减少截取数量");
      const images = this.images;
      const scope = await this.toolScope();
      if (!scope || images !== this.images) return;
      const screenshot = this.imageTask;
      const result = await this.api.request("/api/uploads/jobs", {items: images.map(x => ({root_key: "images", relative_path: x.path}))});
      this.track(result.task, {type: "upload", ...scope, images, screenshot});
    }
    static receipts() {
      try {
        const entries = JSON.parse(global.sessionStorage.getItem(receiptKey) || "[]");
        return Array.isArray(entries) ? entries.filter(entry => Number.isSafeInteger(entry?.id) &&
          entry.id > 0 && typeof entry.scope === "string" && Object.hasOwn(toolTypes, entry.type)).slice(-40) : [];
      } catch { return []; }
    }
    updateReceipts(transform) {
      try { global.sessionStorage.setItem(receiptKey, JSON.stringify(transform(Workbench.receipts()).slice(-40))); }
      catch { /* Storage may be disabled. Live dispatch is unaffected. */ }
    }
    async restoreTools(epoch) {
      const scope = signature(this.resources), receipts = Workbench.receipts().filter(entry => entry.scope === scope);
      if (!receipts.length) return;
      const current = () => !this.destroyed && this.epoch === epoch && signature(this.resources) === scope;
      this.restoring = epoch;
      this.syncToolButtons();
      this.renderToolProgress();
      try {
        await Promise.all(receipts.map(async receipt => {
          try {
            const task = await this.api.request(`/api/tasks/${receipt.id}`);
            const input = task.input;
            if (!current() || task.id !== receipt.id || task.task_type !== toolTypes[receipt.type] || !input ||
                this.toolTasks.has(receipt.type) || task.project_id != null) return;
            const source = this.resources[0];
            if (["report", "bdinfo"].includes(receipt.type) && (!input.resource || signature([input.resource]) !== scope)) return;
            if (receipt.type === "torrent" && (input.source_root_key !== source.root_key || input.source_relative_path !== source.relative_path)) return;
            if (receipt.type === "screenshot" && (!receipt.session || input.session_id !== receipt.session)) return;
            if (receipt.type === "upload" && (!receipt.screenshot || !receipts.some(entry =>
              entry.type === "screenshot" && `processing:${entry.id}` === receipt.screenshot))) return;
            const item = {...this.api.legacyTask(task), legacy: this.api.tasksMode === "legacy"};
            const id = key(item);
            this.tasks.set(id, item);
            this.jobs.set(id, {...receipt, epoch, restored: true, input});
            this.applied.delete(id); this.resultErrors.delete(id);
            this.toolTasks.set(receipt.type, id);
          } catch { /* Missing/inaccessible tasks are not attached to the resource. */ }
        }));
      } finally {
        if (this.restoring === epoch) this.restoring = null;
        if (current()) {
          this.renderTasks();
          this.renderToolProgress(); this.syncToolButtons(); this.refreshTasks();
        }
      }
    }
    track(task, job) {
      const normalized = task.kind ? task : this.api.legacyTask(task), id = key(normalized);
      if (this.destroyed) return;
      this.tasks.set(id, normalized); this.jobs.set(id, job);
      if (job.scope && normalized.kind === "processing" && Number.isSafeInteger(normalized.id) && Object.hasOwn(toolTypes, job.type)) {
        const receipt = {id: normalized.id, scope: job.scope, type: job.type, session: job.session, screenshot: job.screenshot};
        this.updateReceipts(entries => [...entries.filter(entry => entry.scope !== receipt.scope ||
          entry.type !== receipt.type && !(receipt.type === "screenshot" && entry.type === "upload") &&
          !(["report", "bdinfo"].includes(receipt.type) && ["report", "bdinfo"].includes(entry.type))), receipt]);
      }
      if (job.epoch === this.epoch && (!job.images || job.images === this.images)) {
        if (Object.values(tools).some(([type]) => type === job.type)) this.toolTasks.set(job.type, id);
        if (!Object.values(tools).some(([type]) => type === job.type)) this.notice("任务已提交，完成后会通知");
      }
      this.renderTasks(); this.renderToolProgress(); this.refreshTasks();
    }
    async refreshTasks(initial = false) {
      if (this.refreshing || this.destroyed) return; this.refreshing = true; this.cancelTimer(this.pollTimer);
      try {
        const tasks = await this.api.tasks(); if (this.destroyed) return;
        // Reuse the same polling cycle to read worker checkpoints only for current,
        // running tools; the shared task projection has no processing counters.
        const active = tasks.filter(task => task.status === "running" && this.toolTasksHas(key(task)));
        await Promise.all(active.map(async task => {
          if (task.legacy) return;
          try {
            const detail = await this.api.request(`/api/tasks/${encodeURIComponent(task.id)}`);
            task.result = detail.result;
          } catch { /* Still show the queue's real state if a detail request fails. */ }
        }));
        if (this.destroyed) return;
        const listed = new Set(tasks.map(key));
        for (const id of this.jobs.keys()) {
          const previous = this.tasks.get(id);
          if (!listed.has(id) && previous && (this.toolTasksHas(id) || this.jobs.get(id)?.epoch === this.epoch) && (!terminal(previous) || !this.applied.has(id) && previous.status === "completed")) {
            try {
              const detail = await this.api.request(previous.legacy ? `/api/tasks/${previous.id}` : `/api/v2/tasks/${previous.kind}/${previous.id}`);
              tasks.push(previous.legacy ? this.api.legacyTask(detail) : detail);
            } catch (error) {
              if (this.toolTasksHas(id)) this.actionErrors.set(id, `状态读取失败：${error.message}`);
            }
          }
        }
        const dependencyOrder = task => ({screenshot: 0, upload: 2}[this.jobs.get(key(task))?.type] ?? 1);
        for (const task of [...tasks].sort((a,b) => dependencyOrder(a) - dependencyOrder(b))) {
          if (this.destroyed) return;
          const id = key(task), previous = this.tasks.get(id);
          if (this.stopAcks.has(id) && terminal(previous || {}) && !terminal(task)) { this.stopAcks.delete(id); continue; }
          this.stopAcks.delete(id);
          this.tasks.set(id, task);
          if (this.actionErrors.get(id)?.startsWith("进度暂时无法读取：") || this.actionErrors.get(id)?.startsWith("状态读取失败：")) this.actionErrors.delete(id);
          if (terminal(task)) {
            this.cancelPending.delete(id);
            if (!previous || !terminal(previous)) this.actionErrors.delete(id);
            const notification = `${id}:${task.status}:${task.finished_at || ""}`;
            if (!global.AppTaskCenter && (!this.jobs.has(id) || this.jobs.get(id).epoch === this.epoch) && !initial && !this.notified.has(notification) && !this.toolTasksHas(id) && (this.jobs.has(id) || (previous && !terminal(previous)))) { this.notice(`${task.task_type}：${{completed: "已完成", failed: "失败", cancelled: "已取消"}[task.status]}${task.error ? ` · ${task.error}` : ""}`, task.status === "failed"); }
            this.notified.add(notification);
            if (task.status === "completed" && this.jobs.has(id) && !this.applied.has(id)) {
              const job = this.jobs.get(id);
              // A restored upload must wait for its screenshot job on the same media.
              if (job.restored && job.type === "upload" && !this.applied.has(job.screenshot)) continue;
              this.renderToolProgress();
              try {
                await this.applyTask(task, job);
              } catch (error) {
                if (!this.destroyed && job.epoch === this.epoch) {
                  this.resultErrors.set(id, error.message);
                  if (!this.toolTasksHas(id)) this.notice(`产物读取失败：${error.message}`, true);
                }
              }
              if (this.jobs.get(id) === job) this.applied.add(id);
            }
          }
        }
        this.el("task-status").textContent = this.api.tasksMode === "legacy" ? "已连接现有工具队列；统一下载任务视图待接入。刷新仅恢复任务，不恢复发布内容。" : "刷新仅恢复任务进度和实际产物，不恢复发布内容。";
        this.renderTasks(); this.renderToolProgress(); this.syncToolButtons();
      } catch (error) {
        if (!this.destroyed) {
          this.el("task-status").textContent = `${error.message}；稍后自动重试`;
          for (const id of this.toolTasks.values()) {
            const task = this.tasks.get(id);
            if (task && !terminal(task)) this.actionErrors.set(id, `进度暂时无法读取：${error.message}；将自动重试`);
          }
          this.renderToolProgress();
        }
      }
      finally { this.refreshing = false; if (!this.destroyed) this.pollTimer = this.later(() => this.refreshTasks(), this.pollInterval); }
    }
    toolTasksHas(id) { return [...this.toolTasks.values()].includes(id); }
    renderToolProgress() {
      const empty = this.el("output-empty"), message = this.restoring === this.epoch ? "正在读取当前媒体的工具任务…" : "执行操作后，进度和结果会显示在这里。";
      if (empty.textContent !== message) empty.textContent = message;
      this.el("output-empty").hidden = this.restoring !== this.epoch && (this.toolTasks.size > 0 || this.toolErrors.size > 0 || Object.keys(tools).some(action => this.busy(action)));
      const working = Object.keys(tools).some(action => this.busy(action) || this.toolActive(action));
      const label = working ? "处理中" : this.toolErrors.size ? "有操作失败" : this.toolTasks.size ? "查看任务与结果" : "";
      const headingStatus = this.el("output-status");
      if (headingStatus.textContent !== label) headingStatus.textContent = label;
      for (const [action, [type, panel]] of Object.entries(tools)) {
        const target = this.el(`tool-progress-${panel}`);
        const id = this.toolTasks.get(type), task = this.tasks.get(id), submitting = this.busy(action);
        const error = this.toolErrors.get(action) || (!submitting && (this.resultErrors.get(id) || this.actionErrors.get(id) || task?.error)) || "";
        const phase = this.toolErrors.has(action) || this.resultErrors.has(id) && !submitting ? "failed" :
          submitting ? "submitting" : task?.status === "completed" && !this.applied.has(id) ? "reading" : task?.status || "";
        const status = {submitting: "正在提交…", reading: "正在读取结果…", ...taskLabels}[phase] || phase;
        const active = ["queued", "running", "submitting", "reading"].includes(phase);
        const data = task?.result;
        const numbers = task?.status !== "running" ? null : panel === "images" && Number.isInteger(data?.image_total) && data.image_total > 0
          ? [Number(data.image_succeeded || 0) + Number(data.image_failed || 0), data.image_total]
          : panel === "upload" && Array.isArray(data?.uploads) ? [data.uploads.length, this.jobs.get(id)?.images?.length] : null;
        const raw = Number.isFinite(task?.progress) ? task.progress : numbers?.[1] > 0 ? 100 * numbers[0] / numbers[1] : null;
        const percent = ["queued", "running"].includes(phase) && raw !== null ? Math.max(0, Math.min(100, raw)) : null;
        if (!status) { if (target.firstChild) target.replaceChildren(); continue; }
        if (!target.firstChild) {
          const summary = node("p", null, "wb-tool-phase"), track = node("div", null, "wb-progress-track");
          track.setAttribute("role", "progressbar"); track.setAttribute("aria-label", "任务进度");
          const cancel = node("button", "停止");
          cancel.type = "button";
          track.append(node("span")); target.append(summary, track, node("p", null, "wb-tool-error"), cancel);
        }
        const [summary, track, reason, cancel] = target.children;
        const name = {screenshots:"截图", report:"MediaInfo", bdinfo:"BDInfo", torrent:"制作种子", upload:"图床"}[action];
        const stopping = this.cancelPending.has(id);
        cancel.hidden = !task?.cancel_allowed || !["queued", "running"].includes(phase);
        cancel.disabled = stopping;
        cancel.setAttribute("aria-label", `${stopping ? "正在停止…" : "停止"} · ${name}`);
        if (cancel.textContent !== (stopping ? "正在停止…" : "停止")) cancel.textContent = stopping ? "正在停止…" : "停止";
        cancel.onclick = () => this.cancelTool(id);
        const count = phase === "completed" && ["images", "upload"].includes(panel) ? ` · ${panel === "images" ? this.images.length : this.links.length} 项` :
          numbers?.[1] > 0 ? ` · ${numbers[0]}/${numbers[1]} 项` :
          panel === "torrent" && phase === "running" && Number.isFinite(data?.bytes) ? ` · 已读取 ${formatSize(data.bytes)}` : "";
        const label = `${name}：${stopping && !terminal(task || {}) ? "正在停止，等待任务结束" : status}${count}${percent === null ? "" : ` · ${Math.round(percent)}%`}`;
        if (summary.textContent !== label) summary.textContent = label;
        if (reason.textContent !== error) reason.textContent = error;
        track.hidden = !active;
        track.classList.toggle("wb-indeterminate", percent === null);
        if (percent === null) track.removeAttribute("aria-valuenow");
        else if (track.getAttribute("aria-valuenow") !== String(percent)) {
          track.setAttribute("aria-valuenow", String(percent));
          track.style.setProperty("--wb-progress", percent / 100);
        }
      }
    }
    async cancelTool(id) {
      const task = this.tasks.get(id);
      if (!task?.cancel_allowed || terminal(task) || this.cancelPending.has(id)) return;
      this.cancelPending.add(id);
      this.actionErrors.delete(id);
      this.renderToolProgress();
      try {
        const result = await this.api.taskAction(task, "cancel");
        if (this.destroyed) return;
        if (!terminal(this.tasks.get(id) || {}) || terminal(result)) this.tasks.set(id, result);
        if (terminal(result)) this.stopAcks.add(id);
        this.refreshTasks();
      } catch (error) {
        this.cancelPending.delete(id);
        this.actionErrors.set(id, error.message);
      } finally {
        if (!this.destroyed) {
          this.renderToolProgress(); this.syncToolButtons();
        }
      }
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
      if (!task.result && (["torrent", "upload"].includes(job.type) || !task.artifact_refs?.length)) {
        const data = await this.api.request((task.legacy || ["torrent", "upload"].includes(job.type)) ? `/api/tasks/${task.id}` : `/api/v2/tasks/${task.kind}/${task.id}`);
        detail = {...task, ...data, artifact_refs: data.artifact_refs || data.result?.artifact_refs || task.artifact_refs};
      }
      if (!detail.artifact_refs && detail.result?.artifact_refs) detail = {...detail, artifact_refs: detail.result.artifact_refs};
      if (job.epoch !== this.epoch || this.destroyed) return;
      const latest = () => !this.destroyed && job.epoch === this.epoch && (!this.toolTasks.has(job.type) || this.toolTasks.get(job.type) === key(task));
      if (!latest() || job.images && job.images !== this.images) return;
      if (job.type === "upload" && job.restored) {
        const matches = job.input.items?.every(item => item.root_key === "images" && this.images.some(image => image.path === item.relative_path));
        if (!matches || !job.input.items?.length || this.imageTask !== job.screenshot) throw new Error("关联的截图已变化，未恢复旧图床结果");
      }
      if (job.type === "screenshot") {
        const session = await this.api.request(`/api/files/screenshots/${encodeURIComponent(job.session)}`); if (!latest()) return;
        const outputs = Object.values(session.final_outputs || {});
        const selected = outputs.length ? outputs : (session.videos || []).flatMap(v => v.items || []);
        const images = selected.filter(x => x.status === "completed" && x.available !== false).map(x => ({id: String(x.id || x.video_id), path: x.output_relative_path || x.relative_path, kind: session.params?.output_mode === "collage" ? "collage" : "screenshot"})).filter(x => x.path);
        if (!images.length) throw new Error("任务未返回可读取的截图，请检查任务日志");
        this.images = images; this.imageTask = key(task);
        if (this.jobs.get(this.toolTasks.get("upload"))?.screenshot !== this.imageTask) this.toolTasks.delete("upload");
        this.toolErrors.delete("upload");
        this.artifacts = this.artifacts.filter(x => !["screenshot", "collage", "upload"].includes(x.kind)); this.links = []; this.renderUploadLinks();
        this.renderImages();
      }
      if (job.type === "upload") {
        const uploads = detail.result?.uploads || [];
        const completed = uploads.filter(x => x.status === "completed" && safeURL(x.remote_url));
        if (!completed.length && !(detail.artifact_refs || []).some(x => x.kind === "upload" && x.url && safeURL(x.url))) {
          throw new Error(uploads.find(x => x.error)?.error || "任务未返回有效的图床链接");
        }
        this.links = [...new Set([...this.links, ...completed.map(x => x.remote_url)])];
        this.artifacts = [...this.artifacts.filter(x => x.kind !== "upload"), ...completed.map(x => ({kind: "upload", id: String(x.id), resource: null, url: x.remote_url}))];
        this.renderUploadLinks();
        if (completed.length < uploads.length) this.notice("部分图片上传失败，请检查任务日志后重试", true);
      }
      if (["report", "bdinfo"].includes(job.type)) {
        const cards = document.createDocumentFragment();
        for (const artifact of detail.artifact_refs || []) {
          if (artifact.kind !== 'report' || !artifact.resource) continue;
          const report=await this.api.request('/api/files/text?'+new URLSearchParams({root_key:artifact.resource.root_key,path:artifact.resource.relative_path}));
          if (!latest()) return;
          const text=report.content || report.text;
          if (!text) throw new Error('任务返回了空的媒体报告');
          const copy=node('button','复制报告'), download=node('a','下载报告');
          copy.type='button'; copy.onclick=()=>this.copyValue(text, copy);
          download.href=(artifact.url && safeURL(artifact.url)) || `/api/v2/reports/${encodeURIComponent(artifact.id)}/file`;
          download.setAttribute('download','');
          const card=node('div',null,'wb-report-card');
          const clear = node("button", "清空");
          clear.type = "button";
          clear.onclick = () => {
            card.remove();
            if (!this.el("reports").childElementCount) {
              this.updateReceipts(entries => entries.filter(entry => entry.id !== task.id));
              if (this.toolTasks.get(job.type) === key(task)) this.toolTasks.delete(job.type);
            }
            this.renderToolProgress();
            this.artifacts = this.artifacts.filter(value => value.kind !== "report" || value.id !== artifact.id);
            this.editVersion++; this.scheduleCompose();
          };
          const body = node("pre", text); body.tabIndex = 0;
          card.append(copy,download,clear,body);
          cards.append(card);
        }
        if (!cards.childElementCount) throw new Error("任务未返回媒体报告");
        this.el("reports").replaceChildren(cards);
        this.artifacts = this.artifacts.filter(x => x.kind !== "report");
      }
      if (detail.artifact_refs?.length) {
        const references = detail.artifact_refs.filter(artifact => artifact && ["screenshot", "collage", "report", "torrent", "upload"].includes(artifact.kind) && typeof artifact.id === "string" && (!artifact.url || safeURL(artifact.url)));
        const added = references.filter(artifact => !this.artifacts.some(x => x.kind === artifact.kind && x.id === artifact.id));
        if (added.length) this.artifacts = [...this.artifacts, ...added];
        this.links = [...new Set([...this.links, ...references.filter(artifact => artifact.kind === "upload" && artifact.url).map(artifact => artifact.url)])];
        this.renderUploadLinks();
      }
      if (job.type === "torrent") {
        if (detail.result?.record) {
          const record = detail.result.record;
          const info = await this.api.request("/api/files/meta?" + new URLSearchParams({root_key: record.output_root_key || "torrents", path: record.output_relative_path}));
          if (!latest()) return;
          this.renderTorrent({...record, size: info.size_bytes});
        }
        else {
          const references = (detail.artifact_refs || []).filter(x => x.kind === "torrent");
          if (!references.length) throw new Error("任务未返回种子文件");
          for (const artifact of references) this.renderTorrent({id: artifact.id, name: artifact.resource?.relative_path?.split("/").at(-1)});
        }
      }
      this.editVersion++; this.scheduleCompose(); this.controls();
    }
    renderUploadLinks() {
      const linkSignature = JSON.stringify(this.links);
      if (this.linkSignature === linkSignature) return;
      this.linkSignature = linkSignature;
      this.el("upload-links").replaceChildren();
      this.links.forEach(url => {
        const row = node("div", null, "wb-upload-link"), link = node("a", url);
        const preview = node("img"); preview.alt = "图床图片预览"; preview.loading = "lazy";
        preview.referrerPolicy = "no-referrer"; preview.src = safeURL(url);
        preview.onerror = () => preview.remove();
        link.href = safeURL(url); link.target = "_blank"; link.rel = "noopener noreferrer";
        const copyUrl = node("button", "复制链接"); copyUrl.type = "button"; copyUrl.onclick = () => this.copyValue(url, copyUrl);
        const copyBBCode = node("button", "复制 BBCode"); copyBBCode.type = "button"; copyBBCode.onclick = () => this.copyValue(`[img]${url}[/img]`, copyBBCode);
        row.append(preview, link, node("code", `[img]${url}[/img]`), copyUrl, copyBBCode); this.el("upload-links").append(row);
      });
      if (this.links.length) for (const [label, text] of [["复制全部链接", this.links.join("\n")], ["复制全部 BBCode", this.links.map(url => `[img]${url}[/img]`).join("\n")]]) {
        const button = node("button", label);
        button.type = "button"; button.onclick = () => this.copyValue(text, button);
        this.el("upload-links").append(button);
      }
    }
    openDialog(dialog, opener) {
      if (!dialog.open) {
        this.dialogState.set(dialog, {opener, x: scrollX, y: scrollY});
        dialog.showModal();
      }
    }
    closeDialog(dialog, restore = true) {
      if (!dialog?.open) return;
      const state = this.dialogState.get(dialog);
      this.dialogState.delete(dialog);
      dialog.close();
      if (dialog === this.el("image-dialog")) {
        const image = this.el("image-dialog-img");
        image.onload = image.onerror = null; image.removeAttribute("src"); image.hidden = true;
      }
      if (restore && state) {
        const opener = state.opener?.isConnected ? state.opener : this.el("tasks").closest("details")?.querySelector("summary");
        opener?.focus({preventScroll: true});
        global.scrollTo({left: state.x, top: state.y, behavior: "instant"});
      }
    }
    showImage(url, path, opener) {
      const dialog = this.el("image-dialog"), image = this.el("image-dialog-img"), status = this.el("image-dialog-status");
      image.hidden = true; status.hidden = false; status.textContent = "正在加载缩略图…";
      image.alt = path;
      image.onload = () => { if (dialog.open) { status.hidden = true; image.hidden = false; } };
      image.onerror = () => { if (dialog.open) status.textContent = "缩略图无法读取，请检查文件是否可用。"; };
      this.openDialog(dialog, opener);
      image.src = url;
    }
    async showTaskLog(task, opener) {
      const dialog = this.el("task-log-dialog"), status = this.el("task-log-status"), output = this.el("task-log-output");
      this.openDialog(dialog, opener);
      const state = this.dialogState.get(dialog), request = Symbol();
      state.request = request;
      const current = () => !this.destroyed && dialog.open && this.dialogState.get(dialog)?.request === request;
      this.el("task-log-title").textContent = `任务日志 · ${task.task_type}`;
      status.textContent = "正在读取日志…"; status.hidden = false; output.textContent = "";
      output.parentElement.scrollTop = 0;
      try {
        const id = encodeURIComponent(task.id);
        const result = await this.api.request(task.kind === "download" ? `/api/jobs/${id}/log?lines=100` : `/api/tasks/${id}/logs?lines=100`);
        if (!current()) return;
        const lines = result.content || result.lines || result.logs || result.log;
        output.textContent = (Array.isArray(lines) ? lines.map(x => typeof x === "string" ? x : x?.message || JSON.stringify(x)).join("\n") : lines) || "暂无日志";
        status.hidden = true;
      } catch (error) { if (current()) status.textContent = `读取日志失败：${error.message}`; }
    }
    renderImages() {
      const figures = this.images.map(image => {
        const url = "/api/files/image?" + new URLSearchParams({root_key: "images", path: image.path});
        const figure = node("figure", null, "wb-image"), img = node("img"), caption = node("figcaption"), download = node("a", "下载缩略图");
        const preview = node("button", null, "wb-image-preview"), placeholder = node("span", "加载图片…", "wb-note");
        preview.type = "button"; preview.setAttribute("aria-label", `预览 ${image.path}`);
        preview.onclick = () => this.showImage(url, image.path, preview);
        img.alt = image.path; img.loading = "lazy";
        img.onload = () => { placeholder.hidden = true; img.classList.add("wb-image-ready"); };
        img.onerror = () => { placeholder.textContent = "图片加载失败"; img.hidden = true; };
        img.src = url; download.href = `${url}&download=true`;
        preview.append(placeholder, img); caption.append(download); figure.append(preview, caption);
        return figure;
      });
      this.el("images").replaceChildren(...figures);
    }
    renderTorrent(record) {
      const id = String(record.id || "");
      if (!id || this.torrentIds.has(id)) return;
      this.torrentIds.add(id);
      const card = node("article", null, "wb-torrent-result"), link = node("a", "下载种子");
      link.href = `/api/torrents/${encodeURIComponent(record.id)}/file`;
      card.append(node("strong", record.output_relative_path?.split("/").at(-1) || record.filename || record.name || ".torrent"), node("p", Number.isFinite(record.size) ? formatSize(record.size) : ""), link);
      this.el("torrents").append(card);
    }
    updateTaskProgress(bar, task) {
      const value = Number.isFinite(task.progress) ? Math.max(0, Math.min(100, task.progress)) : task.status === "completed" ? 100 : null;
      if (value === null) bar.removeAttribute("value");
      else if (bar.getAttribute("value") !== String(value)) bar.value = value;
    }
    renderTasks() {
      const tasks = [...this.tasks.values()], visible = tasks.slice(0, 30);
      const signature = JSON.stringify([tasks.filter(task => !terminal(task)).length,
        visible.map(task => [key(task), task.task_type, task.status, task.error, task.cancel_allowed, task.retry_allowed])]);
      if (signature === this.taskSignature) {
        for (const task of visible) this.updateTaskProgress(this.taskBars.get(key(task)), task);
        return;
      }
      this.taskSignature = signature;
      this.taskBars = new Map();
      this.el("task-count").textContent = String(tasks.filter(task => !terminal(task)).length); this.el("tasks").replaceChildren();
      visible.forEach(task => {
        const card = node("article", null, "wb-task"); card.append(node("strong", `${task.task_type} · ${taskLabels[task.status] || task.status}`));
        const progress = node("progress"); progress.max = 100; progress.setAttribute("aria-label", "任务进度");
        this.taskBars.set(key(task), progress); this.updateTaskProgress(progress, task); card.append(progress);
        if (task.error) card.append(node("p", task.error, "wb-note"));
        const actions = node("div", null, "wb-task-actions");
        for (const [action, label, allowed] of [["cancel", "取消", task.cancel_allowed], ["retry", "重试", task.retry_allowed && !/rename/.test(task.task_type)]]) if (allowed) {
          const button = node("button", label); button.type = "button"; button.onclick = () => this.withButton(button, async () => { const result = await this.api.taskAction(this.tasks.get(key(task)), action); this.tasks.set(key(result), result); if (action === "retry") { this.applied.delete(key(result)); this.resultErrors.delete(key(result)); } this.renderTasks(); this.renderToolProgress(); this.syncToolButtons(); await this.refreshTasks(); }); actions.append(button);
        }
        if (task.status === "completed" && ["screenshot", "screenshot_collage", "image_upload", "torrent_build"].includes(task.task_type)) {
          const show = node("button", "查看产物"); show.type = "button";
          show.onclick = () => this.withButton(show, () => this.showTaskArtifacts(task, card)); actions.append(show);
        }
        const logs = node("button", "日志"); logs.type = "button";
        logs.onclick = () => this.showTaskLog(task, logs);
        actions.append(logs); card.append(actions); this.el("tasks").append(card);
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
    async withButton(button, action) {
      if (button.disabled) return;
      this.setButtonBusy(button, true);
      try { await action(); }
      catch (error) { this.notice(error.message, true); }
      finally { this.setButtonBusy(button, false); button.disabled = false; }
    }
    async copy(name, button) { await this.copyValue(name === "content" ? this.content : this.fields[name], button); }
    async copyValue(text, button) {
      if (!text) return;
      let failure;
      try { await navigator.clipboard.writeText(text); }
      catch (error) {
        if (this.destroyed) return;
        const active = document.activeElement, input = node("textarea");
        input.value = text; input.className = "wb-copy-fallback"; this.host.append(input); input.select();
        try { if (!document.execCommand("copy")) failure = error; }
        catch (fallbackError) { failure = fallbackError; }
        finally { input.remove(); active?.focus({preventScroll: true}); }
      }
      if (this.destroyed) return;
      this.notice(failure ? `复制失败：${failure.message || "无法访问剪贴板"}。请手动复制。` : "已复制", !!failure);
      if (!button?.isConnected) return;
      const previous = this.copyFeedback.get(button), label = previous?.label || button.textContent;
      this.cancelTimer(previous?.timer);
      button.textContent = failure ? "复制失败" : "已复制";
      button.dataset.copyFeedback = failure ? "error" : "success";
      const timer = this.later(() => {
        button.textContent = label; delete button.dataset.copyFeedback; this.copyFeedback.delete(button);
      }, 1200);
      this.copyFeedback.set(button, {label, timer});
    }
    destroy() { this.destroyed = true; this.epoch++; this.closeDialog(this.el("image-dialog"), false); this.closeDialog(this.el("task-log-dialog"), false); this.abort.abort(); this.timers.forEach(clearTimeout); this.timers.clear(); this.browser?.destroy(); this.context = null; this.resources = []; this.fields = {release_title: "", subtitle: "", overview: ""}; this.content = ""; this.tasks.clear(); this.jobs.clear(); }
  }
  global.BiliWorkbench = {mount: async (host, options) => { instances.get(host)?.destroy(); const app = new Workbench(host, options); instances.set(host, app); return app.init(); }, Workbench, resourceRef: ref};
})(window);
