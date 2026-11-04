(function () {
  "use strict";

  const stylesheet = document.createElement("link");
  stylesheet.rel = "stylesheet";
  stylesheet.href = new URL("v2/filebrowser/filebrowser.css", document.currentScript?.src || new URL("/static/file-browser.js", location.href).href).href;
  document.head.append(stylesheet);

  const DEFAULT_SORTS = [
    ["natural", "自然排序"],
    ["name", "名称 A→Z"],
    ["mtime", "修改时间"],
    ["size", "文件大小"]
  ];

  function icon(kind, isDir) {
    const node = element("span", `file-icon ${isDir ? "folder" : "document"}`);
    node.setAttribute("aria-hidden", "true");
    node.textContent = isDir ? "▰" : kind === "image" ? "▧" : kind === "video" ? "▶" : "▤";
    return node;
  }

  function itemKey(rootKey, relativePath) {
    return `${rootKey}\n${relativePath}`;
  }

  function parentPath(path) {
    const parts = String(path || "").split("/").filter(Boolean);
    parts.pop();
    return parts.join("/");
  }

  function basename(path) {
    const parts = String(path || "").split("/").filter(Boolean);
    return parts.at(-1) || "";
  }

  function formatDate(value) {
    const nanos = Number(value);
    if (!Number.isFinite(nanos) || nanos <= 0) return "—";
    return new Date(nanos / 1_000_000).toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit"
    });
  }

  function button(text, className = "button ghost small") {
    const node = element("button", className, text);
    node.type = "button";
    return node;
  }

  function busy(target, active) {
    target.disabled = active;
    target.classList.toggle("is-busy", active);
    target.setAttribute("aria-busy", active ? "true" : "false");
  }

  function previewKind(item) {
    if (!item || item.is_dir) return "directory";
    return ["image", "text", "video"].includes(item.kind) ? item.kind : "other";
  }

  class FileBrowser {
    static _defaults = {
      pageSize: 100,
      sort: "natural",
      direction: "asc"
    };

    static setDefaults(values = {}) {
      const pageSize = Number(values.file_browser_page_size);
      if (Number.isFinite(pageSize) && pageSize >= 20 && pageSize <= 1000) {
        FileBrowser._defaults.pageSize = pageSize;
      }
      if (DEFAULT_SORTS.some(item => item[0] === values.file_browser_default_sort)) {
        FileBrowser._defaults.sort = values.file_browser_default_sort;
      }
      if (["asc", "desc"].includes(values.file_browser_default_direction)) {
        FileBrowser._defaults.direction = values.file_browser_default_direction;
      }
    }

    constructor(host, options = {}) {
      if (!host) throw new Error("文件浏览器缺少挂载节点");
      this.host = host;
      this.mode = options.mode || "full";
      this.scope = options.scope || "browse";
      this.rootKeys = options.rootKeys || null;
      this.selectionType = options.selectionType || (this.mode === "full" ? "both" : "file");
      this.allowMultiple = Boolean(options.allowMultiple);
      this.onConfirm = options.onConfirm || null;
      this.onSelectionChange = options.onSelectionChange || null;
      this.search = "";
      this.searchTimer = null;
      this.destroyed = false;
      this.taskTimers = new Set();
      this.onOpenWorkbench = options.onOpenWorkbench || null;
      this.onOpenNaming = options.onOpenNaming || null;
      this.onMediaInfo = options.onMediaInfo || null;
      this.onLoadStart = options.onLoadStart || null;
      this.onMediaRename = options.onMediaRename || null;
      this.onCollage = options.onCollage || null;
      this.onTool = options.onTool || null;
      this.confirmLabel = options.confirmLabel || "选择";
      this.title = options.title || "文件浏览";
      this.roots = [];
      this.rootKey = options.rootKey || null;
      this.path = options.path || "";
      this.page = 1;
      this.pageSize = options.pageSize || FileBrowser._defaults.pageSize;
      this.sort = options.sort || FileBrowser._defaults.sort;
      this.direction = options.direction || FileBrowser._defaults.direction;
      this.data = null;
      this.selected = new Map();
      this.treeCache = new Map();
      this.expanded = new Set();
      this.loadSeq = 0;
      this.clipboard = null;
      this.taskTimer = null;
      this.el = {};
      this.build();
    }

    build() {
      this.host.replaceChildren();
      const shell = element("div", "unified-file-browser fb-v2");
      const heading = element("div", "file-browser-heading");
      heading.append(element("strong", null, this.title));
      if (this.mode !== "full") {
        heading.append(button("刷新", "button ghost small"));
      }
      const headingRefresh = heading.querySelector("button");
      if (headingRefresh) headingRefresh.addEventListener("click", () => this.refresh());

      const roots = element("nav", "root-shortcuts");
      roots.setAttribute("aria-label", "目录入口");

      const browserShell = element("section", "file-browser-shell");
      const treePane = element("aside", "file-tree-pane");
      treePane.setAttribute("aria-label", "目录树");
      const treeHeading = element("div", "file-pane-heading");
      const treeText = element("div");
      treeText.append(element("strong", null, "目录树"));
      treeText.append(element("span", "muted", "按需展开目录和文件"));
      const treeRefresh = button("刷新", "button ghost small");
      treeRefresh.addEventListener("click", () => this.refresh());
      treeHeading.append(treeText, treeRefresh);
      const tree = element("div", "file-tree");
      tree.setAttribute("aria-live", "polite");
      treePane.append(treeHeading, tree);
      treePane.id = `fb-tree-${FileBrowser._nextId = (FileBrowser._nextId || 0) + 1}`;
      const treeToggle = button("目录树", "button ghost small fb-tree-toggle");
      treeToggle.setAttribute("aria-controls", treePane.id);
      treeToggle.setAttribute("aria-expanded", "false");
      treeToggle.addEventListener("click", () => {
        const open = shell.classList.toggle("fb-tree-open");
        treeToggle.setAttribute("aria-expanded", String(open));
      });
      heading.append(treeToggle);

      const listPane = element("section", "file-list-pane");
      const toolbar = element("div", "file-list-toolbar");
      const pathBlock = element("div", "file-path-block");
      const breadcrumbs = element("nav", "file-breadcrumbs");
      breadcrumbs.setAttribute("aria-label", "当前位置");
      const meta = element("p", "muted", "正在读取目录…");
      pathBlock.append(breadcrumbs, meta);
      const toolbarActions = element("div", "file-toolbar-actions");
      const selectionCount = element("span", "selection-count", "未选择");
      const selectionHint = element("span", "file-selection-hint");
      selectionHint.setAttribute("role", "status");
      toolbarActions.append(selectionCount, selectionHint);

      const sortSelect = document.createElement("select");
      sortSelect.className = "file-sort-select";
      sortSelect.setAttribute("aria-label", "文件排序");
      for (const [value, label] of DEFAULT_SORTS) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = label;
        sortSelect.append(option);
      }
      sortSelect.addEventListener("change", () => {
        this.sort = sortSelect.value;
        this.loadDirectory({page: 1, clearSelection: true});
      });
      sortSelect.value = this.sort;
      const directionButton = button("", "button ghost small file-direction-button");
      directionButton.setAttribute("aria-label", "切换排序方向");
      const syncDirection = () => {
        directionButton.textContent = this.direction === "asc" ? "升序" : "降序";
        directionButton.setAttribute("aria-pressed", this.direction === "desc" ? "true" : "false");
      };
      directionButton.addEventListener("click", () => {
        this.direction = this.direction === "asc" ? "desc" : "asc";
        syncDirection();
        this.loadDirectory({page: 1, clearSelection: true});
      });
      syncDirection();
      toolbarActions.append(sortSelect, directionButton);
      {
        const search = document.createElement("input");
        search.type = "search"; search.className = "file-search-input";
        search.placeholder = "搜索当前目录"; search.setAttribute("aria-label", "搜索当前目录");
        this.el.search = search;
        search.addEventListener("input", () => {
          clearTimeout(this.searchTimer);
          this.searchTimer = setTimeout(() => {this.search = search.value; this.loadDirectory({page: 1, clearSelection: true});}, 200);
        });
        toolbarActions.append(search);
      }

      const operationButtons = {};
      const more = element("details", "file-more-menu");
      const summary = element("summary", "button ghost small", "更多操作");
      const moreItems = element("div", "file-more-items");
      more.append(summary,moreItems);
      if (this.mode === "full") {
        for (const [key, label] of [
          ["preview", "预览/编辑"],
          ["rename", "重命名"],
          ["copy", "复制"],
          ["cut", "剪切"],
          ["paste", "粘贴"],
          ["move", "移动到"],
          ["delete", "删除"],
          ["mediaRename", this.onOpenNaming ? "智能命名" : "批量重命名"],
          ["workbench", "进入发种工作台"],
          ["collage", "生成缩略图"],
          ["report", "媒体报告"], ["upload", "上传图片"], ["torrent", "制作种子"], ["publish", "发布资料"]
        ]) {
          if (key === "workbench" && !this.onOpenWorkbench) continue;
          const node = button(label, key === "delete" ? "button danger small" : "button ghost small");
          node.addEventListener("click", () => this.runAction(key));
          if (["copy","cut","paste","move","delete"].includes(key)) {
            moreItems.append(node);
            node.addEventListener("click",()=>more.open=false);
          } else toolbarActions.append(node);
          operationButtons[key] = node;
        }
      }
      if (this.mode === "full") toolbarActions.append(more);
      if (this.allowMultiple) {
        const selectAll = element("label", "inline-check");
        const selectAllInput = document.createElement("input");
        selectAllInput.type = "checkbox";
        selectAllInput.addEventListener("change", () => this.selectAll(selectAllInput.checked));
        selectAll.append(selectAllInput, element("span", null, "全选当前页"));
        toolbarActions.prepend(selectAll);
        this.el.selectAll = selectAllInput;
      }
      if (this.mode !== "full") {
        const confirm = button(this.confirmLabel, "button primary small");
        confirm.addEventListener("click", () => this.confirm());
        toolbarActions.append(confirm);
        this.el.confirmButton = confirm;
      }
      toolbar.append(pathBlock, toolbarActions);

      const listError = element("p", "form-error");
      listError.setAttribute("role", "alert");
      const actionStatus = element("p", "file-action-status");
      actionStatus.setAttribute("role", "status");
      const listHead = element("div", "file-list-head");
      listHead.setAttribute("aria-hidden", "true");
      listHead.append(
        element("span"),
        element("span"),
        element("span", null, "名称"),
        element("span", null, "大小"),
        element("span", null, "修改时间")
      );
      const list = element("div", "file-list");
      list.setAttribute("aria-live", "polite");
      const pager = element("div", "file-pager hidden");
      const prev = button("上一页");
      const pageLabel = element("span", "muted");
      const next = button("下一页");
      prev.addEventListener("click", () => this.loadDirectory({page: Math.max(1, this.page - 1), clearSelection: true}));
      next.addEventListener("click", () => this.loadDirectory({page: this.page + 1, clearSelection: true}));
      pager.append(prev, pageLabel, next);
      listPane.append(toolbar, listError, actionStatus, listHead, list, pager);
      browserShell.append(treePane, listPane);
      shell.append(heading, roots, browserShell);
      this.host.append(shell);

      Object.assign(this.el, {
        roots,
        tree,
        breadcrumbs,
        meta,
        list,
        listError,
        actionStatus,
        listHead,
        pager,
        prev,
        next,
        pageLabel,
        selectionCount,
        selectionHint,
        operationButtons,
        sortSelect,
        directionButton
      });
      this.updateOperationButtons();
    }

    async init() {
      await this.loadRoots();
      if (!this.roots.length) throw new Error("没有可浏览的目录");
      const preferred = this.rootKey && this.roots.some(root => root.key === this.rootKey)
        ? this.rootKey
        : this.roots.some(root => root.key === "downloads")
          ? "downloads"
          : this.roots[0].key;
      await this.navigate(preferred, this.rootKey === preferred ? this.path : "", {keepTree: false});
    }

    async loadRoots() {
      const roots = await api(`/api/files/roots?scope=${encodeURIComponent(this.scope)}`);
      this.roots = this.rootKeys
        ? roots.filter(root => this.rootKeys.includes(root.key))
        : roots;
      this.renderRoots();
    }

    root(rootKey = this.rootKey) {
      return this.roots.find(root => root.key === rootKey) || null;
    }

    renderRoots() {
      this.el.roots.replaceChildren();
      for (const root of this.roots) {
        const node = button("", "root-shortcut");
        node.classList.toggle("active", root.key === this.rootKey);
        node.title = root.path;
        const marker = element("span", "root-marker");
        const text = element("span", "root-text");
        text.append(element("strong", null, root.label));
        text.append(element("small", null, root.available === false ? "目录不可访问" : root.path));
        node.append(marker, text);
        node.addEventListener("click", () => this.navigate(root.key, ""));
        this.el.roots.append(node);
      }
    }

    renderBreadcrumbs() {
      this.el.breadcrumbs.replaceChildren();
      const root = this.root();
      if (!root) return;
      const rootButton = button(root.label, "crumb");
      rootButton.addEventListener("click", () => this.navigate(root.key, ""));
      this.el.breadcrumbs.append(rootButton);
      if (!this.path) return;
      const parts = this.path.split("/").filter(Boolean);
      parts.forEach((part, index) => {
        this.el.breadcrumbs.append(element("span", "crumb-separator", "/"));
        const node = button(part, "crumb");
        node.addEventListener("click", () => this.navigate(root.key, parts.slice(0, index + 1).join("/")));
        this.el.breadcrumbs.append(node);
      });
    }

    async navigate(rootKey, path, options = {}) {
      this.rootKey = rootKey;
      this.path = path || "";
      this.page = 1;
      clearTimeout(this.searchTimer);
      this.search = "";
      if (this.el.search) this.el.search.value = "";
      this.selected.clear();
      this.renderRoots();
      this.renderBreadcrumbs();
      await this.loadDirectory({page: 1, clearSelection: true, focus: options.focus});
      this.drawTree();
      const navigationKey = this.treeKey(rootKey, this.path);
      this.ensureTreeChain(rootKey, this.path)
        .then(() => {
          if (this.treeKey(this.rootKey, this.path) === navigationKey) this.drawTree();
        })
        .catch(error => {
          if (this.treeKey(this.rootKey, this.path) === navigationKey) {
            this.el.tree.append(element("p", "form-error", error.message));
          }
        });
    }

    async loadDirectory({page = 1, clearSelection = false, focus = null} = {}) {
      if (!this.rootKey) return;
      if (this.destroyed) return;
      const seq = ++this.loadSeq;
      if (clearSelection) this.selected.clear();
      this.el.listError.textContent = "";
      this.data = null;
      this.el.list.replaceChildren();
      this.updateSelection(false);
      this.onLoadStart?.();
      this.el.meta.textContent = "正在读取目录…";
      try {
        const query = new URLSearchParams({
          root_key: this.rootKey,
          path: this.path,
          page: String(page),
          page_size: String(this.pageSize),
          scope: this.scope,
          sort: this.sort,
          direction: this.direction,
          search: this.search
        });
        if (focus) query.set("focus", focus);
        const data = await api(`/api/files/browse?${query.toString()}`);
        if (seq !== this.loadSeq) return;
        if (page > 1 && data.items.length === 0 && data.total > 0) {
          const lastPage = Math.max(1, Math.ceil(data.total / this.pageSize));
          if (lastPage !== page) {
            await this.loadDirectory({page: lastPage, clearSelection});
            return;
          }
        }
        this.page = data.page;
        this.data = data;
        if (
          !this.search && data.page === 1
          && this.sort === "natural"
          && this.direction === "asc"
        ) {
          this.treeCache.set(this.treeKey(this.rootKey, data.relative_path), {
            items: data.items,
            page: data.page,
            has_more: data.has_more
          });
        }
        if (focus) {
          const focused = data.items.find(item => item.relative_path === focus);
          if (focused && !focused.is_dir && this.isSelectable(focused)) {
            this.selected.set(itemKey(this.rootKey, focused.relative_path), focused);
          }
        }
        this.renderBreadcrumbs();
        this.renderMeta();
        this.renderList();
        this.renderPager();
        this.updateSelection(false);
      } catch (error) {
        if (seq !== this.loadSeq) return;
        this.el.meta.textContent = "目录读取失败";
        this.el.list.replaceChildren();
        this.el.listError.textContent = error.message;
        this.selected.clear();
        this.updateSelection(false);
      }
    }

    renderMeta() {
      const data = this.data;
      const root = this.root();
      if (!data || !root) return;
      const dirs = data.items.filter(item => item.is_dir).length;
      const files = data.items.length - dirs;
      this.el.meta.textContent = `${root.label} · ${data.total} 项 · 当前页 ${dirs} 个文件夹、${files} 个文件`;
    }

    isSelectable(item) {
      if (!item || !item.allowed) return false;
      if (this.selectionType === "file") return !item.is_dir;
      if (this.selectionType === "directory") return item.is_dir;
      return true;
    }

    renderList() {
      this.el.list.replaceChildren();
      if (!this.data || !this.data.items.length) {
        this.el.list.append(element("p", "empty", this.search ? "没有匹配的文件" : "这个目录是空的"));
        return;
      }
      for (const item of this.data.items) {
        const row = element("div", `file-row ${item.is_dir ? "directory" : "file"}`);
        row.classList.toggle("selected", this.selected.has(itemKey(this.rootKey, item.relative_path)));
        row.classList.toggle("blocked", !item.allowed);
        row.dataset.path = item.relative_path;
        row.tabIndex = item.allowed ? 0 : -1;
        row.setAttribute("aria-label", item.name);
        row.addEventListener("keydown", event => {
          if (event.target !== row || !item.allowed) return;
          if (event.key === "Enter") {
            event.preventDefault();
            if (item.is_dir) this.navigate(this.rootKey, item.relative_path);
            else this.openPreview(item);
          } else if (event.key === " ") {
            event.preventDefault(); this.toggleItem(item); this.updateSelection();
          }
        });
        if (this.isSelectable(item)) {
          const check = document.createElement("input");
          check.type = "checkbox";
          check.className = "file-check";
          check.checked = this.selected.has(itemKey(this.rootKey, item.relative_path));
          check.setAttribute("aria-label", `选择 ${item.name}`);
          check.addEventListener("click", event => event.stopPropagation());
          check.addEventListener("change", () => {
            if (check.checked) this.selectItem(item);
            else this.unselectItem(item);
            this.updateSelection();
          });
          row.append(check);
        } else {
          row.append(element("span", "file-check-placeholder"));
        }
        if (item.kind === "image" && item.allowed && !item.is_dir) {
          const thumbnail = document.createElement("img");
          thumbnail.className = "file-preview-thumb"; thumbnail.alt = ""; thumbnail.loading = "lazy";
          thumbnail.src = `/api/files/image?${new URLSearchParams({root_key: this.rootKey, path: item.relative_path})}`;
          thumbnail.addEventListener("error", () => thumbnail.replaceWith(icon(item.kind, false)));
          row.append(thumbnail);
        } else row.append(icon(item.kind, item.is_dir));
        const name = element("span", "file-name", item.name);
        name.title = item.name;
        if (item.is_dir) name.classList.add("folder-name");
        if (!item.allowed) name.append(element("span", "file-blocked-note", "不可访问"));
        else if (item.protection_reason) {
          const note = element("span", "file-protected-note", "受保护");
          note.title = item.protection_reason;
          name.append(note);
          row.title = item.protection_reason;
        }
        row.append(name, element("span", "file-size", item.is_dir ? "—" : formatBytes(item.size_bytes)), element("span", "file-date", formatDate(item.mtime_ns)));
        row.addEventListener("click", event => {
          if (event.target.closest("input")) return;
          if (!item.allowed) return;
          if (item.is_dir) {
            this.navigate(this.rootKey, item.relative_path);
            return;
          }
          if (this.isSelectable(item)) this.toggleItem(item);
          this.updateSelection();
        });
        row.addEventListener("dblclick", event => {
          if (event.target.closest("input") || !item.allowed) return;
          if (!item.is_dir) this.openPreview(item);
        });
        if (this.mode === "full") {
          row.addEventListener("contextmenu", event => {
            event.preventDefault();
            this.openContextMenu(event, item);
          });
        }
        this.el.list.append(row);
      }
    }

    renderPager() {
      const data = this.data;
      if (!data || data.total <= this.pageSize) {
        this.el.pager.classList.add("hidden");
        return;
      }
      const pages = Math.max(1, Math.ceil(data.total / this.pageSize));
      this.el.pager.classList.remove("hidden");
      this.el.pageLabel.textContent = `第 ${data.page} / ${pages} 页`;
      this.el.prev.disabled = data.page <= 1;
      this.el.next.disabled = !data.has_more;
    }

    selectItem(item) {
      if (!this.isSelectable(item)) return;
      if (!this.allowMultiple) this.selected.clear();
      this.selected.set(itemKey(this.rootKey, item.relative_path), item);
    }

    unselectItem(item) {
      this.selected.delete(itemKey(this.rootKey, item.relative_path));
    }

    toggleItem(item) {
      const key = itemKey(this.rootKey, item.relative_path);
      if (this.selected.has(key)) this.selected.delete(key);
      else this.selectItem(item);
    }

    selectAll(checked) {
      for (const item of this.data?.items || []) {
        if (this.isSelectable(item)) {
          if (checked) this.selectItem(item);
          else this.unselectItem(item);
        }
      }
      this.renderList();
      this.updateSelection();
    }

    selectedItems() {
      return [...this.selected.values()];
    }

    selectedVideos() {
      return this.selectedItems().filter(
        item => !item.is_dir && item.kind === "video" && item.capabilities?.rename !== false
      );
    }

    selectedCollageVideos() {
      return this.selectedItems().filter(
        item => (item.is_dir || item.kind === "video") && item.allowed !== false
      );
    }

    updateSelection(notify = true) {
      const count = this.selected.size;
      this.el.selectionCount.textContent = count ? `已选择 ${count} 项` : "未选择";
      if (this.el.selectionHint) {
        const items = this.selectedItems();
        const protectedItems = items.filter(item => item.protection_reason);
        const videos = this.selectedVideos().length;
        const ignored = items.filter(item => !item.is_dir && item.kind !== "video").length;
        const notes = [];
        if (protectedItems.length) {
          notes.push(`${protectedItems.length} 项受活动任务保护：${protectedItems[0].protection_reason}`);
        }
        if (videos && ignored) notes.push(`剧集重命名将忽略 ${ignored} 个非视频文件`);
        this.el.selectionHint.textContent = notes.join("；");
      }
      if (this.el.selectAll) {
        const selectable = (this.data?.items || []).filter(item => this.isSelectable(item));
        this.el.selectAll.indeterminate = selectable.some(item => this.selected.has(itemKey(this.rootKey, item.relative_path))) && !selectable.every(item => this.selected.has(itemKey(this.rootKey, item.relative_path)));
        this.el.selectAll.checked = selectable.length > 0 && selectable.every(item => this.selected.has(itemKey(this.rootKey, item.relative_path)));
      }
      for (const row of this.el.list.querySelectorAll(".file-row")) {
        const selected = this.selected.has(itemKey(this.rootKey, row.dataset.path));
        row.classList.toggle("selected", selected);
        const check = row.querySelector(".file-check");
        if (check) check.checked = selected;
      }
      this.updateOperationButtons();
      if (notify) this.onSelectionChange?.(this.selectedItems().map(item => ({...item, root_key: this.rootKey})));
    }

    updateOperationButtons() {
      const buttons = this.el.operationButtons || {};
      const count = this.selected.size;
      const items = this.selectedItems();
      const protectionReason = items.find(item => item.protection_reason)?.protection_reason || "";
      for (const key of ["rename", "copy", "cut", "delete", "move", "preview", "mediaRename", "collage"]) {
        if (!buttons[key]) continue;
        buttons[key].disabled = count === 0;
        buttons[key].removeAttribute("title");
      }
      if (buttons.rename) buttons.rename.disabled = count !== 1;
      if (buttons.preview) buttons.preview.disabled = count !== 1 || items[0]?.is_dir;
      if (buttons.mediaRename) {
        buttons.mediaRename.disabled = this.onOpenNaming ? !count : (!this.onMediaRename || this.selectedVideos().length === 0);
        buttons.mediaRename.title = count && buttons.mediaRename.disabled
          ? "请选择至少一个可重命名的视频文件"
          : "";
      }
      if (buttons.workbench) buttons.workbench.disabled = count === 0;
      if (buttons.collage) {
        buttons.collage.disabled = !this.onCollage || this.selectedCollageVideos().length === 0;
        buttons.collage.title = count && buttons.collage.disabled
          ? "请选择至少一个可访问的视频文件"
          : "";
      }
      if (buttons.report) buttons.report.disabled = count !== 1 || !(items[0]?.is_dir || items[0]?.kind === "video");
      if (buttons.torrent) buttons.torrent.disabled = count !== 1;
      if (buttons.publish) buttons.publish.disabled = count !== 1;
      if (buttons.upload) buttons.upload.disabled = !items.some(item => !item.is_dir && item.kind === "image");
      if (buttons.copy) buttons.copy.disabled = count === 0 || items.some(item => item.capabilities?.copy === false);
      if (buttons.cut) buttons.cut.disabled = count === 0 || items.some(item => item.capabilities?.move === false);
      if (buttons.move) buttons.move.disabled = count === 0 || items.some(item => item.capabilities?.move === false);
      if (buttons.delete) buttons.delete.disabled = count === 0 || items.some(item => item.capabilities?.delete === false);
      if (buttons.rename) buttons.rename.disabled = count !== 1 || items[0]?.capabilities?.rename === false;
      for (const key of ["rename", "move", "delete", "mediaRename"]) {
        if (buttons[key] && buttons[key].disabled && protectionReason) {
          buttons[key].title = protectionReason;
        }
      }
      if (buttons.paste) buttons.paste.disabled = !this.clipboard;
      if (this.el.confirmButton) {
        this.el.confirmButton.disabled = this.selectionType === "directory" ? false : count === 0;
      }
    }

    openContextMenu(event, item) {
      document.querySelectorAll(".file-context-menu").forEach(node => node.remove());
      const key = itemKey(this.rootKey, item.relative_path);
      if (!this.selected.has(key)) {
        this.selected.clear();
        if (this.isSelectable(item)) this.selected.set(key, item);
        this.updateSelection();
      }
      const selected = this.selectedItems();
      if (!selected.length) return;
      const menu = element("div", "file-context-menu");
      menu.setAttribute("role", "menu");
      const actions = [
        ["preview", "预览/编辑", selected.length === 1 && !item.is_dir],
        ["rename", "重命名", selected.length === 1 && item.capabilities?.rename !== false],
        ["copy", "复制", selected.every(entry => entry.capabilities?.copy !== false)],
        ["cut", "剪切", selected.every(entry => entry.capabilities?.move !== false)],
        ["paste", "粘贴到当前目录", Boolean(this.clipboard)],
        ["move", "移动到…", selected.every(entry => entry.capabilities?.move !== false)],
        ["delete", "删除", selected.every(entry => entry.capabilities?.delete !== false)]
      ];
      if (this.onOpenWorkbench) actions.push(["workbench", "进入发种工作台", true]);
      if (this.onOpenNaming) actions.push(["mediaRename", "智能命名", true]);
      else if (selected.every(entry => entry.kind === "video" && !entry.is_dir)) {
        actions.push(["mediaRename", "剧集重命名", true]);
      } else if (this.selectedVideos().length) {
        actions.push(["mediaRename", "剧集重命名（仅视频）", true]);
      }
      if (this.onCollage && this.selectedCollageVideos().length) {
        actions.push(["collage", "生成缩略图", true]);
      }
      if (this.onTool) actions.push(
        ["report", "媒体报告", selected.length === 1 && (item.is_dir || item.kind === "video")],
        ["upload", "上传图片", selected.some(entry => entry.kind === "image")],
        ["torrent", "制作种子", selected.length === 1],
        ["publish", "发布资料", selected.length === 1]
      );
      for (const [action, label, enabled] of actions) {
        const node = button(label, "context-menu-item");
        node.setAttribute("role", "menuitem");
        node.disabled = !enabled;
        node.addEventListener("click", () => {
          menu.remove();
          this.runAction(action);
        });
        menu.append(node);
      }
      document.body.append(menu);
      const width = 190;
      const height = Math.min(window.innerHeight - 16, menu.getBoundingClientRect().height);
      menu.style.left = `${Math.max(8, Math.min(event.clientX, window.innerWidth - width - 8))}px`;
      menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - height - 8))}px`;
      window.setTimeout(() => {
        const close = closeEvent => {
          if (!menu.contains(closeEvent.target)) {
            menu.remove();
            document.removeEventListener("pointerdown", close, true);
          }
        };
        document.addEventListener("pointerdown", close, true);
      }, 0);
    }

    async runAction(action) {
      try {
        await this.performAction(action);
      } catch (error) {
        this.el.listError.textContent = error.message;
      }
    }

    async performAction(action) {
      if (action === "paste") {
        await this.paste();
        return;
      }
      const items = this.selectedItems();
      if (!items.length) return;
      if (["report", "upload", "torrent", "publish"].includes(action) && this.onTool) {
        this.onTool(action, items, this.rootKey); return;
      }
      if (action === "workbench" && this.onOpenWorkbench) await this.onOpenWorkbench(items, this.rootKey);
      else if (action === "preview") await this.openPreview(items[0]);
      else if (action === "rename") this.openRename(items[0]);
      else if (action === "copy" || action === "cut") {
        this.clipboard = {action, items: items.map(item => ({root_key: this.rootKey, relative_path: item.relative_path, name: item.name, is_dir: item.is_dir}))};
        this.el.actionStatus.textContent = `${action === "copy" ? "已复制" : "已剪切"} ${items.length} 项，请选择目标目录后粘贴`;
        this.updateOperationButtons();
      } else if (action === "move") this.chooseOperationTarget("移动到", items);
      else if (action === "delete") this.startOperation("delete", items, null);
      else if (action === "mediaRename" && this.onOpenNaming) await this.onOpenNaming(items, this.rootKey);
      else if (action === "mediaRename" && this.onMediaRename) this.onMediaRename(this.selectedVideos());
      else if (action === "collage" && this.onCollage) {
        this.onCollage(this.selectedCollageVideos(), this.rootKey);
      }
    }

    paste() {
      if (!this.clipboard) return;
      return this.startOperation(
        this.clipboard.action === "cut" ? "move" : "copy",
        this.clipboard.items,
        {root_key: this.rootKey, relative_path: this.path}
      );
    }

    chooseOperationTarget(title, items) {
      const sources = items.map(item => ({...item, root_key: item.root_key || this.rootKey}));
      FileBrowser.openPicker({
        mode: "move-copy-target",
        title,
        scope: "browse",
        selectionType: "directory",
        confirmLabel: "选择此目录"
      }).then(target => {
        if (target) this.startOperation("move", sources, target);
      }).catch(error => {
        this.el.listError.textContent = error.message;
      });
    }

    async startOperation(operation, items, target) {
      this.el.actionStatus.textContent = "正在检查目标…";
      try {
        let plan = await api("/api/files/operations/preview", {
          method: "POST",
          body: JSON.stringify({
            operation,
            sources: items.map(item => ({
              root_key: item.root_key || this.rootKey,
              relative_path: item.relative_path
            })),
            target: target || null,
            conflict_policy: "abort"
          })
        });
        if (plan.summary.blocked) {
          this.showOperationErrors(plan);
          return;
        }
        if (plan.summary.conflict) {
          this.showConflictChoice(plan, items, target, operation);
          return;
        }
        this.confirmAndApply(plan, false);
      } catch (error) {
        this.el.actionStatus.textContent = "";
        this.el.listError.textContent = error.message;
      }
    }

    showOperationErrors(plan) {
      const panel = element("div", "operation-dialog");
      panel.append(element("h2", null, "无法执行文件操作"));
      for (const item of plan.items.filter(entry => entry.status === "blocked")) {
        const line = element("p", "form-error", `${item.source.relative_path}：${(item.reasons || []).join("；")}`);
        panel.append(line);
      }
      const close = button("关闭", "button primary");
      close.addEventListener("click", closeModal);
      panel.append(close);
      showModal(panel);
    }

    showConflictChoice(plan, items, target, operation) {
      const panel = element("div", "operation-dialog");
      panel.append(element("h2", null, "目标名称冲突"));
      panel.append(element("p", "muted", `发现 ${plan.summary.conflict} 个已存在的目标。请选择取消、跳过冲突项，或明确覆盖。`));
      for (const item of plan.items.filter(entry => entry.status === "conflict")) {
        panel.append(element("p", "conflict-line", `${item.target.relative_path} 已存在`));
      }
      const footer = element("div", "dialog-actions");
      const cancel = button("取消", "button ghost");
      const skip = button("跳过冲突项", "button secondary");
      const overwrite = button("覆盖冲突项", "button danger");
      cancel.addEventListener("click", closeModal);
      skip.addEventListener("click", () => this.recheckConflicts("skip", items, target, operation));
      overwrite.addEventListener("click", () => this.recheckConflicts("overwrite", items, target, operation));
      footer.append(cancel, skip, overwrite);
      panel.append(footer);
      showModal(panel);
    }

    async recheckConflicts(policy, items, target, operation) {
      try {
        const plan = await api("/api/files/operations/preview", {
          method: "POST",
          body: JSON.stringify({
            operation,
            sources: items.map(item => ({root_key: item.root_key || this.rootKey, relative_path: item.relative_path})),
            target: target || null,
            conflict_policy: policy
          })
        });
        this.confirmAndApply(plan, policy === "overwrite");
      } catch (error) {
        closeModal();
        this.el.listError.textContent = error.message;
      }
    }

    confirmAndApply(plan, overwrite) {
      const panel = element("div", "operation-dialog");
      const action = plan.operation === "copy" ? "复制" : plan.operation === "move" ? "移动" : "删除";
      panel.append(element("h2", null, `确认${action}`));
      const summary = element("p", "muted", `共 ${plan.summary.ready} 项可执行，${plan.summary.skipped} 项将跳过，${plan.summary.conflict} 项冲突。`);
      panel.append(summary);
      if (plan.operation !== "delete") {
        const targetItem = plan.items.find(item => item.target);
        if (targetItem) {
          const root = this.root(targetItem.target.root_key);
          const targetDir = parentPath(targetItem.target.relative_path);
          panel.append(element("p", "muted", `目标目录：${root?.label || targetItem.target.root_key}${targetDir ? ` / ${targetDir}` : ""}`));
        }
      }
      const objectList = element("div", "operation-results");
      for (const item of plan.items) {
        const directoryDetail = item.object_type === "directory"
          ? `（包含 ${item.descendant_count ?? 0} 项，${formatBytes(item.descendant_bytes ?? 0)}）`
          : "";
        objectList.append(element("p", "muted", `${item.source.relative_path}${directoryDetail}`));
      }
      panel.append(objectList);
      if (overwrite) {
        panel.append(element("p", "form-error", "覆盖会替换目标名称对应的现有文件或目录，操作不可撤销。"));
      }
      if (plan.operation === "delete" && plan.items.some(item => item.object_type === "directory")) {
        panel.append(element("p", "rename-warning", "删除目录会同时删除其中列出的全部下级内容，且不可撤销。"));
      }
      const footer = element("div", "dialog-actions");
      const cancel = button("取消", "button ghost");
      const apply = button(overwrite ? "确认覆盖并执行" : `确认${action}`, overwrite ? "button danger" : "button primary");
      cancel.addEventListener("click", closeModal);
      apply.addEventListener("click", async () => {
        busy(apply, true);
        try {
          const result = await api("/api/files/operations/apply", {
            method: "POST",
            body: JSON.stringify({token: plan.token, confirm_overwrite: overwrite})
          });
          this.showTaskProgress(result.task_id, {operation: plan.operation, clipboard: this.clipboard});
        } catch (error) {
          panel.append(element("p", "form-error", error.message));
          busy(apply, false);
        }
      });
      footer.append(cancel, apply);
      panel.append(footer);
      showModal(panel);
    }

    async showTaskProgress(taskId, operationState = {}) {
      const panel = element("div", "operation-dialog");
      panel.append(element("h2", null, "文件操作进行中"));
      const status = element("p", "muted", "正在排队…");
      const progress = document.createElement("progress");
      progress.max = 100;
      progress.value = 0;
      progress.className = "operation-progress";
      const details = element("div", "operation-results");
      const footer = element("div", "dialog-actions");
      const cancel = button("取消任务", "button danger");
      const close = button("关闭", "button ghost");
      close.textContent = "收起";
      close.addEventListener("click", () => panel.remove());
      cancel.addEventListener("click", async () => {
        try {
          await api(`/api/tasks/${taskId}/cancel`, {method: "POST"});
        } catch (error) {
          status.textContent = error.message;
        }
      });
      footer.append(cancel, close);
      panel.append(status, progress, details, footer);
      closeModal();
      panel.classList.add("file-inline-task");
      this.host.prepend(panel);
      let timer = null, finished = false, polling = false;
      const remember = remove => {
        try {
          const ids = JSON.parse(localStorage.getItem("biliRemote.fileTasks") || "[]");
          localStorage.setItem("biliRemote.fileTasks", JSON.stringify(remove ? ids.filter(id => id !== taskId) : [...new Set([...ids, taskId])]));
        } catch {}
      };
      remember(false);
      const poll = async () => {
        if (polling || finished || this.destroyed) return;
        polling = true;
        try {
          const task = await api(`/api/tasks/${taskId}`);
          if (this.destroyed) return;
          const result = task.result || {};
          const total = Number(result.total) || 0;
          const processed = Number(result.processed) || 0;
          progress.value = total ? Math.round(processed / total * 100) : task.status === "completed" ? 100 : 0;
          const succeededCount = Number(result.succeeded) || 0;
          const failedCount = Number(result.failed) || 0;
          const skippedCount = Number(result.skipped) || 0;
          status.textContent = task.status === "queued"
            ? "排队中…"
            : task.status === "running"
              ? `处理中 ${processed}/${total}`
              : task.status === "failed"
                ? `操作结束：成功 ${succeededCount}，失败 ${failedCount}，跳过 ${skippedCount}${succeededCount > 0 ? "（部分完成）" : ""}`
                : task.status === "cancelled"
                  ? `任务已取消：已成功 ${succeededCount} 项，已跳过 ${skippedCount} 项`
                  : `操作完成：成功 ${succeededCount}，跳过 ${skippedCount}`;
          if (["completed", "failed", "cancelled"].includes(task.status)) {
            finished = true;
            window.clearInterval(timer);
            this.taskTimers.delete(timer);
            remember(true);
            document.dispatchEvent(new CustomEvent("app:notice", {detail: {text: status.textContent, error: task.status !== "completed"}}));
            cancel.disabled = true;
            close.disabled = false;
            details.replaceChildren();
            for (const item of result.items || []) {
              const statusLabel = {completed: "成功", failed: "失败", skipped: "跳过", cancelled: "已取消"}[item.status] || item.status;
              const targetLabel = item.target?.relative_path ? ` → ${item.target.relative_path}` : "";
              const line = element("p", item.status === "failed" ? "form-error" : "muted", `${item.source?.relative_path || ""}${targetLabel}：${statusLabel}${item.message ? `（${item.message}）` : ""}`);
              details.append(line);
            }
            if (operationState.operation === "move" && operationState.clipboard === this.clipboard && this.clipboard?.action === "cut") {
              const completed = new Set((result.items || []).filter(item => item.status === "completed")
                .map(item => itemKey(item.source.root_key, item.source.relative_path)));
              this.clipboard.items = this.clipboard.items.filter(item => !completed.has(itemKey(item.root_key, item.relative_path)));
              if (!this.clipboard.items.length) this.clipboard = null;
            }
            this.updateOperationButtons();
            await this.refresh();
          }
        } catch (error) {
          status.textContent = error.message;
        } finally { polling = false; }
      };
      await poll();
      if (!finished && !this.destroyed) {
        timer = window.setInterval(poll, 1000);
        this.taskTimers.add(timer);
      }
    }

    openRename(item) {
      const rootKey = this.rootKey;
      const panel = element("div", "operation-dialog");
      panel.append(element("h2", null, "重命名"));
      panel.append(element("p", "muted", `当前：${item.relative_path}`));
      const input = document.createElement("input");
      const dot = item.is_dir ? -1 : item.name.lastIndexOf(".");
      const hasExtension = dot > 0;
      const stem = hasExtension ? item.name.slice(0, dot) : item.name;
      const extension = hasExtension ? item.name.slice(dot) : "";
      input.value = stem;
      input.setAttribute("aria-label", item.is_dir ? "新名称" : "新文件名（不含扩展名）");
      if (extension) panel.append(element("p", "muted", `扩展名保持不变：${extension}`));
      const error = element("p", "form-error");
      error.setAttribute("role", "alert");
      const footer = element("div", "dialog-actions");
      const cancel = button("取消", "button ghost");
      const save = button("保存", "button primary");
      cancel.addEventListener("click", closeModal);
      save.addEventListener("click", async () => {
        error.textContent = "";
        busy(save, true);
        try {
          await api("/api/files/rename", {
            method: "POST",
            body: JSON.stringify({
              root_key: rootKey,
              relative_path: item.relative_path,
              new_name: `${input.value.trim()}${extension}`,
              fingerprint: item.fingerprint || null
            })
          });
          closeModal();
          await this.refresh();
          this.el.actionStatus.textContent = `已重命名：${item.name} → ${input.value.trim()}${extension}`;
        } catch (err) {
          error.textContent = err.message;
          busy(save, false);
        }
      });
      footer.append(cancel, save);
      panel.append(input, error, footer);
      showModal(panel);
      input.select();
    }

    async openPreview(item) {
      const rootKey = this.rootKey;
      const kind = previewKind(item);
      if (kind === "image") {
        const panel = element("div", "preview-dialog image-preview");
        panel.append(element("h2", null, item.name));
        const frame = element("div", "image-preview-frame");
        const image = document.createElement("img");
        image.alt = item.name;
        image.src = `/api/files/image?root_key=${encodeURIComponent(rootKey)}&path=${encodeURIComponent(item.relative_path)}&v=${item.mtime_ns}`;
        image.addEventListener("error", () => frame.append(element("p", "form-error", "图片加载失败")));
        frame.append(image);
        panel.append(frame, element("p", "muted", item.relative_path));
        showModal(panel, {stack: true});
        return;
      }
      if (kind === "text") {
        try {
          const doc = await api(`/api/files/text?root_key=${encodeURIComponent(rootKey)}&path=${encodeURIComponent(item.relative_path)}`);
          this.openTextEditor(item, doc, rootKey);
        } catch (error) {
          const panel = element("div", "operation-dialog");
          panel.append(element("h2", null, item.name), element("p", "form-error", error.message));
          showModal(panel);
        }
        return;
      }
      if (kind === "video" && this.onMediaInfo) {
        try { await this.onMediaInfo(item, rootKey); }
        catch (error) { this.el.listError.textContent = error.message; }
        return;
      }
      const panel = element("div", "operation-dialog");
      panel.append(element("h2", null, item.name));
      panel.append(element("p", "muted", `${item.relative_path} · ${formatBytes(item.size_bytes)} · ${formatDate(item.mtime_ns)}`));
      panel.append(element("p", null, "此文件类型暂不支持内嵌预览。"));
      showModal(panel);
    }

    openTextEditor(item, doc, rootKey = this.rootKey) {
      const panel = element("div", "text-editor-dialog");
      const head = element("div", "text-editor-head");
      head.append(element("h2", null, doc.name));
      head.append(element("span", "muted", `${doc.encoding_label} · ${doc.newline} · ${formatBytes(doc.size_bytes)}`));
      const textarea = document.createElement("textarea");
      textarea.value = doc.content;
      textarea.spellcheck = false;
      textarea.readOnly = doc.editable === false;
      textarea.setAttribute("aria-label", `${doc.name} 文本内容`);
      const validation = element("p", "rename-warning");
      const error = element("p", "form-error");
      error.setAttribute("role", "alert");
      let fingerprint = doc.fingerprint;
      const validate = () => {
        validation.textContent = "";
        if (item.extension === "json" && textarea.value.trim()) {
          try {
            JSON.parse(textarea.value);
          } catch (err) {
            validation.textContent = `JSON 语法提示：${err.message}（保存不会自动改写内容）`;
          }
        }
      };
      textarea.addEventListener("input", validate);
      validate();
      const footer = element("div", "dialog-actions");
      const reload = button("重新加载", "button secondary");
      const cancel = button("取消", "button ghost");
      const save = button("保存", "button primary");
      save.disabled = doc.editable === false;
      if (doc.editable === false) error.textContent = "文件只读，无法保存";
      reload.addEventListener("click", async () => {
        try {
          const fresh = await api(`/api/files/text?root_key=${encodeURIComponent(rootKey)}&path=${encodeURIComponent(item.relative_path)}`);
          textarea.value = fresh.content;
          fingerprint = fresh.fingerprint;
          textarea.readOnly = fresh.editable === false;
          save.disabled = fresh.editable === false;
          error.textContent = fresh.editable === false ? "文件只读，无法保存" : "";
          validate();
          this.el.actionStatus.textContent = "已重新加载文件";
        } catch (err) {
          error.textContent = err.message;
        }
      });
      cancel.addEventListener("click", closeModal);
      save.addEventListener("click", async () => {
        error.textContent = "";
        busy(save, true);
        try {
          const result = await api("/api/files/text", {
            method: "PUT",
            body: JSON.stringify({root_key: rootKey, relative_path: item.relative_path, content: textarea.value, fingerprint})
          });
          fingerprint = result.fingerprint;
          closeModal();
          await this.refresh();
          this.el.actionStatus.textContent = `已保存 ${item.name}`;
        } catch (err) {
          error.textContent = `${err.message}；编辑内容仍保留，可重新加载或重试。`;
          busy(save, false);
        }
      });
      footer.append(reload, cancel, save);
      panel.append(head, textarea, validation, error, footer);
      showModal(panel);
    }

    confirm() {
      if (!this.onConfirm) return;
      if (this.selectionType === "directory") {
        this.onConfirm({root_key: this.rootKey, relative_path: this.path});
      } else {
        this.onConfirm(this.selectedItems().map(item => ({root_key: this.rootKey, ...item})));
      }
    }

    treeKey(rootKey, path) {
      return `${rootKey}\n${path}`;
    }

    async loadTreeDirectory(rootKey, path, page = 1) {
      const key = this.treeKey(rootKey, path);
      const previous = this.treeCache.get(key) || {items: [], has_more: false};
      const query = new URLSearchParams({
        root_key: rootKey,
        path,
        page: String(page),
        page_size: "100",
        scope: this.scope,
        sort: "natural",
        direction: "asc"
      });
      const data = await api(`/api/files/browse?${query.toString()}`);
      const merged = page === 1 ? [] : [...previous.items];
      const known = new Set(merged.map(item => item.relative_path));
      for (const item of data.items) if (!known.has(item.relative_path)) merged.push(item);
      const cache = {items: merged, page: data.page, has_more: data.has_more};
      this.treeCache.set(key, cache);
      return cache;
    }

    async ensureTreeChain(rootKey, path) {
      this.expanded.add(this.treeKey(rootKey, ""));
      if (!this.treeCache.has(this.treeKey(rootKey, ""))) {
        try {
          await this.loadTreeDirectory(rootKey, "");
        } catch (error) {
          this.el.listError.textContent = error.message;
          return;
        }
      }
      let current = "";
      for (const part of String(path || "").split("/").filter(Boolean)) {
        current = current ? `${current}/${part}` : part;
        this.expanded.add(this.treeKey(rootKey, current));
        if (!this.treeCache.has(this.treeKey(rootKey, current))) {
          try {
            await this.loadTreeDirectory(rootKey, current);
          } catch (error) {
            this.el.listError.textContent = error.message;
            return;
          }
        }
      }
    }

    drawTree() {
      this.el.tree.replaceChildren();
      if (!this.roots.length) {
        this.el.tree.append(element("p", "empty", "暂无可用目录"));
        return;
      }
      for (const root of this.roots) this.el.tree.append(this.treeRoot(root));
    }

    treeRoot(root) {
      const node = element("div", "tree-node");
      const row = element("div", "tree-row");
      row.style.setProperty("--tree-depth", "0");
      const key = this.treeKey(root.key, "");
      const expanded = this.expanded.has(key);
      const toggle = button(expanded ? "▾" : "▸", "tree-toggle");
      toggle.addEventListener("click", async event => {
        event.stopPropagation();
        if (expanded) this.expanded.delete(key);
        else this.expanded.add(key);
        this.drawTree();
      });
      const label = button(root.label, "tree-label tree-root-label");
      label.classList.toggle("active", root.key === this.rootKey && this.path === "");
      label.addEventListener("click", () => this.navigate(root.key, ""));
      row.append(toggle, icon("directory", true), label);
      node.append(row);
      if (expanded) {
        const children = element("div", "tree-children");
        const cache = this.treeCache.get(key);
        if (!cache) {
          this.loadTreeDirectory(root.key, "").then(() => this.drawTree()).catch(error => {
            children.append(element("p", "form-error", error.message));
          });
        } else {
          for (const item of cache.items) children.append(this.treeItem(root, item, 1));
          if (cache.has_more) {
            const more = button("加载更多", "tree-more");
            more.addEventListener("click", async () => {
              try {
                await this.loadTreeDirectory(root.key, "", cache.page + 1);
                this.drawTree();
              } catch (error) { this.el.listError.textContent = error.message; }
            });
            children.append(more);
          }
        }
        node.append(children);
      }
      return node;
    }

    treeItem(root, item, depth) {
      const node = element("div", "tree-node");
      const row = element("div", "tree-row");
      row.style.setProperty("--tree-depth", String(depth));
      const key = this.treeKey(root.key, item.relative_path);
      const expanded = this.expanded.has(key);
      const toggle = button(item.is_dir ? (expanded ? "▾" : "▸") : "·", "tree-toggle");
      toggle.disabled = !item.is_dir || !item.allowed;
      toggle.addEventListener("click", async event => {
        event.stopPropagation();
        if (!item.is_dir) return;
        if (expanded) this.expanded.delete(key);
        else this.expanded.add(key);
        try {
          if (!this.treeCache.has(key)) await this.loadTreeDirectory(root.key, item.relative_path);
          this.drawTree();
        } catch (error) { this.el.listError.textContent = error.message; }
      });
      const label = button(item.name, "tree-label");
      label.classList.toggle("active", root.key === this.rootKey && this.path === item.relative_path && item.is_dir);
      label.classList.toggle("selected", this.selected.has(itemKey(root.key, item.relative_path)));
      if (!item.allowed) {
        label.disabled = true;
        label.title = "符号链接或越界路径不可访问";
      }
      label.addEventListener("click", () => {
        if (!item.allowed) return;
        if (item.is_dir) this.navigate(root.key, item.relative_path);
        else {
          const parent = parentPath(item.relative_path);
          this.navigate(root.key, parent, {focus: item.relative_path});
        }
      });
      row.append(toggle, icon(item.kind, item.is_dir), label);
      node.append(row);
      if (item.is_dir && expanded) {
        const children = element("div", "tree-children");
        const cache = this.treeCache.get(key);
        if (!cache) {
          this.loadTreeDirectory(root.key, item.relative_path)
            .then(() => this.drawTree())
            .catch(error => children.append(element("p", "form-error", error.message)));
        } else {
          for (const child of cache.items) children.append(this.treeItem(root, child, depth + 1));
          if (cache.has_more) {
            const more = button("加载更多", "tree-more");
            more.addEventListener("click", async () => {
              try {
                await this.loadTreeDirectory(root.key, item.relative_path, cache.page + 1);
                this.drawTree();
              } catch (error) { this.el.listError.textContent = error.message; }
            });
            children.append(more);
          }
        }
        node.append(children);
      }
      return node;
    }

    async refresh() {
      this.treeCache.clear();
      if (!this.rootKey) return;
      await this.loadDirectory({page: this.page, clearSelection: true});
      this.drawTree();
      await this.ensureTreeChain(this.rootKey, this.path);
      this.drawTree();
    }

    destroy() {
      this.destroyed = true;
      ++this.loadSeq;
      clearTimeout(this.searchTimer);
      for (const timer of this.taskTimers) window.clearInterval(timer);
      this.taskTimers.clear();
      window.clearInterval(this.taskTimer);
      this.host.replaceChildren();
    }

    static openPicker(options = {}) {
      return new Promise((resolve, reject) => {
        const panel = element("div", "file-picker-dialog");
        panel.append(element("h2", null, options.title || "选择目录"));
        const host = element("div", "file-picker-host");
        panel.append(host);
        const footer = element("div", "dialog-actions");
        const cancel = button("取消", "button ghost");
        footer.append(cancel);
        panel.append(footer);
        showModal(panel, {stack: true});
        let settled = false;
        let browser = null;
        const cleanup = () => document.removeEventListener("app:modal-close", onModalClose);
        const onModalClose = event => {
          if (event.detail?.panel && event.detail.panel !== panel) return;
          if (settled) return;
          settled = true;
          cleanup();
          browser?.destroy();
          resolve(null);
        };
        document.addEventListener("app:modal-close", onModalClose);
        browser = new FileBrowser(host, {
          ...options,
          onConfirm: result => {
            settled = true;
            cleanup();
            browser.destroy();
            closeModal();
            resolve(result);
          }
        });
        cancel.addEventListener("click", () => {
          if (!settled) {
            settled = true;
            browser.destroy();
          }
          cleanup();
          closeModal();
          resolve(null);
        });
        browser.init().catch(error => {
          cleanup();
          host.append(element("p", "form-error", error.message));
          reject(error);
        });
      });
    }
  }

  window.FileBrowser = FileBrowser;
})();
