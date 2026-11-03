const renameSourceDefaults = ["WEB-DL", "WEBRip", "BluRay", "HDTV", "DVD"];
const renameSourceStorageKey = "biliRemote.rename.sources";
const renameLastSourceStorageKey = "biliRemote.rename.lastSource";
let mediaBrowser = null;
let renameDefaults = {defaultSeason: "01", defaultSource: "", customSources: []};

function applyRenameSettings(settings) {
  renameDefaults = {
    defaultSeason: String(settings.rename_default_season || "01"),
    defaultSource: String(settings.rename_default_source || ""),
    customSources: Array.isArray(settings.rename_custom_sources)
      ? settings.rename_custom_sources.filter(value => typeof value === "string" && value.trim())
      : []
  };
}

async function loadRenameSettings() {
  try {
    applyRenameSettings(await api("/api/settings"));
  } catch {
    // The browser and rename workbench remain usable with built-in defaults.
  }
}

document.addEventListener("rename-settings-updated", event => {
  if (event.detail) applyRenameSettings(event.detail);
});

function readRenameSourceState() {
  try {
    const raw = window.localStorage.getItem(renameSourceStorageKey);
    const parsed = raw ? JSON.parse(raw) : [];
    const custom = Array.isArray(parsed)
      ? parsed.filter(value => typeof value === "string" && value.trim())
      : [];
    const lastSource = window.localStorage.getItem(renameLastSourceStorageKey) || "";
    return {custom, lastSource};
  } catch {
    return {custom: [], lastSource: ""};
  }
}

function renameSourceOptions() {
  const {custom} = readRenameSourceState();
  return [...new Set([
    ...renameSourceDefaults,
    ...renameDefaults.customSources,
    ...custom.map(value => value.trim())
  ])];
}

function rememberRenameSource(value) {
  const source = String(value || "").trim();
  try {
    if (!source) {
      window.localStorage.setItem(renameLastSourceStorageKey, "");
      return;
    }
    const {custom} = readRenameSourceState();
    const next = [source, ...custom.filter(item => item !== source)].slice(0, 20);
    window.localStorage.setItem(renameSourceStorageKey, JSON.stringify(next));
    window.localStorage.setItem(renameLastSourceStorageKey, source);
    const known = new Set(
      [...renameSourceDefaults, ...renameDefaults.customSources].map(item => item.toLocaleLowerCase())
    );
    if (!known.has(source.toLocaleLowerCase()) && renameDefaults.customSources.length < 50) {
      const customSources = [...renameDefaults.customSources, source];
      renameDefaults = {...renameDefaults, customSources};
      api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({rename_custom_sources: customSources})
      }).catch(() => {});
    }
  } catch {
    // localStorage may be disabled; renaming must continue to work.
  }
}

function renameEpisodeLabel(value) {
  return Number.isInteger(value) ? String(value) : "未识别";
}

function renameProbeLabel(status) {
  if (status === "completed") return "媒体已识别";
  if (status === "unavailable") return "ffprobe 不可用，请手动填写";
  if (status === "failed") return "媒体探测失败，请手动填写";
  if (status === "skipped") return "非视频文件";
  return "";
}

function renameDetectedLabel(item) {
  const parts = [];
  if (Number.isInteger(item?.season)) parts.push(`S${String(item.season).padStart(2, "0")}`);
  if (Number.isInteger(item?.episode)) parts.push(`E${String(item.episode).padStart(2, "0")}`);
  if (item?.episode_ambiguous) return "需确认";
  return parts.join(" / ") || "未识别";
}

function renameDetectedCandidates(item) {
  return (item?.episode_candidates || [])
    .map(candidate => {
      const season = Number.isInteger(candidate.season)
        ? `S${String(candidate.season).padStart(2, "0")}`
        : "";
      return `${season}E${String(candidate.episode).padStart(2, "0")}`;
    })
    .join("、");
}

function renameIssueMap(plan) {
  const issues = new Map();
  for (const error of plan.errors || []) {
    const messages = issues.get(error.file) || [];
    messages.push(error.message);
    issues.set(error.file, messages);
  }
  return issues;
}

function fileRenamePayload(context) {
  const readNumber = (input, fallback) => {
    const raw = input.value.trim();
    return raw ? Number(raw) : fallback;
  };
  const values = {};
  context.valueInputs.forEach((input, key) => {
    values[key] = input.value.trim();
  });
  if (!values.season) values.season = "01";
  const commonSeason = values.season;
  const manualNames = {};
  const episodeOverrides = {};
  const media = {};
  const fileValues = {};
  context.rows.forEach((row, name) => {
    const manualName = row.manual.value.trim();
    if (manualName) manualNames[name] = manualName;
    if (row.episodeEdited) {
      episodeOverrides[name] = row.finalEpisode.value.trim();
    }
    const rowMedia = {};
    row.media.forEach((input, key) => {
      const value = input.value.trim();
      if (value) rowMedia[key] = value;
    });
    if (Object.keys(rowMedia).length) media[name] = rowMedia;
    let season = row.season.value.trim();
    if (!season && !row.seasonEdited) {
      season = commonSeason;
      row.season.value = commonSeason;
    }
    fileValues[name] = {
      season,
      source: row.source.value.trim()
    };
  });
  const startEpisodeValue = readNumber(context.startEpisode, 1);
  const stepValue = readNumber(context.step, 1);
  const offsetValue = readNumber(context.offset, 0);
  const usesStart = context.correctionEnabled && context.mode.value === "start";
  const usesOffset = context.correctionEnabled && context.mode.value === "offset";
  return {
    root_key: context.rootKey,
    directory: context.directory,
    files: context.files,
    template: context.template.value,
    episode_mode: context.correctionEnabled ? context.mode.value : "detected",
    start_episode: usesStart ? startEpisodeValue : 1,
    episode_step: usesStart ? stepValue : 1,
    episode_offset: usesOffset ? offsetValue : 0,
    episode_direction: context.direction.value,
    episode_scope: context.scope.value,
    file_sort: context.fileSort.value,
    episode_overrides: episodeOverrides,
    media,
    file_values: fileValues,
    values,
    manual_names: manualNames
  };
}

function scheduleFileRenamePreview(context, delay = 180) {
  if (!context) return;
  window.clearTimeout(context.previewTimer);
  context.apply.disabled = true;
  context.error.textContent = "";
  context.warning.textContent = context.analysisWarnings.join("；");
  context.status.textContent = "正在检查名称…";
  context.previewTimer = window.setTimeout(() => validateFileRename(context), delay);
}

async function validateFileRename(context) {
  const sequence = ++context.sequence;
  const payload = fileRenamePayload(context);
  context.status.textContent = "正在检查名称…";
  context.apply.disabled = true;
  try {
    const plan = await api("/api/files/rename/preview", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    if (sequence !== context.sequence || !context.panel.isConnected) return;
    context.payload = payload;
    context.preview = plan;
    const issues = renameIssueMap(plan);
    const operations = new Map(
      (plan.operations || []).map(operation => [operation.source_name, operation])
    );
    context.rows.forEach((row, name) => {
      const operation = operations.get(name);
      const messages = issues.get(name) || [];
      const detected = operation?.detected_episode ?? null;
      row.detectedEpisode = detected;
      row.detected.textContent = operation
        ? renameDetectedLabel(operation)
        : renameEpisodeLabel(detected);
      row.detected.title = operation ? renameDetectedCandidates(operation) : "";
      if (!context.correctionEnabled && !row.episodeEdited) {
        row.finalEpisode.value = Number.isInteger(detected) ? String(detected) : "";
      } else if (!row.episodeEdited && operation) {
        row.finalEpisode.value = Number.isInteger(operation.final_episode)
          ? String(operation.final_episode)
          : "";
      }
      row.target.textContent = operation?.target_name || "—";
      row.target.classList.toggle("invalid", messages.length > 0);
      const probeNote = renameProbeLabel(row.probeStatus);
      if (messages.length) {
        row.status.textContent = messages[0];
        row.status.className = "rename-row-status invalid";
      } else if (operation && !operation.changed) {
        row.status.textContent = ["无需修改", probeNote].filter(Boolean).join(" · ");
        row.status.className = "rename-row-status muted";
      } else if (operation) {
        const action = operation.manual ? "使用手动名称" : "待重命名";
        row.status.textContent = [action, probeNote].filter(Boolean).join(" · ");
        row.status.className = "rename-row-status ok";
      } else {
        row.status.textContent = "名称无效";
        row.status.className = "rename-row-status invalid";
      }
    });
    const generalErrors = (plan.errors || []).filter(item => !context.rows.has(item.file));
    const messages = generalErrors.map(item => `${item.file ? `${item.file}: ` : ""}${item.message}`);
    if (messages.length) context.error.textContent = messages.join("；");
    context.warning.textContent = [...context.analysisWarnings, ...(plan.warnings || [])].join("；");
    const canApply = plan.valid && plan.changed_count > 0;
    context.apply.disabled = !canApply;
    context.apply.textContent = canApply
      ? `确认重命名 ${plan.changed_count} 个文件`
      : plan.valid
        ? "没有需要修改的名称"
        : "请先修正名称";
    context.status.textContent = plan.valid
      ? `检查通过，将修改 ${plan.changed_count} 个文件名`
      : `发现 ${plan.errors.length} 个问题`;
  } catch (error) {
    if (sequence !== context.sequence || !context.panel.isConnected) return;
    context.preview = null;
    context.error.textContent = error.message;
    context.apply.disabled = true;
    context.apply.textContent = "请先修正命名参数";
    context.status.textContent = "预览失败";
  }
}

async function analyzeFileRename(context) {
  const sequence = ++context.analysisSequence;
  context.analysisStatus.textContent = "正在读取分辨率、编码和 HDR…";
  try {
    const result = await api("/api/files/rename/analyze", {
      method: "POST",
      body: JSON.stringify({
        root_key: context.rootKey,
        directory: context.directory,
        files: context.files
      })
    });
    if (sequence !== context.analysisSequence || !context.panel.isConnected) return;
    const warnings = [];
    for (const item of result.items || []) {
      const row = context.rows.get(item.name);
      if (!row) continue;
      row.probeStatus = item.status;
      const detected = Number.isInteger(item.episode) ? item.episode : null;
      row.detectedEpisode = detected;
      row.detected.textContent = renameDetectedLabel(item);
      row.detected.title = renameDetectedCandidates(item);
      row.media.forEach((input, key) => {
        const value = item.media?.[key] || "";
        if (!input.dataset.userEdited && !input.value && value) input.value = value;
      });
      if (!context.correctionEnabled && !row.episodeEdited) {
        row.finalEpisode.value = Number.isInteger(detected) ? String(detected) : "";
      } else if (!row.episodeEdited && Number.isInteger(detected)) {
        row.finalEpisode.value = String(detected);
      }
      if (item.episode_ambiguous) {
        warnings.push(
          `${item.name}: 集数存在多个候选（${renameDetectedCandidates(item)}），请手动确认`
        );
      }
      if (item.season_ambiguous) {
        warnings.push(`${item.name}: 季号存在多个候选，请手动确认`);
      }
      if (item.status === "unavailable") {
        warnings.push("ffprobe 不可用，媒体参数需手动填写");
      } else if (item.status === "failed") {
        warnings.push(`${item.name}: ${item.error || "媒体探测失败"}`);
      }
    }
    if (result.mixed_seasons) {
      warnings.push(`识别到多个季号：${(result.seasons || []).join("、")}，请手动确认`);
    }
    context.analysisWarnings = [...new Set(warnings)];
    context.analysisStatus.textContent = "媒体信息识别完成，可逐文件修改";
    scheduleFileRenamePreview(context, 0);
  } catch (error) {
    if (sequence !== context.analysisSequence || !context.panel.isConnected) return;
    context.analysisWarnings = [`媒体识别失败：${error.message}，可手动填写`];
    context.analysisStatus.textContent = "媒体识别失败，可手动填写";
    context.warning.textContent = context.analysisWarnings.join("；");
  }
}

async function applyFileRename(context) {
  if (!context.preview?.valid || !context.payload || !context.preview.fingerprint) return;
  const count = context.preview.changed_count;
  if (!confirm(`确认重命名 ${count} 个文件？只修改文件名，不移动、不复制、不删除文件。`)) return;
  setBusy(context.apply, true);
  context.apply.textContent = "正在重命名…";
  try {
    const result = await api("/api/files/rename/apply", {
      method: "POST",
      body: JSON.stringify({...context.payload, fingerprint: context.preview.fingerprint})
    });
    const renamed = result.renamed || [];
    document.dispatchEvent(new CustomEvent("files:renamed", {detail:{rootKey:context.rootKey,directory:context.directory,renamed}}));
    const details = renamed
      .slice(0, 8)
      .map(item => `${item.source_name} → ${item.target_name}`)
      .join("；");
    const more = renamed.length > 8 ? `；另有 ${renamed.length - 8} 个文件` : "";
    closeModal();
    if (mediaBrowser) {
      await mediaBrowser.refresh();
      if (mediaBrowser.el.actionStatus) {
        mediaBrowser.el.actionStatus.textContent = `已重命名 ${result.renamed_count} 个文件${details ? `：${details}${more}` : ""}`;
        mediaBrowser.el.actionStatus.title = mediaBrowser.el.actionStatus.textContent;
      }
    }
  } catch (error) {
    setBusy(context.apply, false);
    alert(error.message);
    scheduleFileRenamePreview(context, 0);
  }
}

function createRenameValueField(labelText, key, placeholder = "") {
  const label = element("label", "rename-value-field");
  label.append(element("span", null, labelText));
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = placeholder;
  input.dataset.templateValue = key;
  label.append(input);
  return {label, input};
}

function createRenameMediaInput(key, placeholder) {
  const input = document.createElement("input");
  input.type = "text";
  input.className = "rename-media-input";
  input.placeholder = placeholder;
  input.dataset.mediaKey = key;
  return input;
}

function createRenameCell(labelText, child) {
  const cell = element("div", "rename-field-cell");
  cell.append(element("span", "rename-cell-label", labelText), child);
  return cell;
}

function resetFileRenameEpisodes(context, {clearManual = false} = {}) {
  const usesStart = context.mode.value === "start";
  if (clearManual) {
    context.rows.forEach(row => {
      row.episodeEdited = false;
    });
  }
  context.startLabel.classList.toggle("hidden", !usesStart);
  context.stepLabel.classList.toggle("hidden", !usesStart);
  context.directionLabel.classList.toggle("hidden", !usesStart);
  context.offsetLabel.classList.toggle("hidden", usesStart);
  context.rows.forEach(row => {
    row.finalEpisode.disabled = false;
    if (!context.correctionEnabled && !row.episodeEdited) {
      row.finalEpisode.value = Number.isInteger(row.detectedEpisode)
        ? String(row.detectedEpisode)
        : "";
    }
  });
  context.correctionControls.classList.toggle("hidden", !context.correctionEnabled);
  scheduleFileRenamePreview(context, 0);
}

function openFileRenameWorkbench(items = null) {
  const files = (items || mediaBrowser?.selectedItems() || [])
    .filter(item => item && !item.is_dir && item.allowed && item.kind === "video")
    .map(item => ({...item, root_key: item.root_key || mediaBrowser?.rootKey}));
  if (!files.length) return;
  const rootKey = files[0].root_key;
  if (files.some(item => item.root_key !== rootKey)) return;
  const directories = [...new Set(files.map(item => String(item.relative_path || item.name).split("/").slice(0, -1).join("/")))];
  if (directories.length !== 1) throw new Error("批量重命名请选择同一目录中的视频");
  const context = {
    rootKey,
    directory: directories[0],
    files: files.map(item => item.name),
    rows: new Map(),
    valueInputs: new Map(),
    correctionEnabled: false,
    analysisWarnings: [],
    analysisSequence: 0,
    sequence: 0,
    previewTimer: null,
    preview: null,
    payload: null
  };
  const panel = element("div", "rename-workbench");
  context.panel = panel;
  panel.append(element("h2", null, "重命名文件"));
  panel.append(
    element(
      "p",
      "muted",
      `当前目录：${mediaBrowser?.path || "/"} · 已选择 ${files.length} 个文件。确认后直接改名，不移动或复制文件。`
    )
  );

  const options = element("div", "rename-options");
  const templateLabel = element("label", "rename-template-field");
  templateLabel.append(element("span", null, "命名模板"));
  const template = document.createElement("input");
  template.type = "text";
  template.value = "{original_name}.{ext}";
  template.spellcheck = false;
  templateLabel.append(template);
  options.append(templateLabel);
  panel.append(options);

  const quickRow = element("div", "rename-template-actions");
  const originalButton = element("button", "button ghost small", "保持原名");
  originalButton.type = "button";
  const episodeButton = element("button", "button ghost small", "剧集模板");
  episodeButton.type = "button";
  const movieButton = element("button", "button ghost small", "电影模板");
  movieButton.type = "button";
  quickRow.append(originalButton, episodeButton, movieButton);
  quickRow.append(
    element(
      "span",
      "muted",
      "模板变量：{original_name} {ext} {episode:02d} {title} {year} {season} {resolution} {source} {hdr} {video_codec} {audio_codec} {group}"
    )
  );
  panel.append(quickRow);

  const variables = document.createElement("details");
  variables.className = "rename-variables";
  variables.append(element("summary", null, "公共命名参数（媒体信息在下方逐文件识别）"));
  const variableGrid = element("div", "rename-variable-grid");
  for (const [labelText, key, placeholder] of [
    ["标题", "title", "可留空"],
    ["中文名", "title_zh", "可从 TMDB 填入"],
    ["英文名", "title_en", "可从 TMDB 填入"],
    ["年份", "year", "可留空，例如 2026"],
    ["季号", "season", "默认 01"],
    ["片源", "source", "例如 WEB-DL"],
    ["制作组", "group", "可留空"]
  ]) {
    const field = createRenameValueField(labelText, key, placeholder);
    context.valueInputs.set(key, field.input);
    variableGrid.append(field.label);
  }
  const catalog = element("button", "button secondary small", "TMDB 选择作品 / 双语命名");
  catalog.type = "button";
  catalog.addEventListener("click", () => window.MediaCatalog.open(fields => {
    for(const key of ["title_zh","title_en","year"]) context.valueInputs.get(key).value = fields[key] || "";
    context.valueInputs.get("title").value = [fields.title_zh,fields.title_en].filter(Boolean).join(" ");
    context.template.value = fields.media_type === "movie" ? "{title_zh} {title_en} {year} {resolution} {source} {hdr} {video_codec} {audio_codec}-{group}.{ext}" : "{title_zh} {title_en} {year} S{season:02d}E{episode:02d} {resolution} {source} {hdr} {video_codec} {audio_codec}-{group}.{ext}";
    scheduleFileRenamePreview(context,0);
  }, Object.fromEntries([...context.valueInputs].map(([key,input])=>[key,input.value]))));
  quickRow.append(catalog);
  const seasonInput = context.valueInputs.get("season");
  seasonInput.value = renameDefaults.defaultSeason || "01";
  seasonInput.addEventListener("input", () => {
    context.rows.forEach(row => {
      if (!row.seasonEdited) row.season.value = seasonInput.value;
    });
  });
  const sourceInput = context.valueInputs.get("source");
  const sourceState = readRenameSourceState();
  sourceInput.value = sourceState.lastSource || renameDefaults.defaultSource || "";
  sourceInput.setAttribute("list", "rename-source-options");
  sourceInput.addEventListener("change", () => rememberRenameSource(sourceInput.value));
  sourceInput.addEventListener("input", () => {
    context.rows.forEach(row => {
      if (!row.sourceEdited) row.source.value = sourceInput.value;
    });
  });
  const sourceOptions = document.createElement("datalist");
  sourceOptions.id = "rename-source-options";
  for (const value of renameSourceOptions()) {
    const option = document.createElement("option");
    option.value = value;
    sourceOptions.append(option);
  }
  variableGrid.append(sourceOptions);
  variables.append(variableGrid);
  panel.append(variables);

  const correction = element("div", "rename-correction");
  const correctionToggle = element("label", "rename-correction-toggle");
  const correctionCheckbox = document.createElement("input");
  correctionCheckbox.type = "checkbox";
  correctionToggle.append(
    correctionCheckbox,
    element("span", null, "启用集数修正"),
    element("small", "muted", "默认保留每个文件识别出的集数，不自动重排")
  );
  correction.append(correctionToggle);
  const correctionControls = element("div", "rename-correction-controls hidden");
  const modeLabel = element("label");
  modeLabel.append(element("span", null, "修正方式"));
  const mode = document.createElement("select");
  for (const [value, text] of [
    ["start", "按顺序重新编号"],
    ["offset", "原集数偏移"]
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    mode.append(option);
  }
  modeLabel.append(mode);
  const directionLabel = element("label");
  directionLabel.append(element("span", null, "方向"));
  const direction = document.createElement("select");
  for (const [value, text] of [
    ["increment", "递增"],
    ["decrement", "递减"]
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    direction.append(option);
  }
  directionLabel.append(direction);
  const startLabel = element("label");
  startLabel.append(element("span", null, "起始集数"));
  const startEpisode = document.createElement("input");
  startEpisode.type = "number";
  startEpisode.min = "0";
  startEpisode.step = "1";
  startEpisode.value = "1";
  startLabel.append(startEpisode);
  const stepLabel = element("label");
  stepLabel.append(element("span", null, "步长"));
  const step = document.createElement("input");
  step.type = "number";
  step.min = "1";
  step.step = "1";
  step.value = "1";
  stepLabel.append(step);
  const offsetLabel = element("label");
  offsetLabel.append(element("span", null, "集数偏移"));
  const offset = document.createElement("input");
  offset.type = "number";
  offset.step = "1";
  offset.value = "0";
  offsetLabel.append(offset);
  const scopeLabel = element("label");
  scopeLabel.append(element("span", null, "作用范围"));
  const scope = document.createElement("select");
  for (const [value, text] of [
    ["all", "全部所选文件"],
    ["detected", "仅已识别集数"],
    ["missing", "仅未识别集数"]
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    scope.append(option);
  }
  scopeLabel.append(scope);
  const sortLabel = element("label");
  sortLabel.append(element("span", null, "文件排序"));
  const fileSort = document.createElement("select");
  for (const [value, text] of [
    ["natural", "自然排序"],
    ["name", "文件名 A→Z"],
    ["name_desc", "文件名 Z→A"],
    ["mtime", "修改时间从早到晚"],
    ["mtime_desc", "修改时间从晚到早"],
    ["selection", "当前选择顺序"]
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = text;
    fileSort.append(option);
  }
  sortLabel.append(fileSort);
  correctionControls.append(
    modeLabel,
    directionLabel,
    startLabel,
    stepLabel,
    offsetLabel,
    scopeLabel,
    sortLabel
  );
  correction.append(correctionControls);
  panel.append(correction);

  const analysisStatus = element("p", "rename-status muted", "等待识别媒体信息…");
  panel.append(analysisStatus);

  const preview = element("section", "rename-preview");
  preview.append(element("h3", null, "重命名预览"));
  const previewHead = element("div", "rename-preview-head");
  previewHead.append(
    element("span", null, "原文件名"),
    element("span", null, "识别季/集"),
    element("span", null, "季号"),
    element("span", null, "集数"),
    element("span", null, "分辨率"),
    element("span", null, "视频编码"),
    element("span", null, "音频编码"),
    element("span", null, "HDR"),
    element("span", null, "片源类型"),
    element("span", null, "新文件名"),
    element("span", null, "手动名称"),
    element("span", null, "状态")
  );
  preview.append(previewHead);
  const previewList = element("div", "rename-preview-list");
  for (const file of files) {
    const row = element("div", "rename-preview-row");
    const sourceName = element("code", "rename-source", file.name);
    const detected = element("code", "rename-detected", "—");
    const season = document.createElement("input");
    season.type = "text";
    season.inputMode = "numeric";
    season.maxLength = 4;
    season.placeholder = "01";
    season.value = seasonInput.value || "01";
    season.setAttribute("aria-label", `${file.name} 季号`);
    season.dataset.seasonInput = file.name;
    const finalEpisode = document.createElement("input");
    finalEpisode.type = "number";
    finalEpisode.min = "0";
    finalEpisode.step = "1";
    finalEpisode.placeholder = "未识别";
    finalEpisode.disabled = false;
    finalEpisode.setAttribute("aria-label", `${file.name} 集数`);
    finalEpisode.dataset.episodeInput = file.name;
    const media = new Map();
    for (const [key, placeholder] of [
      ["resolution", "1080p"],
      ["video_codec", "H264"],
      ["audio_codec", "AAC"],
      ["hdr", "HDR"]
    ]) {
      const input = createRenameMediaInput(key, placeholder);
      input.setAttribute("aria-label", `${file.name} ${placeholder}`);
      media.set(key, input);
    }
    const source = document.createElement("input");
    source.type = "text";
    source.placeholder = "如 WEB-DL";
    source.value = sourceInput.value;
    source.setAttribute("list", "rename-source-options");
    source.setAttribute("aria-label", `${file.name} 片源类型`);
    source.dataset.sourceInput = file.name;
    const target = element("code", "rename-target", "—");
    const manual = document.createElement("input");
    manual.type = "text";
    manual.placeholder = "可选";
    manual.setAttribute("aria-label", `${file.name} 手动完整文件名`);
    manual.dataset.manualName = file.name;
    const status = element("span", "rename-row-status muted", "等待检查");
    row.append(
      createRenameCell("原文件名", sourceName),
      createRenameCell("识别季/集", detected),
      createRenameCell("季号", season),
      createRenameCell("集数", finalEpisode),
      createRenameCell("分辨率", media.get("resolution")),
      createRenameCell("视频编码", media.get("video_codec")),
      createRenameCell("音频编码", media.get("audio_codec")),
      createRenameCell("HDR", media.get("hdr")),
      createRenameCell("片源类型", source),
      createRenameCell("新文件名", target),
      createRenameCell("手动名称", manual),
      createRenameCell("状态", status)
    );
    const rowState = {
      detected,
      detectedEpisode: null,
      season,
      source,
      finalEpisode,
      media,
      target,
      manual,
      status,
      episodeEdited: false,
      seasonEdited: false,
      sourceEdited: false,
      probeStatus: ""
    };
    context.rows.set(file.name, rowState);
    previewList.append(row);
  }
  preview.append(previewList);
  panel.append(preview);

  const error = element("p", "form-error", "");
  error.setAttribute("role", "alert");
  const warning = element("p", "rename-warning", "");
  const status = element("p", "rename-status muted", "正在检查名称…");
  panel.append(error, warning, status);

  const footer = element("div", "rename-footer");
  const cancel = element("button", "button ghost", "取消");
  cancel.type = "button";
  const apply = element("button", "button primary", "正在检查…");
  apply.type = "button";
  apply.disabled = true;
  footer.append(cancel, apply);
  panel.append(footer);

  context.template = template;
  context.mode = mode;
  context.direction = direction;
  context.directionLabel = directionLabel;
  context.startLabel = startLabel;
  context.stepLabel = stepLabel;
  context.offsetLabel = offsetLabel;
  context.scope = scope;
  context.fileSort = fileSort;
  context.startEpisode = startEpisode;
  context.step = step;
  context.offset = offset;
  context.correctionControls = correctionControls;
  context.analysisStatus = analysisStatus;
  context.error = error;
  context.warning = warning;
  context.status = status;
  context.apply = apply;

  const changed = () => scheduleFileRenamePreview(context);
  template.addEventListener("input", changed);
  startEpisode.addEventListener("input", changed);
  step.addEventListener("input", changed);
  offset.addEventListener("input", changed);
  direction.addEventListener("change", changed);
  scope.addEventListener("change", changed);
  fileSort.addEventListener("change", changed);
  context.valueInputs.forEach(input => input.addEventListener("input", changed));
  context.rows.forEach(row => {
    row.manual.addEventListener("input", changed);
    row.season.addEventListener("input", () => {
      row.seasonEdited = true;
      changed();
    });
    row.source.addEventListener("change", () => rememberRenameSource(row.source.value));
    row.source.addEventListener("input", () => {
      row.sourceEdited = true;
      changed();
    });
    row.finalEpisode.addEventListener("input", () => {
      row.episodeEdited = true;
      changed();
    });
    row.media.forEach(input => {
      input.addEventListener("input", () => {
        input.dataset.userEdited = "true";
        changed();
      });
    });
  });
  correctionCheckbox.addEventListener("change", () => {
    context.correctionEnabled = correctionCheckbox.checked;
    resetFileRenameEpisodes(context, {clearManual: true});
  });
  mode.addEventListener("change", () => resetFileRenameEpisodes(context));
  originalButton.addEventListener("click", () => {
    template.value = "{original_name}.{ext}";
    scheduleFileRenamePreview(context, 0);
  });
  episodeButton.addEventListener("click", () => {
    template.value = "{title}.S{season:02d}E{episode:02d}.{year}.{resolution}.{source}.{hdr}.{video_codec}.{audio_codec}-{group}.{ext}";
    variables.open = true;
    scheduleFileRenamePreview(context, 0);
  });
  movieButton.addEventListener("click", () => {
    template.value = "{title}.{year}.{resolution}.{source}.{hdr}.{video_codec}.{audio_codec}-{group}.{ext}";
    variables.open = true;
    scheduleFileRenamePreview(context, 0);
  });
  cancel.addEventListener("click", closeModal);
  apply.addEventListener("click", () => applyFileRename(context));

  showModal(panel);
  scheduleFileRenamePreview(context, 0);
  analyzeFileRename(context);
}

async function initFileBrowser() {
  const host = document.querySelector("#file-browser-host");
  document.addEventListener("app:files-refresh", () => {
    mediaBrowser?.loadDirectory({page:mediaBrowser.page,clearSelection:false}).catch(() => {});
  });
  await loadAppSettings();
  if (appSettings) applyRenameSettings(appSettings);
  else await loadRenameSettings();
  try {
    mediaBrowser = new FileBrowser(host, {
      mode: "full",
      scope: "browse",
      allowMultiple: true,
      title: "本地文件",
      onMediaRename: items => openFileRenameWorkbench(items),
      onTool: (kind, items, rootKey) => window.BiliModules.runFileTool(kind, items, rootKey),
      onCollage: (items, rootKey) => {
        if (window.SeedAssistant) window.SeedAssistant.select(items.map(item => ({...item, root_key:rootKey})));
        else if (window.CollageTool) window.CollageTool.open(items, rootKey);
      }
    });
    await mediaBrowser.init();
    try {
      for (const id of JSON.parse(localStorage.getItem("biliRemote.fileTasks") || "[]")) mediaBrowser.showTaskProgress(id);
    } catch {}
  } catch (error) {
    host.replaceChildren(element("p", "empty", `文件浏览器初始化失败：${error.message}`));
  }
}

initFileBrowser();

document.querySelector("#screenshot-sessions-button")?.addEventListener("click", () => {
  if (window.CollageTool) window.CollageTool.openRecent();
});

document.addEventListener("files:locate", async event => {
  const source=event.detail;
  if(!mediaBrowser || !source) return;
  const path=String(source.relative_path || "");
  const parent=source.is_dir ? path : path.split("/").slice(0,-1).join("/");
  try {await mediaBrowser.navigate(source.root_key,parent,{focus:source.is_dir ? null : path});}
  catch(error) {mediaBrowser.el.listError.textContent=error.message;}
});
