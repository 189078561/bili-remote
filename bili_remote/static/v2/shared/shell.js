/* Shared application shell. Projects existing queues; owns no task execution/storage. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const node = (tag, text, cls) => {
    const item = document.createElement(tag);
    if (text != null) item.textContent = text;
    if (cls) item.className = cls;
    return item;
  };
  const button = (label, run) => {
    const item = node("button", label, "button ghost small");
    item.type = "button";
    item.onclick = async () => {
      item.disabled = true;
      try { await run(); } catch (error) { notice(error.message, true); }
      finally { item.disabled = false; }
    };
    return item;
  };
  const notices = [];
  function notice(text, error = false) {
    if (!text) return;
    const toast = node("div", null, `app-toast${error ? " error" : ""}`);
    toast.append(node("span", text), button("关闭", () => toast.remove()));
    $("app-notifications").append(toast);
    while ($("app-notifications").children.length > 3) $("app-notifications").firstChild.remove();
    setTimeout(() => toast.remove(), 6500);
    notices.unshift({text, error, time: new Date().toLocaleTimeString("zh-CN")});
    notices.splice(50);
    $("task-center-notices").replaceChildren(...notices.map(n =>
      node("p", `${n.time} · ${n.text}`, n.error ? "form-error" : "muted")));
  }
  window.AppNotice = {show: notice};
  document.addEventListener("app:notice", event => notice(event.detail?.text, event.detail?.error));

  const titles = {home: "首页", files: "文件管理", publish: "发种工作台", download: "B站下载", settings: "设置"};
  const nav = $("app-navigation"), toggle = $("nav-toggle"), backdrop = $("nav-backdrop");
  const mobile = matchMedia("(max-width: 900px)");
  function syncNavigation() {
    const opened = document.body.classList.contains("nav-open") && mobile.matches;
    backdrop.hidden = !opened;
    toggle.setAttribute("aria-expanded", String(opened));
    nav.inert = mobile.matches && !opened;
  }
  function closeNavigation(restore = false) {
    document.body.classList.remove("nav-open");
    syncNavigation();
    if (restore) toggle.focus();
  }
  backdrop.onclick = () => closeNavigation(true);
  // ui.js retains the existing toggle handler; observe its state rather than double-toggle.
  new MutationObserver(syncNavigation).observe(document.body, {attributes: true, attributeFilter: ["class"]});
  mobile.addEventListener("change", () => closeNavigation());
  syncNavigation();
  document.addEventListener("app:view", event => {
    const view = event.detail.view;
    $("app-page-label").textContent = titles[view] || "文件管理";
    document.querySelectorAll(".app-tab").forEach(tab => {
      const selected = (tab.dataset.view || (tab.id === "settings-entry" ? "settings" : "")) === view;
      tab.setAttribute("aria-current", selected ? "page" : "false");
    });
    closeNavigation();
    // Move keyboard focus out of the now-hidden drawer and into the selected page.
    if (nav.contains(document.activeElement)) $("app-content").focus({preventScroll: true});
  });
  document.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      if (!$("modal").classList.contains("hidden")) return;
      if (!$("task-center").hidden) { setDrawer(false); event.preventDefault(); }
      else if (document.body.classList.contains("nav-open")) closeNavigation(true);
    }
    if(event.key === 'Tab' && !$("task-center").hidden && $("modal").classList.contains('hidden')) {
      const nodes=[...$("task-center").querySelectorAll('button,select,a[href],summary')].filter(el=>!el.disabled&&el.getClientRects().length);
      if(event.shiftKey && document.activeElement===nodes[0]){event.preventDefault();nodes.at(-1)?.focus();}
      else if(!event.shiftKey && document.activeElement===nodes.at(-1)){event.preventDefault();nodes[0]?.focus();}
    }
    if (event.key === "Tab" && mobile.matches && document.body.classList.contains("nav-open")) {
      const controls = [toggle, ...nav.querySelectorAll("button")];
      const first = controls[0], last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
      else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
    }
  });

  const queues = {download: [], processing: []};
  const errors = new Map(), loaded = new Set(), observed = new Map(), cache = new Map(), pending = new Map();
  const terminal = status => ["completed", "failed", "cancelled", "auth_required"].includes(status);
  const statuses = {queued: "排队中", running: "进行中", completed: "已完成", failed: "失败", cancelled: "已取消", auth_required: "需要登录"};
  const types = {screenshot: "截图", screenshots: "截图", collage: "缩略图", torrent: "种子制作", torrent_create: "种子制作", release_info: "媒体报告", upload: "图床上传", file_operation: "文件操作"};
  let processingRequest, drawerReturn, drawerInert=[];
  function observe(kind, task, baseline = false) {
    const key = `${kind}:${task.id}`;
    const previous = observed.get(key);
    observed.set(key, task.status);
    if (!baseline && previous && previous !== task.status && terminal(task.status)) {
      notice(`${kind === "download" ? "下载" : types[task.task_type] || "媒体任务"} #${task.id} ${statuses[task.status] || task.status}`, ["failed", "auth_required"].includes(task.status));
    }
  }
  function receive(kind, tasks) {
    if (!Array.isArray(tasks)) return;
    const baseline = !loaded.has(kind);
    tasks.forEach(task => observe(kind, task, baseline));
    queues[kind] = tasks;
    loaded.add(kind);
    errors.delete(kind);
    if (kind === "processing") tasks.filter(task => !task.kind).forEach(task => cache.set(task.id, {task, at: Date.now()}));
    render();
    document.dispatchEvent(new CustomEvent("app:tasks", {detail: {kind, items: tasks}}));
  }
  function failed(kind, error) {
    errors.set(kind, error.message);
    render();
  }
  async function refreshProcessing() {
    if (processingRequest) return processingRequest;
    processingRequest = api(window.BILI_V2 ? "/api/v2/tasks?limit=500" : "/api/tasks?limit=500")
      .then(result => {
        if (window.BILI_V2) {
          receive("processing", result.items.filter(task => task.kind === "processing"));
          receive("download", result.items.filter(task => task.kind === "download"));
        } else receive("processing", result);
      })
      .catch(error => failed("processing", error))
      .finally(() => { processingRequest = null; });
    return processingRequest;
  }
  async function readProcessing(id) {
    const recent = cache.get(id);
    if (recent && !terminal(recent.task.status) && Date.now() - recent.at < 900) return recent.task;
    if (pending.has(id)) return pending.get(id);
    const request = api(`/api/tasks/${id}`).then(task => {
      cache.set(id, {task, at: Date.now()});
      observe("processing", task);
      const index = queues.processing.findIndex(item => item.id === id);
      if (index < 0) queues.processing.unshift(task); else queues.processing[index] = task;
      render();
      return task;
    }).finally(() => pending.delete(id));
    pending.set(id, request);
    return request;
  }
  function setDrawer(open) {
    if (open) { drawerReturn = document.activeElement; closeNavigation(); }
    if(open && $("task-center").hidden) {
      drawerInert=[document.querySelector('.app-shell'),document.querySelector('.topbar')].map(el=>[el,el.inert]);
      drawerInert.forEach(([el])=>el.inert=true);
    } else if(!open) {drawerInert.forEach(([el,value])=>el.inert=value);drawerInert=[];syncNavigation();}
    $("task-center").hidden = !open;
    $("task-center-backdrop").hidden = !open;
    $("task-center-toggle").setAttribute("aria-expanded", String(open));
    if (open) { render(); $("task-center-close").focus(); refreshProcessing(); }
    else (drawerReturn?.isConnected ? drawerReturn : $("task-center-toggle")).focus();
  }
  async function refresh() { await Promise.allSettled([loadJobs(), refreshProcessing()]); }
  function taskCard(kind, task) {
    const card = node("article", null, "task-card");
    card.dataset.taskKey = `${kind}:${task.id}`;
    const head = node("div", null, "task-card-head");
    head.append(node("strong", kind === "download" ? task.title || `下载 #${task.id}` : `${types[task.task_type] || task.task_type || "媒体工具"} #${task.id}`));
    head.append(node("span", statuses[task.status] || task.status, `status-pill ${task.status}`));
    card.append(head, node("p", `${kind === "download" ? "B站下载" : "媒体工具"} · #${task.id}`, "muted"));
    if (!terminal(task.status)) {
      const progress = node("progress");
      progress.max = 100;
      progress.setAttribute("aria-label", "任务进度");
      let percent = kind === "download" ? task.progress : null;
      if (kind === "processing" && task.result?.total > 0) percent = 100 * ((task.result.succeeded || 0) + (task.result.failed || 0)) / task.result.total;
      if (percent != null && Number.isFinite(Number(percent))) progress.value = Math.max(0, Math.min(99, Number(percent)));
      card.append(progress, node("p", "后台处理中，可继续其他操作。", "muted"));
    }
    if (task.error) card.append(node("p", task.error, "form-error"));
    const actions = node("div", null, "button-row");
    const base = kind === "download" ? `/api/jobs/${task.id}` : `/api/tasks/${task.id}`;
    const mutate = async action => {
      await api(`${window.BILI_V2 ? `/api/v2/tasks/${kind}/${task.id}` : base}/${action}`, {method: "POST"});
      cache.delete(task.id);
      await refresh();
    };
    if (["running", "queued"].includes(task.status) && (!window.BILI_V2 || task.cancel_allowed !== false)) actions.append(button("取消", () => mutate("cancel")));
    if (["failed", "cancelled", "auth_required"].includes(task.status) && (window.BILI_V2 ? task.retry_allowed : (kind === "download" || !["file_operation","v2_rename_apply","v2_rename_undo"].includes(task.task_type)))) actions.append(button("重试", () => mutate("retry")));
    if (kind === "processing" && task.task_type === "file_operation" && ["failed", "cancelled"].includes(task.status)) actions.append(button("重新检查文件", () => { setDrawer(false); openBusinessView("files"); }));
    actions.append(button("日志", async () => {
      const result = await api(`${base}/${kind === "download" ? "log" : "logs"}`);
      const output = node("div");
      output.append(node("h2", `任务 #${task.id} 日志`), node("pre", result.content || "暂无日志", "log-output"));
      showModal(output, {stack: true});
    }));
    if(task.status === 'completed') {
      if(kind === 'download' && window.AppResources) {
        actions.append(button('进入工作台',async()=>{await AppResources.openDownload(task);setDrawer(false);}));
        actions.append(button('打开文件管理',async()=>{await AppResources.openDownload(task,'files');setDrawer(false);}));
      }
      for(const artifact of task.artifact_refs || []) {
        if(!artifact.url)continue;
        try {
          const url=new URL(artifact.url,location.href);
          if(!['http:','https:'].includes(url.protocol)||url.username||url.password)continue;
          const link=node('a',({report:'下载报告',torrent:'下载种子',screenshot:'打开截图',collage:'打开拼图',upload:'打开图床图片'})[artifact.kind]||'打开产物','task-result-link');
          link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';actions.append(link);
        }catch{}
      }
    }
    card.append(actions);
    return card;
  }
  function render() {
    const all = Object.entries(queues).flatMap(([kind, tasks]) => tasks.map(task => ({kind, task})));
    const active = all.filter(({task}) => ["queued", "running"].includes(task.status)).length;
    $("task-center-count").textContent = errors.size ? "!" : loaded.size < 2 ? "…" : String(active);
    $("task-center-error").textContent = [...errors].map(([kind, message]) => `${kind === "download" ? "下载" : "媒体任务"}读取失败：${message}；保留上次结果。`).join(" ");
    if ($("task-center").hidden) return;
    // Avoid replacing the button under a keyboard/pointer user during background polling.
    if ($("task-center-list").contains(document.activeElement)) return;
    const filter = $("task-center-filter").value;
    const visible = all.filter(({task}) => filter === "all" || (filter === "active" ? ["queued", "running"].includes(task.status) : filter === "failed" ? ["failed", "auth_required"].includes(task.status) : task.status === filter));
    visible.sort((a, b) => String(b.task.created_at || "").localeCompare(String(a.task.created_at || "")) || a.kind.localeCompare(b.kind) || b.task.id - a.task.id);
    $("task-center-list").replaceChildren(...(visible.length ? visible.map(({kind, task}) => taskCard(kind, task)) : [node("p", errors.size ? "暂时无法确认任务状态，请重试。" : loaded.size < 2 ? "正在读取任务…" : "没有符合条件的任务", "empty")]));
  }
  window.AppTaskCenter = {receive, failed, readProcessing, refresh, open: () => setDrawer(true)};
  $("task-center-toggle").onclick = () => setDrawer($("task-center").hidden);
  $("task-center-backdrop").onclick=()=>setDrawer(false);
  $("task-center-close").onclick = () => setDrawer(false);
  $("task-center-filter").onchange = render;
  $("task-center-refresh").onclick = async event => {
    const control = event.currentTarget;
    control.disabled = true;
    try { await refresh(); } finally { control.disabled = false; }
  };
  document.addEventListener("click", event => { if (event.target.closest("[data-open-task-center]")) setDrawer(true); });
  refreshProcessing();
  setInterval(refreshProcessing, 3000);
})();
