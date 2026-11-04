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
  const ref = item => {
    if (!["downloads", "torrents", "images", "release_info"].includes(item.root_key) || !item.relative_path || item.relative_path.startsWith("/") || item.relative_path.includes("\\") || item.relative_path.split("/").some(x => ["..", ".", ""].includes(x)) || item.relative_path.includes("\0") || !item.fingerprint) throw new Error("资源引用无效，请在文件浏览器中重新选择");
    return {root_key: item.root_key, relative_path: item.relative_path, kind: item.is_dir || item.kind === "directory" ? "directory" : "file", fingerprint: item.fingerprint};
  };
  class Workbench {
    constructor(host, {api = new global.BiliWorkbenchAPI(), FileBrowser = global.FileBrowser, pollInterval = 2000} = {}) {
      if (!host) throw new Error("工作台缺少挂载节点");
      this.host = host; this.api = api; this.FileBrowser = FileBrowser; this.pollInterval = pollInterval;
      this.context = null; this.resources = []; this.origins = []; this.epoch = 0; this.versions = {}; this.dirty = new Set(); this.editVersion = 0;
      this.fields = {release_title: "", subtitle: "", overview: ""}; this.preview = null; this.content = ""; this.images = []; this.artifacts = []; this.links = [];
      this.fieldVersions = {}; this.tasks = new Map(); this.jobs = new Map(); this.notified = new Set(); this.applied = new Set(); this.pending = new Set(); this.timers = new Set(); this.abort = new AbortController(); this.destroyed = false;
      this.listen(); this.setTab("content");
    }
    el(name) { return this.host.querySelector(`[data-wb="${name}"]`); }
    button(action) { return this.host.querySelector(`[data-action="${action}"]`); }
    async init() {
      if (this.FileBrowser) {
        this.browser = new this.FileBrowser(this.el("browser"), {mode: "select-file", scope: "browse", rootKeys: ["downloads", "torrents", "images", "release_info"], selectionType: "both", allowMultiple: true, onSelectionChange: items => this.select(items), title: "资源文件"});
        try { await this.browser.init(); } catch (error) { this.el("browser").textContent = error.message; this.notice(error.message, true); }
        // The embedded selector uses live selection; no second confirmation step.
        if (this.browser.el.confirmButton) this.browser.el.confirmButton.hidden = true;
      } else this.el("browser").textContent = "统一文件浏览器尚未加载，请由主页面加载共享组件。";
      await this.refreshTasks(true);
      return this;
    }
    listen() {
      const opts = {signal: this.abort.signal};
      this.host.addEventListener("click", event => {
        const tab = event.target.closest("[data-tab]"); if (tab) this.setTab(tab.dataset.tab);
        const copy = event.target.closest("[data-copy]"); if (copy) this.copy(copy.dataset.copy);
        const action = event.target.closest("[data-action]");
        if (action && !action.disabled) this.run(action.dataset.action, action);
      }, opts);
      this.host.addEventListener("input", event => {
        const name = event.target.dataset.edit;
        if (name) { this.fieldVersions[name] = (this.fieldVersions[name] || 0) + 1; this.dirty.add(name); this.setField(name, event.target.value); this.editVersion++; this.scheduleCompose(); }
        if (["naming-rule", "subtitle-rule", "title-rule", "cast-limit", "selected-title", "season", "episode"].includes(event.target.dataset.wb)) this.invalidateRename();
      }, opts);
      this.el("query").addEventListener("keydown", event => { if (event.key === "Enter") { event.preventDefault(); this.run("search", this.button("search")); } }, opts);
      this.el("file-search").addEventListener("input", () => {
        this.cancelTimer(this.searchTimer);
        this.searchTimer = this.later(() => { if (this.browser) { this.browser.search = this.el("file-search").value; this.browser.loadDirectory({page: 1, clearSelection: true}).catch(error => this.notice(error.message, true)); } }, 250);
      }, opts);
      this.el("subtitle-rule").addEventListener("change", () => this.invalidateRename(), opts);
      this.el("cast-limit").addEventListener("change", () => this.invalidateRename(), opts);
      this.el("format").addEventListener("change", () => { this.editVersion++; this.scheduleCompose(); }, opts);
      this.el("scope").addEventListener("change", () => { this.invalidateRename(); if (this.resources.length) this.run("overrides", this.button("overrides")); }, opts);
      this.host.querySelector('[role="tablist"]').addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault(); const tabs = [...this.host.querySelectorAll("[data-tab]")]; let index = tabs.indexOf(document.activeElement);
        index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
        this.setTab(tabs[index].dataset.tab); tabs[index].focus();
      }, opts);
    }
    setTab(name) {
      this.host.querySelectorAll("[data-tab]").forEach(el => { const active = el.dataset.tab === name; el.setAttribute("aria-selected", String(active)); el.tabIndex = active ? 0 : -1; });
      this.host.querySelectorAll("[data-panel]").forEach(el => el.hidden = el.dataset.panel !== name);
    }
    notice(message, error = false) {
      if (this.destroyed) return;
      if ([...this.el("notices").children].some(item => item.querySelector("span")?.textContent === message)) return;
      const item = node("div", null, `wb-notice${error ? " wb-error" : ""}`); item.append(node("span", message));
      const close = node("button", "×"); close.type = "button"; close.setAttribute("aria-label", "关闭通知"); close.onclick = () => item.remove(); item.append(close); this.el("notices").append(item);
      while (this.el("notices").children.length > 3) this.el("notices").firstChild.remove();
      if (!error) this.later(() => item.remove(), 4500);
    }
    later(fn, delay) { const timer = setTimeout(() => { this.timers.delete(timer); if (!this.destroyed) fn(); }, delay); this.timers.add(timer); return timer; }
    cancelTimer(timer) { clearTimeout(timer); this.timers.delete(timer); }
    stamp(channel) { const version = (this.versions[channel] || 0) + 1; this.versions[channel] = version; const epoch = this.epoch; return () => !this.destroyed && epoch === this.epoch && version === this.versions[channel]; }
    async run(action, button) {
      if (this.pending.has(action)) return;
      this.pending.add(action); if (button) { button.disabled = true; button.setAttribute("aria-busy", "true"); }
      try {
        const handlers = {resolve: () => this.resolve(), search: () => this.search(), overrides: () => this.resolve(null, this.overrides()), rules: () => this.applyRules(), naming: () => this.naming(), apply: () => this.applyRename(), title: () => this.generate("release_title", true), subtitle: () => this.generate("subtitle", true), probe: () => this.probe(), screenshots: () => this.screenshots(), report: () => this.report(), torrent: () => this.torrent(), upload: () => this.upload()};
        await handlers[action]?.();
      } catch (error) { this.notice(error.message, true); }
      finally { this.pending.delete(action); button?.removeAttribute("aria-busy"); this.controls(); }
    }
    controls() {
      if (this.destroyed) return;
      const has = this.resources.length > 0, ctx = !!this.context;
      for (const action of ["resolve", "overrides", "probe", "screenshots"]) this.button(action).disabled = !has || this.pending.has(action);
      for (const action of ["naming", "title", "subtitle", "rules"]) this.button(action).disabled = !ctx || this.pending.has(action);
      for (const action of ["report", "torrent"]) this.button(action).disabled = this.resources.length !== 1 || this.pending.has(action);
      this.button("upload").disabled = !this.images.length || this.pending.has("upload");
      this.button("search").disabled = this.pending.has("search");
      this.button("apply").disabled = !this.preview?.can_apply || this.preview?.context_revision !== this.context?.revision || !this.preview?.preview_token || !this.preview?.operations?.length || this.preview?.conflicts?.length > 0 || !(Date.parse(this.preview?.expires_at) > Date.now()) || this.pending.has("apply");
      this.host.querySelectorAll("[data-copy]").forEach(el => el.disabled = !(el.dataset.copy === "content" ? this.content : this.fields[el.dataset.copy]));
    }
    invalidateRename() { this.versions.naming = (this.versions.naming || 0) + 1; this.preview = null; this.el("rename").hidden = true; this.button("apply").disabled = true; }
    invalidateContent() {
      this.content = ""; this.el("output").textContent = ""; this.el("rich-preview").replaceChildren(); this.el("html-preview").hidden = true; this.el("html-preview").removeAttribute("srcdoc");
      this.el("validation").textContent = this.context ? "正在更新预览…" : "等待识别服务返回上下文"; this.controls();
    }
    async select(items, {context = null, origins = []} = {}) {
      this.epoch++; this.versions = {}; this.resources = []; this.context = null; this.origins = origins; this.dirty.clear(); this.editVersion++;
      this.images = []; this.artifacts = []; this.links = []; this.pendingReplacement = null; this.probedResources = null; this.invalidateRename();
      for (const field of Object.keys(this.fields)) this.setField(field, "");
      ["images", "reports", "torrents", "upload-links", "issues", "candidates", "media"].forEach(name => this.el(name).replaceChildren());
      ["naming-rule", "subtitle-rule", "title-rule", "selected-title", "season", "episode"].forEach(name => this.el(name).value = "");
      try {
        if (items.some(item => !item.is_dir && item.kind !== "directory" && item.kind !== "video" && item.kind !== "file")) throw new Error("请选择视频文件或整季目录");
        this.resources = items.map(ref);
        if (this.resources.length > 1000) throw new Error("一次最多选择 1000 个资源");
      } catch (error) { this.resources = []; this.notice(error.message, true); }
      this.el("scope").value = this.resources.some(item => item.kind === "directory") || this.resources.length > 1 ? "season" : "single";
      this.el("resource").textContent = this.resources.length ? `${this.resources.length} 项资源 · ${this.resources[0].relative_path.split("/").at(-1)}` : "选择视频或整季目录";
      this.el("context-summary").textContent = "资料和参数尚未识别";
      this.invalidateContent();
      if (!this.resources.length) return;
      if (context && JSON.stringify(context.resources) === JSON.stringify(this.resources)) { this.context = context; this.renderContext(); await this.generateAll(); }
      else { try { await this.resolve(); } catch (error) { this.el("issues").textContent = error.message; this.notice(error.message, true); } }
      this.controls();
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
    async resolve(selection = null, manual_overrides = {}) {
      if (!this.resources.length) throw new Error("请先选择资源");
      this.invalidateRename(); this.invalidateContent(); const current = this.stamp("context");
      const response = await this.api.resolve({resources: this.resources, origins: this.origins, selection, context: this.context, manual_overrides});
      if (!current()) return;
      if (!response.context || !Number.isInteger(response.context.revision)) throw new Error("识别服务未返回有效上下文");
      this.context = response.context; this.resources = response.context.resources;
      for (const name of ["release_title", "subtitle"]) if (!this.dirty.has(name)) this.setField(name, "");
      this.renderContext(); await this.generateAll();
      const probeKey = JSON.stringify(this.resources);
      if (current() && this.probedResources !== probeKey && (!this.context.files?.length || this.context.files.some(file => !file.probe))) {
        this.probedResources = probeKey;
        try { await this.probe(); } catch (error) { if (current()) this.notice(`媒体参数：${error.message}`, true); }
      }
    }
    renderContext() {
      const ctx = this.context; if (!ctx) return;
      this.el("context-summary").textContent = [val(ctx.selected_title) || "未确认影视身份", ctx.episode?.scope === "season" ? "整季" : "单集 / 单视频", `修订 ${ctx.revision}`, `${ctx.files?.length || 0} 个媒体文件`].join(" · ");
      this.el("selected-title").value = val(ctx.selected_title); this.el("season").value = val(ctx.episode?.season); this.el("episode").value = val(ctx.episode?.episode); this.el("scope").value = ctx.episode?.scope || this.el("scope").value;
      this.el("issues").textContent = [...(ctx.warnings || []), ...(ctx.conflicts || [])].map(x => x.message).join("；");
      this.namingType = ctx.episode?.scope === "season" ? "season" : ctx.identity?.media_type || "series";
      this.el("naming-rule").value = ctx.effective_rules?.naming_rule?.templates?.[this.namingType] || "";
      this.el("naming-rule").disabled = !ctx.effective_rules?.naming_rule;
      this.el("subtitle-rule").value = ctx.effective_rules?.subtitle_rule?.template_id || "";
      this.el("cast-limit").value = String(ctx.effective_rules?.subtitle_rule?.cast_limit || 3);
      if (!this.dirty.has("overview")) this.setField("overview", val(ctx.metadata?.overview));
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
      if (name === "naming-rule") { const rule = this.context?.effective_rules?.naming_rule || fallback; return raw ? {...rule, templates: {...rule.templates, [this.namingType]: raw}} : rule; }
      const rule = this.context?.effective_rules?.subtitle_rule || fallback;
      return {...rule, ...(raw ? {template_id: raw} : {}), cast_limit: this.el("cast-limit").value === "all" ? "all" : Number(this.el("cast-limit").value)};
    }
    async applyRules() {
      if (!this.context) return;
      const naming = this.readRule("naming-rule"), subtitle = this.readRule("subtitle-rule"); this.readRule("title-rule");
      this.context = {...this.context, effective_rules: {...this.context.effective_rules, naming_rule: naming, subtitle_rule: subtitle}};
      this.invalidateRename(); this.invalidateContent(); await this.naming(); await this.generateAll();
    }
    async search() {
      const query = this.el("query").value.trim(); if (!query) throw new Error("请输入作品名称"); const current = this.stamp("search"), text = query;
      const result = await this.api.search(query, this.el("media-type").value); if (!current() || text !== this.el("query").value.trim()) return;
      const container = this.el("candidates"); container.replaceChildren();
      if (!result.items.length) container.append(node("p", "未找到候选，可以调整名称后重试。", "wb-note"));
      result.items.forEach(candidate => {
        const button = node("button", candidate.title || candidate.name || candidate.id); button.type = "button"; button.disabled = !this.resources.length;
        button.onclick = () => this.withButton(button, () => this.resolve({provider: candidate.provider, id: String(candidate.id), media_type: candidate.media_type})); container.append(button);
      });
    }
    setField(name, value) {
      this.fields[name] = String(value ?? ""); this.host.querySelector(`[data-edit="${name}"]`).value = this.fields[name]; this.host.querySelector(`[data-preview="${name}"]`).textContent = this.fields[name]; this.host.querySelector(`[data-lock="${name}"]`).textContent = this.dirty.has(name) ? "人工编辑 · 自动识别会保留此内容" : ""; this.controls();
    }
    async generateAll() {
      const results = await Promise.allSettled([this.generate("release_title"), this.generate("subtitle")]); results.forEach(result => { if (result.status === "rejected") this.notice(result.reason.message, true); }); this.scheduleCompose();
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
      if (result.warnings?.length) this.notice(result.warnings.map(x => x.message).join("；"));
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
        const result = await this.api.compose({context: this.context, ...this.fields, poster: null, reports: this.artifacts.filter(x => x.kind === "report"), screenshots: this.artifacts.filter(x => ["screenshot", "collage", "upload"].includes(x.kind)), links: this.links, format});
        if (!current() || edit !== this.editVersion || revision !== this.context?.revision || result.context_revision !== revision) return;
        this.content = result.content; this.el("output").textContent = result.content;
        this.el("rich-preview").hidden = format === "html";
        if (format !== "html") global.BiliWorkbenchPreview?.render(this.el("rich-preview"), result.content, format);
        const messages = [...(result.validation.errors || []), ...(result.validation.warnings || [])].map(x => typeof x === "string" ? x : x.message);
        this.el("validation").textContent = (result.validation.ready ? "发布内容已就绪" : "内容尚有缺项，可预览复制") + (messages.length ? ` · ${messages.join("；")}` : "");
        if (format === "html") { this.el("html-preview").srcdoc = '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; img-src https: http:; style-src &#39;unsafe-inline&#39;"><style>body{font:14px/1.6 system-ui;overflow-wrap:anywhere}img{max-width:100%}pre{white-space:pre-wrap}</style>' + result.content; this.el("html-preview").hidden = false; }
        this.controls();
      } catch (error) { if (current() && edit === this.editVersion) { this.el("validation").textContent = error.message; this.notice(error.message, true); } }
    }
    async naming() {
      const current = this.stamp("naming"), revision = this.context.revision;
      const preview = await this.api.naming(this.context, this.readRule("naming-rule", this.context.effective_rules?.naming_rule || {}));
      if (!current() || this.context?.revision !== revision || preview.context_revision < revision) return;
      this.context = {...this.context, revision: preview.context_revision};
      this.invalidateContent(); this.scheduleCompose();
      this.preview = preview; this.applyKey = global.crypto.randomUUID(); this.el("rename-list").replaceChildren();
      preview.operations.forEach(op => { const row = node("div", null, "wb-rename-row"); row.append(node("span", op.source.relative_path), node("strong", `→ ${op.target_relative_path}`)); this.el("rename-list").append(row); });
      this.el("rename-warning").textContent = (preview.conflicts || []).map(x => x.message).join("；") || "请核对全部目标路径。确认后由服务端复核指纹与冲突，再执行原地改名。";
      this.el("rename").hidden = false; this.controls();
      const delay = Date.parse(preview.expires_at) - Date.now(); if (Number.isFinite(delay) && delay > 0) this.later(() => { if (this.preview === preview) { this.invalidateRename(); this.notice("命名预览已过期，请重新预览"); } }, Math.min(delay, 2147483647));
    }
    async applyRename() {
      if (!this.preview?.can_apply || !this.preview.preview_token || !this.preview.operations?.length || this.preview.conflicts?.length > 0 || this.preview.context_revision !== this.context?.revision || !(Date.parse(this.preview.expires_at) > Date.now())) throw new Error("命名预览已失效，请重新预览");
      const preview = this.preview, epoch = this.epoch, resources = this.resources, response = await this.api.apply(preview, this.applyKey);
      this.track(response.task, {type: "rename", epoch, resources, operations: preview.operations}); if (this.preview === preview) this.invalidateRename();
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
      const result = await this.api.request("/api/files/screenshots", {sources: this.sources(), total, columns, output_mode: this.el("image-mode").value, auto_upload: false, content_type: this.context?.identity?.media_type || "custom", skip_start_seconds: skipStart, skip_end_seconds: skipEnd});
      this.track(result.task, {type: "screenshot", session: result.session_id, epoch});
    }
    async report() {
      const epoch=this.epoch, source=this.resources[0];
      const result=await this.api.request('/api/v2/reports',source);
      this.el('reports').textContent='正在后台生成媒体报告…';
      this.track(result.task,{type:'report',epoch});
    }
    async torrent() {
      const source = this.resources[0], epoch = this.epoch;
      const trackers = this.el("trackers").value.split(/\r?\n/).map(x => x.trim()).filter(Boolean);
      const result = await this.api.request("/api/torrents/jobs", {source_root_key: source.root_key, source_relative_path: source.relative_path, private: this.el("private").checked, overwrite: false, ...(trackers.length ? {trackers} : {})});
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
        this.context = null; this.resources = []; this.invalidateRename(); this.invalidateContent();
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
          const text=report.content || report.text || '', copy=node('button','复制报告');
          copy.type='button'; copy.onclick=()=>this.copyValue(text);
          this.el('reports').append(copy,node('pre',text));
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
        const row = node("p"), link = node("a", url); link.href = safeURL(url); link.target = "_blank"; link.rel = "noopener noreferrer"; row.append(link); this.el("upload-links").append(row);
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
      const card = node("article", null, "wb-task"), link = node("a", "下载种子"), add = node("button", "添加到 qBittorrent"); add.type = "button";
      link.href = `/api/torrents/${encodeURIComponent(record.id)}/file`; card.append(node("strong", record.name || record.filename || ".torrent"), node("p", record.info_hash || ""), link, add);
      add.onclick = () => this.withButton(add, async () => { await this.api.request("/api/qbittorrent/torrents", {torrent_build_id: record.id}); this.notice("种子已提交到 qBittorrent"); }); this.el("torrents").append(card);
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
