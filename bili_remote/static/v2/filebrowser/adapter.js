(function () {
  "use strict";
  const ROOTS = ["downloads", "torrents", "images", "release_info"];
  const signature = refs => JSON.stringify(refs.map(ref => [ref.root_key, ref.relative_path, ref.kind, ref.fingerprint]).sort());

  function normalizedPath(path) {
    return String(path || "").replace(/\/+$/, "");
  }

  function isDescendant(path, ancestorPath) {
    const ancestor = normalizedPath(ancestorPath);
    const candidate = normalizedPath(path);
    if (!ancestor) return candidate !== "";
    return candidate !== ancestor && candidate.startsWith(ancestor + "/");
  }

  // Batch-selecting a directory already covers every file inside it. Drop
  // descendant files/directories so a handoff never double-counts them.
  function pruneDescendants(items, defaultRootKey) {
    return items.filter((item, index, all) => {
      const key = item.root_key || defaultRootKey;
      return !all.some((other, otherIndex) => {
        if (otherIndex === index) return false;
        return other.is_dir && (other.root_key || defaultRootKey) === key && isDescendant(item.relative_path, other.relative_path);
      });
    });
  }

  // Keep one page-owned context. Never create a second resolver or naming algorithm.
  class FileBrowserAdapter {
    constructor({getContext = () => null, getContextVersion = () => null, setContext, openWorkbench, openNaming, request = api} = {}) {
      this.getContext = getContext;
      this.getContextVersion = getContextVersion;
      this.setContext = setContext;
      this.openWorkbench = openWorkbench;
      this.openNaming = openNaming;
      this.request = request;
      this.sequence = 0;
    }

    async select(items, rootKey) {
      if (!items.length || items.length > 1000) throw new Error("请选择 1 至 1000 项资源");
      const effective = pruneDescendants(items, rootKey);
      return Promise.all(effective.map(async item => {
        const key = item.root_key || rootKey;
        if (!ROOTS.includes(key)) throw new Error("此目录不属于四类资源目录");
        const query = new URLSearchParams({root_key: key, path: item.relative_path});
        const fresh = await this.request(`/api/files/meta?${query}`);
        if (!fresh.allowed || fresh.is_symlink || !fresh.fingerprint) throw new Error("资源不可访问，请刷新目录");
        if (item.fingerprint && item.fingerprint !== fresh.fingerprint) throw new Error("资源已变化，请刷新目录后重新选择");
        return await this.request('/api/v2/resources/reference', {method:'POST',body:JSON.stringify({root_key: key, relative_path: fresh.relative_path, kind: fresh.is_dir ? "directory" : "file", fingerprint: fresh.fingerprint})});
      }));
    }

    async contextFor(items, rootKey, {commit = true} = {}) {
      const sequence = ++this.sequence;
      const previous = this.getContext(), version = this.getContextVersion();
      const revision = previous?.revision;
      const refs = await this.select(items, rootKey);
      let context = previous;
      if (!context || signature(context.resources || []) !== signature(refs)) {
        const locations = values => JSON.stringify(values.map(x => [x.root_key,x.relative_path,x.kind]).sort());
        const sameResources = previous && locations(previous.resources || []) === locations(refs);
        const result = await this.request("/api/v2/resources/resolve", {
          method: "POST",
          body: JSON.stringify({resources: refs, origins: sameResources ? previous.origins || [] : [], selection: null, context: sameResources ? previous : null, manual_overrides: {}})
        });
        context = result.context;
        if (!context || context.schema_version !== 1 || !Number.isInteger(context.revision) ||
            !context.context_id || JSON.stringify((context.resources || []).map(x => [x.root_key,x.relative_path,x.kind])) !== JSON.stringify(refs.map(x => [x.root_key,x.relative_path,x.kind]))) {
          throw new Error("识别服务返回的资源上下文不匹配");
        }
      }
      if (sequence !== this.sequence || this.getContext() !== previous || this.getContextVersion() !== version || previous?.revision !== revision) throw new Error("资源选择已更新，请重试");
      if (commit && context !== previous) {
        if (!this.setContext) throw new Error("尚未连接共享资源上下文");
        this.setContext(context);
      }
      return context;
    }

    async handoff(destination, items, rootKey) {
      const callback = destination === "naming" ? this.openNaming : this.openWorkbench;
      if (!callback) throw new Error("尚未连接对应工作台入口");
      const previous = this.getContext();
      const context = await this.contextFor(items, rootKey, {commit: false});
      if (await callback(context) === false) return false;
      // The destination owns confirmation and may already have committed a newer context.
      if (this.getContext() === previous && context !== previous) this.setContext?.(context);
      return context;
    }

    async mediaInfo(item, rootKey, {render = null} = {}) {
      const context = await this.contextFor([item], rootKey, {commit: false});
      const ref = context.resources[0];
      const file = (context.files || []).find(entry => entry.ref.root_key === ref.root_key && entry.ref.relative_path === ref.relative_path);
      const panel = element("div", "fb-media-info operation-dialog");
      panel.append(element("h2", null, `${item.name} · 媒体信息`));
      const fields = [
        ["resolution", "分辨率"], ["width", "宽度"], ["height", "高度"],
        ["video_codec", "视频编码"], ["audio_codec", "音频编码"], ["audio_channels", "声道"],
        ["frame_rate", "帧率"], ["bit_depth", "位深"], ["hdr", "HDR"],
        ["duration_seconds", "时长（秒）"], ["container", "容器"]
      ];
      const list = element("dl");
      for (const [key, label] of fields) {
        const field = file?.probe?.[key];
        const value = field?.value;
        let text = value == null ? "未知" : typeof value === "object" ? JSON.stringify(value) : String(value);
        if (value != null && field.source) text += `（${field.source === "manual" ? "人工" : field.source}）`;
        list.append(element("dt", null, label), element("dd", null, text));
      }
      panel.append(list);
      if (file?.probe?.probe_error) panel.append(element("p", "form-error", String(file.probe.probe_error)));
      for (const warning of context.warnings || []) panel.append(element("p", "rename-warning", warning.message));
      if (!file?.probe) panel.append(element("p", "rename-warning", "尚无媒体探测结果，请在识别与命名区域重新探测。"));
      if (render) { render(panel); return context; }
      const close = document.createElement("button");
      close.type = "button"; close.className = "button ghost"; close.textContent = "关闭";
      close.addEventListener("click", closeModal); panel.append(close);
      showModal(panel, {stack: true});
      return context;
    }

    mount(host, options = {}) {
      return new FileBrowser(host, {
        ...options, scope: "browse", rootKeys: ROOTS, mode: "full", allowMultiple: true,
        onLoadStart: () => {
          ++this.sequence;
          options.onLoadStart?.();
        },
        onSelectionChange: items => {
          ++this.sequence;
          options.onSelectionChange?.(items);
        },
        onOpenWorkbench: (items, root) => this.handoff("workbench", items, root),
        onOpenNaming: (items, root) => this.handoff("naming", items, root),
        onMediaInfo: (item, root, options) => this.mediaInfo(item, root, options)
      });
    }
  }
  window.FileBrowserAdapter = FileBrowserAdapter;
})();
