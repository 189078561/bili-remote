export function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== null) node.textContent = text;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function codecLabel(value) {
  if (/^avc/.test(value)) return "H.264";
  if (/^(hev|hvc)/.test(value)) return "H.265";
  if (/^av01/.test(value)) return "AV1";
  if (/^mp4a/.test(value)) return "AAC";
  return value.toUpperCase();
}

function formatLabel(track) {
  const bandwidth = track.bandwidth >= 1e6 ? (track.bandwidth / 1e6).toFixed(2) + " Mbps" : Math.round(track.bandwidth / 1000) + " kbps";
  return [track.height && track.height + "P", track.fps && track.fps + "fps", codecLabel(track.codec),
    track.height && ({125: "HDR10", 126: "Dolby Vision"}[track.quality] || ""), bandwidth].filter(Boolean).join(" · ");
}

function profile(track) {
  return [track.quality, codecLabel(track.codec), track.height, track.fps && track.fps >= 50].join("/");
}

export function mountPicker(root, episodes, {order = "asc", rows = 3, onSelect, onChange, active = -1, initial, queued = []} = {}) {
  const pageSize = 50;
  let descending = order === "desc";
  let page = descending ? Math.floor((episodes.length - 1) / pageSize) : 0;
  const selected = new Map();
  const grid = el("div", null, {class: "br-grid", "aria-label": "正片选集"});
  const summary = el("small", "", {"aria-live": "polite"});
  const ranges = el("div", null, {class: "br-ranges"});
  const unified = el("div", null, {class: "br-action"});
  const toolbar = el("div", null, {class: "br-action"});
  const formatList = el("div", null, {class: "br-formats"});
  const details = el("details", null);
  details.append(el("summary", "逐集选择视频与音轨"), formatList);
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
    if (indices.every(i => selected.has(i))) indices.forEach(i => selected.delete(i));
    else for (const i of indices) if (!selected.has(i)) choose(i);
    reflect();
  });
  toolbar.append(asc, desc, all, summary);
  box.append(el("strong", "选集与版本"), toolbar, ranges, grid, unified, details);
  root.replaceChildren(box);
  function range() {
    const first = page * pageSize;
    const ids = episodes.slice(first, first + pageSize).map((_, i) => i + first);
    return descending ? ids.reverse() : ids;
  }
  function choose(i) {
    selected.set(i, {episode: i, video: 0, audio: 0});
    onSelect?.(i);
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
        if (selected.has(i)) selected.delete(i);
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
    const pending = [...selected.values()].filter(pick => {
      const ep = episodes[pick.episode];
      return !ep.video?.[pick.video] || !ep.audio?.[pick.audio];
    }).length;
    summary.textContent = "已选 " + selected.size + " 项" + (pending ? " · " + pending + " 项待确认档位" : "");
    drawUnified();
    if (details.open) drawFormats();
    onChange?.(choices());
  }
  function drawUnified() {
    const picks = [...selected.values()];
    const focused = unified.contains(document.activeElement) && document.activeElement.dataset.key;
    unified.replaceChildren();
    if (!picks.length) return;
    for (const [key, title] of [["video", "统一画质"], ["audio", "统一音轨"]]) {
      const tracks = episodes[picks[0].episode][key] || [];
      const common = new Map();
      for (const track of tracks) {
        const id = profile(track);
        if (picks.every(pick => episodes[pick.episode][key]?.some(item => profile(item) === id))) common.set(id, track);
      }
      if (!common.size) continue;
      const select = el("select", null);
      select.dataset.key = key;
      select.add(new Option("保持各集选择", ""));
      for (const [id, track] of common) select.add(new Option(formatLabel(track), id));
      const first = episodes[picks[0].episode][key][picks[0][key]];
      if (first && picks.every(pick => {
        const track = episodes[pick.episode][key][pick[key]];
        return track && profile(track) === profile(first);
      })) select.value = profile(first);
      select.onchange = () => {
        if (!select.value) return;
        for (const pick of picks) pick[key] = episodes[pick.episode][key].findIndex(track => profile(track) === select.value);
        reflect();
      };
      const label = el("label", title);
      label.append(select);unified.append(label);
      if (focused === key) select.focus({preventScroll: true});
    }
  }

  function drawFormats() {
    const scroll = formatList.scrollTop;
    formatList.replaceChildren(...[...selected].map(([i, pick]) => {
      const ep = episodes[i];
      const row = el("div", null, {class: "br-format"});
      row.append(el("strong", ep.title));
      const controls = el("div", null, {class: "br-action"});
      for (const [key, text] of [["video", "视频档位"], ["audio", "音轨"]]) {
        const tracks = ep[key] || [];
        if (!tracks.length) continue;
        const select = el("select", null, {"aria-label": ep.title + text});
        for (const [index, track] of tracks.entries()) select.add(new Option(formatLabel(track), String(index)));
        select.value = String(pick[key]);
        select.onchange = () => {
          pick[key] = Number(select.value);
          drawUnified();
          onChange?.(choices());
        };
        const label = el("label", text);
        label.append(select);controls.append(label);
      }
      row.append(controls);
      if (!ep.video?.length || !ep.audio?.length) row.append(el("small", ep.error || "正在确认可用档位…"));
      return row;
    }));
    formatList.scrollTop = scroll;
  }
  function choices() {
    return [...selected.values()].filter(pick => !queued.some(entry =>
      entry[0] === pick.episode && entry[1] === pick.video && entry[2] === pick.audio));
  }
  details.ontoggle = () => { if (details.open) drawFormats(); };
  if (initial) initial.forEach(pick => selected.set(pick.episode, pick));
  else if (episodes[active]) choose(active);
  drawGrid();
  return {choices, selected: () => [...selected.values()], refresh: reflect};
}
