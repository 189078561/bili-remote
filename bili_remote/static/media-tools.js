/* Independent media tools: durable jobs, shared catalog and explicit proposal review. */
(function () {
  "use strict";
  const el = (tag, text, cls) => {
    const n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  };
  const byId = (id) => document.getElementById(id);
  const values = () =>
    (typeof appSettings !== "undefined" && appSettings?.values) || {};
  const editors = new Map();
  const active = new Map();
  const storage = {
    get(key) {
      try {
        return JSON.parse(localStorage.getItem(key) || "null");
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch {}
    },
  };
  function action(label, fn, primary = false) {
    const b = el(
      "button",
      label,
      primary ? "button primary small" : "button secondary small",
    );
    b.type = "button";
    b.addEventListener("click", async () => {
      b.disabled = true;
      b.classList.add("is-busy");
      try {
        await fn();
      } catch (error) {
        const target = b
          .closest(".card,.collage-dialog,.metadata-picker")
          ?.querySelector(".tool-error");
        if (target) target.textContent = error.message;
        else window.alert(error.message);
      } finally {
        b.disabled = false;
        b.classList.remove("is-busy");
      }
    });
    return b;
  }
  async function reviewProposal(proposal, existing, apply) {
    proposal = Object.fromEntries(Object.entries(proposal).filter(([,val]) => val != null && val !== "" && (!Array.isArray(val) || val.length)));
    const ignored = new Set(["raw", "source", "related_links", "lookup_url", "catalog_sources"]);
    const provenance = Object.fromEntries(Object.entries(proposal).filter(([key]) => ["related_links", "lookup_url", "catalog_sources"].includes(key)));
    const meaningful = Object.entries(proposal).filter(([key,val]) => !ignored.has(key) && val != null && val !== "");
    const conflicts = meaningful.filter(([key,val]) => existing[key] && JSON.stringify(existing[key]) !== JSON.stringify(val));
    if (!conflicts.length) { apply({...existing,...proposal}); return; }
    const safe = {...existing,...provenance};
    for (const [key,val] of meaningful) if (!existing[key]) safe[key] = val;
    proposal = Object.fromEntries(conflicts);
    existing = safe;
    const panel = el("div", null, "metadata-picker");
    panel.append(
      el("h2", "预览资料变更"),
      el(
        "p",
        "以下已有字段与新资料不同。勾选后替换，未勾选的内容继续保留。空字段会直接补齐。",
        "muted",
      ),
    );
    const chosen = new Map();
    const labels = {
      title_zh: "中文名",
      title_en: "英文名",
      year: "年份",
      original_title: "原名",
      overview: "简介",
      poster_url: "海报",
      imdb_url: "IMDb",
      douban_url: "豆瓣",
      ptgen_text: "PT-Gen 简介",
      related_links: "关联链接（请核对条目）",
      genres: "类型",
      countries: "地区",
    };
    for (const [key, val] of Object.entries(proposal)) {
      if (val == null || val === "" || key === "raw") continue;
      const row = el("label", null, "proposal-row");
      const check = el("input");
      check.type = "checkbox";
      check.checked = !existing[key];
      chosen.set(key, check);
      const block = el("div");
      block.append(el("strong", labels[key] || key));
      if (existing[key])
        block.append(el("p", "当前：" + String(existing[key]), "muted"));
      block.append(
        el("pre", typeof val === "object" ? JSON.stringify(val) : String(val)),
      );
      row.append(check, block);
      panel.append(row);
    }
    panel.append(
      action(
        "采用所选字段",
        () => {
          const next = { ...existing };
          for (const [key, check] of chosen)
            if (check.checked) next[key] = proposal[key];
          apply(next);
          closeModal();
        },
        true,
      ),
    );
    panel.append(action("取消", () => closeModal()));
    showModal(panel, { stack: true });
  }
  function openCatalog(apply, current = {}, options = {}) {
    const panel = el("div", null, "metadata-picker");
    panel.append(el("h2", "搜索并选择作品"));
    const bar = el("div", null, "tool-search-row");
    const provider = el("select");
    provider.setAttribute("aria-label", "资料来源");
    provider.append(new Option("TMDB", "tmdb"));
    provider.value = "tmdb";
    const type = el("select");
    type.setAttribute("aria-label", "作品类型");
    type.append(new Option("剧集", "tv"), new Option("电影", "movie"));
    type.value = current.media_type || "tv";
    const query = el("input");
    query.placeholder = "中文名或英文名";
    query.setAttribute("aria-label", "搜索作品");
    query.value = current.title_zh || current.title_en || "";
    const year = el("input");
    year.type = "number";
    year.placeholder = "年份（可选）";
    year.setAttribute("aria-label", "搜索年份");
    let requestVersion = 0;
    let selectionVersion = 0;
    const results = el("div", null, "metadata-results");
    const error = el("p", "", "tool-error form-error");
    const hint = el("p", "", "muted");
    const valid = () => panel.isConnected && (!options.isCurrent || options.isCurrent());
    function changed() {
      requestVersion++;
      selectionVersion++;
      results.replaceChildren();
      error.textContent = "";
      type.hidden = year.hidden = provider.value !== "tmdb";
      hint.textContent = provider.value === "ptgen"
        ? "按片名搜索豆瓣，核对年份、季度后选择；选择后直接获取简介。也可切换 TMDB 补充英文名称。"
        : "TMDB 提供名称、海报与作品标识；发布简介可通过豆瓣补充。";
    }
    for (const input of [provider, type, query, year]) input.addEventListener("input", changed);
    changed();
    async function search() {
      const version = ++requestVersion;
      ++selectionVersion;
      const source = provider.value;
      error.textContent = "";
      results.textContent = "正在搜索…";
      try {
        const url = source === "ptgen"
          ? `/api/media/ptgen/search?${new URLSearchParams({query:query.value})}`
          : `/api/media/search?${new URLSearchParams({query:query.value, media_type:type.value, ...(year.value ? {year:year.value} : {})})}`;
        const result = await api(url);
        if (version !== requestVersion || !valid()) return;
        results.replaceChildren();
        if (!result.items.length)
          results.append(el("p", "没有找到匹配作品，请调整名称、切换来源或手动填写链接。"));
        for (const item of result.items) {
          const card = el("article", null, "metadata-candidate");
          if (item.poster_url) {
            const img = el("img");
            img.src = item.poster_url;
            img.alt = item.title_zh;
            img.loading = "lazy";
            card.append(img);
          }
          const copy = el("div");
          copy.append(
            el("strong", `${item.title_zh || item.original_title} (${item.year || "未知年份"})`),
            el("p", source === "ptgen" ? "豆瓣 · " + item.douban_url : `${item.media_type === "tv" ? "剧集" : "电影"} · TMDB #${item.tmdb_id}`, "muted"),
            el("p", item.overview),
          );
          copy.append(action("选择作品", async () => {
            const selectedVersion = ++selectionVersion;
            error.textContent = "正在获取所选作品资料…";
            try {
              const detail = source === "ptgen"
                ? await api("/api/media/ptgen", {method:"POST",body:JSON.stringify({url:item.douban_url})})
                : await api(`/api/media/details/${item.media_type}/${item.tmdb_id}`);
              if (selectedVersion !== selectionVersion || version !== requestVersion || !valid()) return;
              const proposal = {...detail.proposal};
              delete proposal.source;
              if (source === "ptgen") {
                proposal.title_zh ||= item.title_zh;
                proposal.year ||= item.year;
                proposal.catalog_sources = [...new Set([...(current.catalog_sources || []),"ptgen"])];
                // Correlated links are suggestions; only the selected entry is adopted.
                if (proposal.imdb_url && proposal.imdb_url !== item.douban_url) delete proposal.imdb_url;
              }
              closeModal();
              await reviewProposal(proposal, current, next => {
                if (!options.isCurrent || options.isCurrent()) apply(next, source);
              });
            } catch (failure) {
              if (selectedVersion === selectionVersion && version === requestVersion && valid())
                error.textContent = `获取资料失败：${failure.message}。已有资料已保留，可重试或选择其他条目。`;
            }
          }));
          card.append(copy);
          results.append(card);
        }
      } catch (failure) {
        if (version === requestVersion && valid()) {
          results.replaceChildren();
          error.textContent = failure.message + "。已有资料已保留，可切换来源或直接填写豆瓣链接。";
        }
      }
    }
    const searchButton = action("搜索", search, true);
    query.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        searchButton.click();
      }
    });
    bar.append(provider, type, query, year, searchButton);
    panel.append(bar, hint, error, results, action("关闭", () => closeModal()));
    showModal(panel, { stack: true });
  }
  const commonFields = [
    ["title_zh", "中文名"],
    ["title_en", "英文名 / 罗马字名"],
    ["year", "年份"],
    ["imdb_url", "IMDb 链接"],
    ["douban_url", "豆瓣链接"],
    ["poster_url", "海报链接"],
  ];
  function mountEditor(scope) {
    const host = byId(`${scope}-metadata`);
    if (!host) return;
    let extras = {};
    let revision = 0;
    const imported = new Map();
    const inputs = new Map();
    const externalFields=[];
    const bar = el("div", null, "button-row");
    const grid = el("div", null, "module-form-grid");
    const read = () =>
      Object.fromEntries([
        ...Object.entries(extras),
        ...[...inputs].map(([key, input]) => [key, input.value]),
      ]);
    const set = (data) => {
      revision++;
      err.textContent="";
      recovery.hidden = true;
      extras = { ...data };
      if(data.tmdb_id) extras.catalog_sources=[...new Set([...(data.catalog_sources || []),"tmdb"])];
      for (const [key, input] of inputs) {input.value = data[key] || ""; imported.set(key,input.value);}
      renderRelated();
      host.dispatchEvent(
        new CustomEvent("metadata:changed", { bubbles: true }),
      );
    };
    const err = el("p", "", "tool-error form-error");
    const related=el("div",null,"related-links");
    function renderRelated() {
      related.replaceChildren();
      for(const url of extras.related_links || []) {
        if(!/^https:\/\/(movie\.douban\.com\/subject\/\d+|www\.imdb\.com\/title\/tt\d+)\/$/.test(url) || url===extras.lookup_url) continue;
        const row=el("div",null,"button-row");
        const link=el("a",url);link.href=url;link.target="_blank";link.rel="noopener noreferrer";
        const key=url.includes("douban.com") ? "douban_url" : "imdb_url";
        const adopt=action(read()[key]===url ? "已采用" : "核对后采用",()=>set({...read(),[key]:url}));
        adopt.disabled=read()[key]===url;
        row.append(link,adopt);related.append(row);
      }
    }
    function edited() {
      revision++;
      if(err.textContent.startsWith("正在通过"))err.textContent="资料已修改，本次补充结果不会覆盖；可重试。";
    }
    const recovery = el("div", null, "button-row");
    recovery.hidden = true;
    function chooseCatalog(provider = "tmdb") {
      const version = revision;
      openCatalog((next, source) => {
        if (version !== revision) return;
        set(next);
        if (scope === "publish" && source === "tmdb") err.textContent = "TMDB 资料已载入，可直接编辑简介。";
        else if (source === "ptgen") err.textContent = "PT-Gen 简介已补充，可继续编辑。";
      }, read(), {provider, isCurrent:() => revision === version});
    }
    recovery.append(action("搜索 TMDB", () => chooseCatalog("tmdb")));
    async function supplement() {
      const fields = read();
      const link = fields.douban_url || fields.imdb_url;
      if (!link) {
        err.textContent = "可搜索 TMDB；如需 PT-Gen 补充，请在外部链接中填写豆瓣 / IMDb 链接。";
        recovery.hidden = false;
        return;
      }
      recovery.hidden = true;
      const version = revision;
      err.textContent = "正在通过 PT-Gen 补充简介…";
      try {
        const result = await api("/api/media/ptgen", {method:"POST",body:JSON.stringify({url:link})});
        if (version !== revision) return;
        const proposal = {...result.proposal};
        // PT-Gen text is supplementary content; its provenance is not a release source.
        delete proposal.source;
        for(const [key,val] of Object.entries(proposal)) if(val == null || val === "") delete proposal[key];
        for(const key of ["imdb_url","douban_url"]) if(proposal[key] && proposal[key] !== link) delete proposal[key];
        const current = read();
        const preserved = new Set([...inputs].filter(([key,input])=>input.value !== (imported.get(key) || "")).map(([key])=>key));
        const conflicting = Object.entries(proposal).some(([key,val]) => preserved.has(key) && current[key] && current[key] !== val);
        const next = {...current};
        for (const [key,val] of Object.entries(proposal)) if (!preserved.has(key)) next[key]=val;
        const applySupplement = data => {
          if (version !== revision) return;
          set({...data,catalog_sources:[...new Set([...(data.catalog_sources || []),"ptgen"])]});
          err.textContent="PT-Gen 简介已补充，可继续编辑。";
        };
        if (conflicting) await reviewProposal(proposal,current,applySupplement);
        else applySupplement(next);
      } catch(error) {
        if (version === revision) {
          err.textContent = `PT-Gen 补充失败：${error.message}。已有资料已保留，可核对外部链接后重试。`;
          recovery.hidden = false;
        }
      }
    }
    bar.append(
      action("搜索作品", () => chooseCatalog()),
      action("补充 / 重试简介", supplement),
      action("保存影视资料", async () => {
        const result = await api("/api/media/metadata", {
          method: "POST",
          body: JSON.stringify({ fields: read(), ...(scope === "publish" ? window.BiliModules?.publishSourcePayload() : {}) }),
        });
        extras.catalog_id = result.id;
        err.textContent = "影视资料已保存";
      }),
      action("读取已保存资料", async () => {
        const list = await api("/api/media/metadata");
        const panel = el("div", null, "metadata-picker");
        panel.append(el("h2", "已保存的影视资料"));
        for (const record of list.items)
          panel.append(
            action(
              `${record.fields.title_zh || record.fields.title_en || "未命名"} · ${record.fields.year || ""}`,
              () => {
                closeModal();
                return reviewProposal(
                  { ...record.fields, catalog_id: record.id },
                  read(),
                  set,
                );
              },
            ),
          );
        panel.append(action("关闭", () => closeModal()));
        showModal(panel, { stack: true });
      }),
    );
    for (const [key, label] of commonFields) {
      const wrap = el("label", label);
      const input = el("input");
      input.id = `${scope}-${key}`;
      input.addEventListener("input",edited);
      wrap.append(input);
      if(["imdb_url","douban_url","poster_url"].includes(key)) externalFields.push(wrap); else grid.append(wrap);
      inputs.set(key, input);
    }
    const wrap = el("label", "影视简介");
    if(scope === "publish") wrap.hidden=true;
    const overview = el("textarea");
    overview.rows = 4;
    overview.id = `${scope}-overview`;
    overview.addEventListener("input",edited);
    wrap.append(overview);
    inputs.set("overview", overview);
    const pt = el("label", "PT-Gen 原始简介（采用后可编辑）");
    const ptInput = el("textarea");
    ptInput.rows = 4;
    ptInput.id = `${scope}-ptgen_text`;
    ptInput.addEventListener("input",edited);
    pt.append(ptInput);
    inputs.set("ptgen_text", ptInput);
    const extra = el("details",null,"metadata-extra");
    const externalGrid=el("div",null,"module-form-grid");externalGrid.append(...externalFields);
    extra.append(el("summary","外部链接、海报与 PT-Gen 原文"),externalGrid,related,pt);
    const library=el("details",null,"file-more-menu");
    const libraryActions=el("div",null,"file-more-items");
    libraryActions.append(...[...bar.children].slice(2));
    library.append(el("summary","资料库","button ghost small"),libraryActions);bar.append(library);
    host.append(bar, err, recovery, grid, wrap, extra);
    editors.set(scope, { read, set });
  }
  function taskPanel(scope) {
    let host = byId(`${scope}-job`);
    if (!host) {
      host = el("section", null, "card tool-task");
      host.id = `${scope}-job`;
      byId(`${scope === "release" ? "release-info" : scope}-view`)?.append(
        host,
      );
    }
    return host;
  }
  async function watch(scope, id, inlineHost = null) {
    if (active.get(scope) === id) return;
    active.set(scope, id);
    storage.set(`media-job-${scope}`, id);
    const host = inlineHost || taskPanel(scope);
    while (active.get(scope) === id) {
      try {
        const task = await window.AppTaskCenter.readProcessing(id);
        if (!host) return;
        const labels = {
          queued: "排队中",
          running: "处理中",
          completed: "已完成",
          failed: "失败",
          cancelled: "已取消",
        };
        host.replaceChildren(
          el("strong", `任务 #${id} · ${labels[task.status] || task.status}`),
        );
        const detail = task.result || {};
        host.append(
          el(
            "p",
            task.error ||
              (detail.bytes
                ? `已计算 ${(detail.bytes / 1024 ** 2).toFixed(1)} MiB`
                : "进度和结果保存在服务器，可刷新页面恢复。"),
            task.error ? "form-error" : "muted",
          ),
        );
        const buttons = el("div", null, "button-row");
        if (["queued", "running"].includes(task.status))
          buttons.append(
            action("取消", () =>
              api(`/api/tasks/${id}/cancel`, { method: "POST" }),
            ),
          );
        if (["failed", "cancelled"].includes(task.status))
          buttons.append(
            action("重试失败项目", async () => {
              await api(`/api/tasks/${id}/retry`, { method: "POST" });
              active.delete(scope);
              watch(scope, id);
            }),
          );
        buttons.append(
          action("查看日志", async () => {
            const result = await api(`/api/tasks/${id}/logs`);
            const pre = el("pre", result.content || "暂无日志", "log-output");
            host.append(pre);
          }),
        );
        host.append(buttons);
        if (!["queued", "running"].includes(task.status)) {
          active.delete(scope);
          if (task.status === "completed")
            storage.set(`media-job-${scope}`, null);
          document.dispatchEvent(
            new CustomEvent("tools:completed", { detail: { scope, task } }),
          );
          return;
        }
      } catch (error) {
        if (host)
          host.textContent = `任务 #${id}：${error.message}，稍后自动重连。`;
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  }
  async function startJob(scope, path, payload, inlineHost = null) {
    if (active.has(scope)) throw new Error("当前模块已有任务在执行");
    const result = await api(path, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    watch(scope, result.task.id, inlineHost);
    return result;
  }
  async function optical(source, host) {
    if (!host) return;
    host.replaceChildren();
    const info = await api(
      `/api/media/optical?${new URLSearchParams({ root_key: source.root_key, relative_path: source.relative_path })}`,
    );
    if (info.kind === "video") return;
    const label = el(
      "label",
      info.kind === "bluray"
        ? "蓝光播放列表（默认最长）"
        : "DVD 标题组（默认体积最大）",
    );
    const select = el("select");
    select.className = "optical-selection";
    for (const choice of info.choices)
      select.append(
        new Option(
          `${choice.id} · ${choice.duration_seconds ? Math.round(choice.duration_seconds) + " 秒" : (choice.size_bytes / 1024 ** 3).toFixed(2) + " GiB"}`,
          choice.id,
        ),
      );
    select.value = info.selected;
    label.append(select);
    host.append(label);
  }
  async function useInPublish(_type, record) {
    const source = {root_key:record.source_root_key, relative_path:record.source_relative_path};
    if (document.querySelector("#modal .file-tool-dialog")) closeModal();
    await window.BiliModules.openPublishResource(source);
  }

  function boot() {
    mountEditor("release");
    for (const scope of ["uploads", "release", "torrents"]) {
      const id = storage.get(`media-job-${scope}`);
      if (id) watch(scope, id);
    }
    document.addEventListener("settings-updated", () => {
      for (const scope of ["release", "torrents"]) {
        const summary = byId(`${scope}-defaults`);
        if (summary)
          summary.textContent =
            scope === "torrents"
              ? `默认：${values().torrent_default_private ? "私有" : "公开"} · piece ${values().torrent_default_piece_length || "auto"} · ${(values().torrent_default_trackers || []).length} 个 Tracker`
              : `默认：${values().release_info_default_format || "markdown"} · 完整 MediaInfo / BDInfo`;
      }
    });
  }
  window.MediaCatalog = { open: openCatalog };
  window.ToolUI = {
    startJob,
    optical,
    metadata: (scope) => editors.get(scope)?.read() || {},
    setMetadata: (scope, v) => editors.get(scope)?.set(v),
    useInPublish,
    storage,
  };
  boot();
})();
