/* Session-memory-only UI. All recognition, mapping and names come from V2 APIs. */
export function mount(container, { request, selectResources, onContextChange = () => {}, onTask = () => {}, context = null } = {}) {
  if (typeof request !== "function" || typeof selectResources !== "function") throw new Error("Naming requires authenticated request and shared resource selector");
  let current = context ? structuredClone(context) : null;
  let resources = current?.resources || [];
  let preview = null;
  let generation = 0;
  let lastBatch = null;
  let manualSidecars = [];
  let disposed = false;
  const root = document.createElement("section");
  root.className = "v2-naming";
  root.setAttribute("aria-label", "智能识别与命名");
  container.replaceChildren(root);
  const el = (tag, text, parent = root) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    parent.append(node);
    return node;
  };
  el("h2", "智能资源识别与命名");
  const status = el("p", "选择视频或整季目录，识别后先预览，再确认改名。");
  status.setAttribute("role", "status");
  const toolbar = el("div"); toolbar.className = "v2-naming__toolbar";
  const button = (text, handler, parent = toolbar) => {
    const node = el("button", text, parent); node.type = "button";
    node.addEventListener("click", () => run(handler));
    return node;
  };
  const input = (label, value = "", type = "text", parent = root) => {
    const wrapper = el("label", label, parent);
    const node = el("input", undefined, wrapper); node.type = type; node.value = value;
    node.addEventListener("input", invalidate);
    return node;
  };
  const select = (label, choices, parent = root) => {
    const wrapper = el("label", label, parent);
    const node = el("select", undefined, wrapper);
    for (const [value, text] of choices) { const option = el("option", text, node); option.value = value; }
    node.addEventListener("change", invalidate);
    return node;
  };
  function invalidate() {
    generation += 1; preview = null; apply.disabled = true;
    output.replaceChildren();
  }
  async function run(action) {
    try { await action(); } catch (error) {
      status.textContent = error?.detail?.message || error?.message || "操作未完成，请检查输入。";
    }
  }
  async function api(path, method = "GET", body) {
    const response = await request(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } }) });
    if (response && typeof response.json === "function") {
      const data = await response.json();
      if (!response.ok) throw data;
      return data;
    }
    return response;
  }
  const type = select("资源类型", [["movie", "电影"], ["series", "电视剧"], ["anime", "动画"]], toolbar);
  button("选择视频 / 目录", async () => {
    const selected = await selectResources();
    if (!selected?.length || disposed) return;
    resources = selected; current = null; manualSidecars = []; files.replaceChildren(); invalidate();
    status.textContent = `已选择 ${resources.length} 个资源，请识别或手动填写资料。`;
  });
  button("探测与识别", () => analyze());
  const metadata = el("fieldset"); el("legend", "影视资料", metadata);
  const query = input("搜索名称", "", "text", metadata);
  const provider = select("资料来源", [["tmdb", "TMDB"], ["bangumi", "Bangumi"], ["ptgen", "PT-Gen"]], metadata);
  const candidates = el("div", undefined, metadata);
  button("搜索资料", async () => {
    const ticket = ++generation;
    const result = await api(`/api/v2/metadata/search?query=${encodeURIComponent(query.value)}&media_type=${type.value}&providers=${provider.value}`);
    if (ticket !== generation || disposed) return;
    candidates.replaceChildren();
    for (const item of result.items || []) {
      button(`${item.title || item.original_title || item.id} ${item.year || ""}`, () => analyze({ provider: item.provider, id: item.id, media_type: item.media_type, ...(item.media_type === "movie" ? {} : { season: number(season.value, 1) }) }), candidates);
    }
    status.textContent = (result.warnings || []).map(x => x.message).join("；") || `找到 ${result.items.length} 个候选，请选择。`;
  }, metadata);
  button("确认当前影视身份", async () => {
    const identity = current?.identity;
    if (!identity) throw new Error("请先搜索并选择影视资料。");
    const selectedProvider = identity.tmdb_id ? "tmdb" : "bangumi";
    await analyze({ provider: selectedProvider, id: identity.tmdb_id || identity.bangumi_id, media_type: identity.media_type, ...(identity.media_type === "movie" ? {} : { season: number(season.value, 1) }) });
  }, metadata);
  const titleChoice = select("名称候选", [["", "自定义名称 / 当前选择"]], metadata);
  const customTitle = input("自定义名称", "", "text", metadata);
  const year = input("年份", "", "number", metadata);
  const rulesPanel = el("fieldset"); el("legend", "命名规则（默认值不是探测结果）", rulesPanel);
  const source = input("片源", "WEB-DL", "text", rulesPanel);
  const group = input("制作组", "HDVWEB", "text", rulesPanel);
  const season = input("默认季号", "1", "number", rulesPanel); season.min = "0";
  const episodeMode = select("集数规则", [["detected", "采用识别结果 / 逐项修正"], ["start", "从指定集号排序"], ["offset", "使用已保存的季集偏移规则"]], rulesPanel);
  const start = input("起始集号", "1", "number", rulesPanel);
  const step = input("集数步长", "1", "number", rulesPanel); step.min = "1";
  const direction = select("集数方向", [["increment", "递增"], ["decrement", "递减"]], rulesPanel);
  const templateMode = select("预览范围", [["files", "视频及已关联字幕 / 音轨"], ["season", "仅整季目录（与内部文件分开操作）"]], rulesPanel);
  const template = el("textarea", undefined, el("label", "自定义模板（留空采用内置模板）", rulesPanel)); template.rows = 3; template.maxLength = 500; template.addEventListener("input", invalidate);
  const optional = {};
  for (const [key, label] of [["include_hdr", "HDR"], ["include_channels", "声道"], ["include_frame_rate", "帧率"], ["include_bit_depth", "位深"]]) {
    optional[key] = input(label, "", "checkbox", rulesPanel);
  }
  const files = el("div"); files.className = "v2-naming__files";
  const memory = el("fieldset"); el("legend", "规则记忆", memory);
  const scope = select("保存层级", [["global", "全局"], ["series", "影视系列"], ["season", "当前季集"]], memory);
  const offset = input("季集偏移", "0", "number", memory);
  let memoryRevision = 0;
  button("读取规则", async () => {
    const key = identityKey();
    const params = scope.value === "global" ? "" : `?identity_key=${encodeURIComponent(key)}&season_key=${season.value}`;
    const result = await api("/api/v2/naming/memory" + params);
    memoryRevision = result.revision?.[scope.value] || 0;
    const saved = result.effective?.naming_rule || {};
    source.value = saved.source?.value ?? saved.source ?? "WEB-DL";
    group.value = saved.group?.value ?? saved.group ?? "HDVWEB";
    if (result.effective?.selected_title) customTitle.value = result.effective.selected_title;
    offset.value = result.effective?.episode_rules?.offset ?? 0;
    invalidate(); status.textContent = "规则已读取；重新识别后生成预览。";
  }, memory);
  scope.addEventListener("change", () => { memoryRevision = 0; });
  button("保存规则", async () => {
    const result = await api("/api/v2/naming/memory", "PUT", { scope: scope.value, identity_key: scope.value === "global" ? "global" : identityKey(), season_key: scope.value === "season" ? number(season.value, 1) : -1,
      expected_revision: memoryRevision, ...(customTitle.value ? { selected_title: customTitle.value } : {}), naming_rule: collectRules(), episode_rules: { offset: number(offset.value, 0) } });
    memoryRevision = result.revision;
    status.textContent = "已保存规则；仅保存偏好和映射，不保存生成文件名。";
  }, memory);
  function identityKey() {
    const identity = current?.identity;
    if (!identity?.confirmed) throw new Error("请先选择并确认影视身份。");
    if (identity.tmdb_id) return `tmdb:${identity.media_type === "movie" ? "movie" : "tv"}:${identity.tmdb_id}`;
    if (identity.bangumi_id) return `bangumi:subject:${identity.bangumi_id}`;
    throw new Error("当前资料不支持影视系列规则记忆。");
  }
  const actions = el("div"); actions.className = "v2-naming__toolbar";
  button("生成命名预览", async () => {
    if (!current) await analyze();
    const ticket = generation;
    const overrides = { file_values: {}, episode_overrides: {}, manual_names: {}, sidecars: manualSidecars };
    for (const row of files.querySelectorAll("[data-file]")) {
      const fields = {};
      if (year.value && String(current.metadata?.year?.value || "") !== year.value) fields.year = year.value;
      for (const control of row.querySelectorAll("[data-field]")) {
        if (!control.dataset.dirty) continue;
        fields[control.dataset.field] = control.type === "number" ? number(control.value, null) : control.value;
      }
      overrides.file_values[row.dataset.file] = fields;
    }
    const result = await api("/api/v2/naming/preview", "POST", { context: current, overrides, rules: collectRules() });
    if (ticket !== generation || disposed) return;
    preview = result;
    current.revision = result.context_revision;
    if (year.value) current.metadata.year = {value: year.value, source: "manual", source_ref: null, manual: true, confirmed: true, confidence: null, updated_at: new Date().toISOString()};
    const selected = (current.titles || []).find(item => item.id === titleChoice.value);
    const chosen = selected?.text || customTitle.value;
    if (chosen) current.selected_title = {value: chosen, source: "manual", source_ref: selected?.id || null, manual: true, confirmed: true, confidence: null, updated_at: new Date().toISOString()};
    onContextChange(structuredClone(current));
    showPreview(result);
  }, actions);
  const apply = button("确认执行本次预览", async () => {
    if (!preview?.can_apply) return;
    const route = preview.undo ? "/api/v2/naming/undo/apply" : "/api/v2/naming/apply";
    const result = await api(route, "POST", { preview_token: preview.preview_token, expected_revision: preview.context_revision, confirm: true, idempotency_key: preview.key });
    lastBatch = result.batch_id; onTask(result.task); apply.disabled = true;
    status.textContent = "已交后台任务执行，请查看任务结果。完成后可预览撤销。";
  }, actions); apply.disabled = true;
  button("预览撤销", async () => {
    if (!lastBatch) throw new Error("还没有可撤销批次。");
    const result = await api("/api/v2/naming/undo/preview", "POST", { batch_id: lastBatch });
    preview = { ...result, undo: true }; showPreview(preview);
  }, actions);
  const output = el("div"); output.className = "v2-naming__preview";
  function number(text, fallback) { return text === "" ? fallback : Number(text); }
  function collectRules() {
    const kind = templateMode.value === "season" ? "season" : type.value;
    return { template_id: kind, ...(template.value ? { templates: { [kind]: template.value } } : {}),
      ...(titleChoice.value ? { selected_title_id: titleChoice.value } : customTitle.value ? { custom_title: customTitle.value } : {}),
      source: source.value, group: group.value, season_default: number(season.value, 1), episode_mode: episodeMode.value,
      start_episode: number(start.value, 1), episode_step: number(step.value, 1), episode_direction: direction.value,
      ...Object.fromEntries(Object.entries(optional).map(([key, control]) => [key, control.checked])) };
  }
  async function analyze(selection) {
    if (!resources.length) throw new Error("请先选择视频或目录。");
    const ticket = ++generation;
    preview = null; apply.disabled = true; output.replaceChildren();
    const manual = {};
    if (customTitle.value) manual.selected_title = customTitle.value;
    if (year.value) manual.year = year.value;
    const result = await api("/api/v2/resources/resolve", "POST", { resources, origins: current?.origins || [], context: current,
      selection: selection || null, manual_overrides: manual });
    if (ticket !== generation || disposed) return;
    current = result.context; preview = null; apply.disabled = true;
    renderContext(); onContextChange(structuredClone(current));
    status.textContent = (current.warnings || []).map(x => x.message).join("；") || "探测完成，请选择名称并检查逐文件参数。";
  }
  function renderContext() {
    if (!current) return;
    candidates.replaceChildren();
    for (const item of current.identity_candidates || []) button(`${item.title || item.original_title || item.id} (${item.provider} · ${item.media_type})`, () => analyze({provider: item.provider, id: item.id, media_type: item.media_type, ...(item.media_type === "movie" ? {} : {season: number(season.value, 1)})}), candidates);
    titleChoice.replaceChildren();
    const blank = el("option", "自定义名称 / 当前选择", titleChoice); blank.value = "";
    for (const candidate of current.titles || []) { const option = el("option", `${candidate.text} (${candidate.language} · ${candidate.kind})`, titleChoice); option.value = candidate.id; option.selected = !!candidate.selected; }
    if (!customTitle.value) customTitle.value = current.selected_title?.value || "";
    if (!year.value) year.value = current.metadata?.year?.value || "";
    files.replaceChildren();
    for (const file of current.files || []) {
      const row = el("fieldset", undefined, files); row.dataset.file = file.ref.relative_path;
      el("legend", file.ref.relative_path, row);
      if (file.probe?.probe_error) el("p", file.probe.probe_error, row);
      for (const [key, label, kind, origin] of [["season", "季号", "number", file.episode], ["episode", "集号", "number", file.episode], ["resolution", "分辨率", "text", file.probe], ["video_codec", "视频编码", "text", file.probe], ["audio_codec", "音频编码", "text", file.probe], ["audio_channels", "声道", "text", file.probe], ["frame_rate", "帧率（分子/分母）", "text", file.probe], ["bit_depth", "位深", "number", file.probe], ["hdr", "HDR", "text", file.probe]]) {
        const actual = origin?.[key]?.value;
        const display = key === "frame_rate" && actual?.numerator ? `${actual.numerator}/${actual.denominator}` : actual;
        const control = input(`${label} (${origin?.[key]?.source || "未知"})`, Array.isArray(display) ? display.join(".") : display ?? "", kind, row);
        control.dataset.field = key;
        control.addEventListener("input", () => { control.dataset.dirty = "true"; });
      }
      const sidecars = (file.sidecars || []).map(x => x.ref.relative_path);
      if (sidecars.length) el("p", "同步关联：" + sidecars.join("、"), row);
      const sidecarKind = select("关联类型", [["external_audio", "外置音轨"], ["subtitle", "字幕"]], row);
      const sidecarSuffix = input("关联文件尾缀（例如 eng.forced）", "", "text", row);
      button("选择并确认关联到本视频", async () => {
        const refs = await selectResources({ kind: "sidecar" });
        for (const ref of refs || []) manualSidecars.push({ ref, video_ref: file.ref, kind: sidecarKind.value, language: null, suffix_tokens: sidecarSuffix.value ? sidecarSuffix.value.split(".") : [], association: "manual", confirmed: true });
        invalidate(); status.textContent = "已确认人工关联，生成新预览后检查目标名。";
      }, row);
    }
  }
  function showPreview(result) {
    output.replaceChildren();
    preview.key = globalThis.crypto.randomUUID ? globalThis.crypto.randomUUID() : Array.from(globalThis.crypto.getRandomValues(new Uint8Array(24)), x => x.toString(16).padStart(2, "0")).join("");
    for (const op of result.operations || []) {
      const item = el("p", `${op.source.relative_path} → ${op.target_relative_path}`, output);
      if (op.sidecar_of) el("small", "关联文件", item);
      for (const warning of op.warnings || []) el("small", warning.message || String(warning), item);
    }
    for (const conflict of result.conflicts || []) el("p", conflict.message, output).className = "v2-naming__error";
    apply.disabled = !result.can_apply;
    status.textContent = result.can_apply ? "预览已生成，尚未修改文件。请核对后确认执行。" : "存在冲突，请修正后重新预览。";
  }
  renderContext();
  return {
    setContext(value) { invalidate(); current = structuredClone(value); resources = current.resources; renderContext(); },
    setBatch(batchId) { lastBatch = batchId; },
    destroy() { disposed = true; generation += 1; root.remove(); }
  };
}
