import "./quality-format.js";

const {batchTrackLabel, bitrateRange, combinedSize, formatResourceSize, preferredTier, tierCatalogue, trackFamily, trackTier} = globalThis.BiliDownloadQuality;

export function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== null) node.textContent = text;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function tracksFor(episode, preferredAudio) {
  const toOption = (track, video, audio, sound) => {
    const tbr = track.bandwidth / 1000;
    const sizeOf = item => item.size || (item.bandwidth > 0 && episode.duration ? item.bandwidth * episode.duration / 8 : null);
    const ownSize = sizeOf(track);
    const total = ownSize && sound ? (sizeOf(sound) ? ownSize + sizeOf(sound) : null) : ownSize;
    return {
      format_id: track.format_id || String(track.quality),
      height: track.height, fps: track.fps,
      vcodec: video === null ? "none" : track.codec,
      acodec: video === null ? track.codec : "none",
      dynamic_range: video === null ? null : track.dynamic_range || ({125: "HDR10", 126: "DV"}[track.quality] || "SDR"),
      tbr, size_with_audio: total,
      size_estimated: Boolean(total && (!track.size || (sound && !sound.size))),
      video, audio,
    };
  };
  const audio = episode.audio || [];
  let best = 0;
  audio.forEach((track, index) => {
    if (track.bandwidth > audio[best].bandwidth) best = index;
  });
  if (audio[preferredAudio]) best = preferredAudio;
  return [
    ...(audio.length ? (episode.video || []).map((track, index) => toOption(track, index, best, audio[best])) : []),
    ...audio.map((track, index) => toOption(track, null, index)),
  ];
}

export function hasTrack(episode, pick) {
  return Boolean(episode?.audio?.[pick.audio] && (pick.video === null || episode.video?.[pick.video]));
}

export function sourceName(source) {
  return source === "tv" ? "本机 TV" : "本机 Web";
}

export function mountPicker(root, episodes, {order = "asc", rows = 3, onSelect, onChange, active = -1, initial, queued = []} = {}) {
  const pageSize = 50;
  let descending = order === "desc";
  let page = descending ? Math.floor((episodes.length - 1) / pageSize) : 0;
  const selected = new Set();
  const overrides = new Map(initial?.overrides);
  const audioOverrides = new Map(initial?.audioOverrides);
  let targetKey = initial?.targetKey || null;
  let allowLower = initial?.allowLower || false;
  const grid = el("div", null, {class: "br-grid", "aria-label": "正片选集"});
  const summary = el("small", "", {"aria-live": "polite"});
  const ranges = el("div", null, {class: "br-ranges"});
  const unified = el("div", null, {class: "br-action"});
  const totals = el("small", "", {class: "br-muted"});
  const toolbar = el("div", null, {class: "br-action"});
  const formatList = el("div", null, {class: "br-formats"});
  const details = el("details", null);
  details.append(el("summary", "逐集明细"), formatList);
  const box = el("section", null, {class: "br-picker"});
  grid.style.setProperty("--br-rows", rows);
  const button = (text, fn) => {
    const control = el("button", text, {type: "button"});
    control.onclick = fn;
    return control;
  };
  const sort = value => {
    descending = value;
    page = descending ? Math.floor((episodes.length - 1) / pageSize) : 0;
    drawGrid();
  };
  const asc = button("正序 ↑", () => sort(false));
  const desc = button("倒序 ↓", () => sort(true));
  const all = button("全选", () => {
    const indices = range();
    if (indices.every(i => selected.has(i))) indices.forEach(deselect);
    else for (const i of indices) if (!selected.has(i)) choose(i);
    reflect();
  });
  toolbar.append(asc, desc, all, summary);
  box.append(el("strong", "选集与版本"), toolbar, ranges, grid, unified, totals, details);
  root.replaceChildren(box);
  function range() {
    const first = page * pageSize;
    const ids = episodes.slice(first, first + pageSize).map((_, i) => i + first);
    return descending ? ids.reverse() : ids;
  }
  function choose(i) {
    selected.add(i);
    onSelect?.(i);
  }
  function deselect(i) {
    selected.delete(i);
    overrides.delete(i);
    audioOverrides.delete(i);
  }
  function drawGrid() {
    asc.setAttribute("aria-pressed", String(!descending));
    desc.setAttribute("aria-pressed", String(descending));
    const total = Math.ceil(episodes.length / pageSize);
    const pages = Array.from({length: total}, (_, i) => i);
    if (descending) pages.reverse();
    ranges.replaceChildren(...(total <= 1 ? [] : pages.map(index => {
      const item = button((index * pageSize + 1) + "–" + Math.min((index + 1) * pageSize, episodes.length), () => {
        page = index;drawGrid();
        ranges.querySelector('[aria-pressed="true"]')?.focus({preventScroll: true});
      });
      item.setAttribute("aria-pressed", String(page === index));
      return item;
    })));
    grid.replaceChildren(...range().map(i => {
      const ep = episodes[i];
      const item = button(String(ep.number || i + 1), () => {
        if (selected.has(i)) deselect(i);
        else {choose(i);details.open = true;}
        reflect();
      });
      item.dataset.index = i;
      item.setAttribute("aria-label", ep.title);
      return item;
    }));
    grid.scrollTop = 0;
    reflect();
  }
  function reflect() {
    for (const item of grid.children) item.setAttribute("aria-pressed", String(selected.has(Number(item.dataset.index))));
    all.textContent = range().every(i => selected.has(i)) ? "取消全选" : "全选";
    const entries = [...selected].map(index => ({index, tracks: tracksFor(episodes[index], audioOverrides.get(index))}));
    const pending = entries.filter(({index, tracks}) => !tracks.length && !episodes[index].error).length;
    const failed = entries.filter(({index}) => episodes[index].error).length;
    const catalogue = tierCatalogue(entries.flatMap(entry => entry.tracks));
    const tier = catalogue.find(item => item.key === targetKey)
      || preferredTier(catalogue, entries.map(entry => entry.tracks));
    if (targetKey && !catalogue.some(item => item.key === targetKey) && !pending) targetKey = null;

    function tierRows(target, manual = true) {
      const lower = allowLower && entries.length > 1 && target?.track.height
        ? catalogue.slice(catalogue.indexOf(target) + 1)
          .find(item => item.track.height && trackFamily(item.track) === trackFamily(target.track)) : null;
      return entries.map(({index, tracks}) => {
        const key = manual ? overrides.get(index) : null;
        const exact = tracks.find(track => trackTier(track) === (key || target?.key));
        const alternate = !key && !exact && lower && tracks.find(track => trackTier(track) === lower.key);
        return {index, track: exact || alternate || null,
          reason: episodes[index].error || (alternate ? "缺档降一档" : (key || audioOverrides.has(index)) && exact ? "单集指定" : !exact ? "目标档位缺失" : "")};
      });
    }
    const rows = tierRows(tier);
    const chosen = rows.filter(row => row.track).map(({index, track}) => ({
      episode: index, video: track.video, audio: track.audio,
    }));
    validChoices = chosen.length === selected.size ? chosen.filter(pick => !queued.some(entry =>
      entry[0] === pick.episode && entry[1] === pick.video && entry[2] === pick.audio)) : [];
    summary.textContent = "已选 " + selected.size + " 项" +
      (pending ? " · " + pending + " 项正在读取档位" : "") +
      (failed ? " · " + failed + " 项读取失败" : "");
    if (failed) details.open = true;

    const focused = document.activeElement?.dataset?.qualityControl;
    unified.replaceChildren();
    if (entries.length) {
      const select = el("select", null, {"aria-label": "统一画质", "data-quality-control": "tier"});
      for (const item of catalogue) {
        const size = combinedSize(tierRows(item, entries.length > 1));
        select.add(new Option(batchTrackLabel(item.track, item, bitrateRange(item.rates)) + " · " + sizeLabel(size), item.key));
      }
      if (!catalogue.length) select.add(new Option("等待确认档位", ""));
      const displayed = entries.length === 1 && rows[0].track ? trackTier(rows[0].track) : tier?.key;
      select.value = displayed || "";
      select.disabled = !catalogue.length;
      select.onchange = () => {
        targetKey = select.value;
        if (entries.length === 1) overrides.clear();
        reflect();
      };
      const label = el("label", "统一画质");
      label.append(select);
      unified.append(label);
      if (entries.length > 1) {
        const fallback = el("input", null, {type: "checkbox", "data-quality-control": "fallback"});
        fallback.checked = allowLower;
        fallback.onchange = () => {allowLower = fallback.checked; reflect();};
        const field = el("label", null, {class: "br-fallback"});
        field.append(fallback, "缺档自动降一档");
        unified.append(field);
      }
      if (focused) unified.querySelector(`[data-quality-control="${focused}"]`)?.focus({preventScroll: true});
    }
    const total = combinedSize(rows);
    totals.textContent = entries.length ? "本档合计 " + sizeLabel(total) +
      (entries.length > 1 && total.complete ? " · 每项约 " + formatResourceSize(total.bytes / entries.length) : "") : "";
    drawFormats(rows, entries, catalogue);
    onChange?.(validChoices);
  }
  function drawFormats(rows, entries, catalogue) {
    details.hidden = !entries.length;
    if (!details.open) return;
    const scroll = formatList.scrollTop;
    const focus = document.activeElement?.dataset?.episodeQuality;
    formatList.replaceChildren();
    for (const [label, subset] of [
      ["档位不同或待确认", rows.filter(row => row.reason)],
      ["与统一档位一致", rows.filter(row => !row.reason)],
    ]) {
      if (!subset.length) continue;
      if (entries.length > 1) formatList.append(el("small", label, {class: "br-muted"}));
      for (const {index, track, reason} of subset) {
        const ep = episodes[index];
        const row = el("div", null, {class: "br-format"});
        row.append(el("strong", ep.title));
        if (reason) row.append(el("small", reason, {class: "br-muted"}));
        const tracks = entries.find(entry => entry.index === index).tracks;
        if (tracks.length) {
          const select = el("select", null, {"aria-label": ep.title + "档位", "data-episode-quality": index});
          select.add(new Option("跟随统一画质", ""));
          for (const entry of tierCatalogue(tracks)) {
            const tier = catalogue.find(item => item.key === entry.key) || entry;
            const text = batchTrackLabel(entry.track, tier, bitrateRange(entry.rates));
            select.add(new Option(text + " · " + sizeLabel(combinedSize([{track: entry.track}])), entry.key));
          }
          select.value = overrides.get(index) || "";
          select.onchange = () => {
            if (select.value) overrides.set(index, select.value);
            else overrides.delete(index);
            reflect();
          };
          row.append(select);
          if (track && track.video !== null && ep.audio.length > 1) {
            const audio = el("select", null, {"aria-label": ep.title + "音轨", "data-episode-quality": index + "-audio"});
            audio.add(new Option("自动选音轨", ""));
            for (const tier of tierCatalogue(tracks.filter(item => item.video === null))) {
              const item = tier.track;
              const label = batchTrackLabel(item, tier, bitrateRange(tier.rates));
              audio.add(new Option(label, String(item.audio)));
            }
            audio.value = String(audioOverrides.get(index) ?? "");
            audio.onchange = () => {
              if (audio.value === "") audioOverrides.delete(index);
              else audioOverrides.set(index, Number(audio.value));
              reflect();
            };
            row.append(audio);
          }
          if (track) row.append(el("small", batchTrackLabel(track, catalogue.find(item => item.key === trackTier(track)),
            bitrateRange([track.tbr])), {class: "br-muted"}));
        }
        formatList.append(row);
      }
    }
    if (focus !== undefined) formatList.querySelector(`[data-episode-quality="${focus}"]`)?.focus({preventScroll: true});
    formatList.scrollTop = scroll;
  }
  function sizeLabel(total) {
    const size = formatResourceSize(total.bytes);
    return total.complete ? (total.estimated ? "约 " : "") + size : size ? "已知 " + size : "体积待确认";
  }
  let validChoices = [];
  details.ontoggle = () => { if (details.open) reflect(); };
  if (initial) initial.episodes.forEach(({episode}) => choose(episode));
  else if (episodes[active]) choose(active);
  drawGrid();
  const selectedEpisodes = () => [...selected].map(episode => ({episode}));
  return {
    choices: () => validChoices,
    selected: selectedEpisodes,
    snapshot: () => ({episodes: selectedEpisodes(), overrides, audioOverrides, targetKey, allowLower}),
    refresh: reflect,
  };
}
