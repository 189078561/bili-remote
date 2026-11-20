import {sourceName, tracksFor} from "./picker.js";

export function importResolution(key, data) {
  const source = data.source === "tv" ? "tv" : "web";
  const active = data.episodes[data.active_episode ?? 0] ? data.active_episode ?? 0 : 0;
  const episodes = data.episodes.map((episode, index) => {
    const part = new URL(episode.url).searchParams.has("p");
    return {
      ...episode, id: String(index), group: "正片", part,
      display_episode_number: episode.number, group_index: index + 1,
    };
  });
  return {
    assistant: {key, queued: new Set((data.queued || []).map(pick => JSON.stringify(pick))), tracksFor},
    title: data.title, api_mode: source, source_url: episodes[active].url,
    episodes, default_episode_id: String(active), playlist_count: episodes.length,
    duration: episodes.length === 1 ? episodes[0].duration : null,
    naming_items: data.naming_items || {},
  };
}

export function mountAssistant(root, api, openImport, initialKey) {
  const list = root.querySelector("[data-assistant-lists]");
  const status = root.querySelector("[data-assistant-status]");
  const use = root.querySelector("[data-assistant-use]");
  const refreshButton = root.querySelector("[data-assistant-refresh]");

  async function open(key) {
    if (!key || use.disabled) return;
    use.disabled = true;
    try {
      status.textContent = "";
      await openImport(key);
    } catch (error) {
      status.textContent = error.message;
      root.open = true;
    } finally {
      use.disabled = !list.value;
    }
  }

  async function refresh() {
    if (refreshButton.disabled) return;
    refreshButton.disabled = true;
    try {
      const previous = list.value;
      const {imports} = await api("/api/parse-assistant");
      list.replaceChildren(...imports.map(item => new Option(
        item.title + " · " + sourceName(item.source) + " · " + item.count + " 项", item.id
      )));
      if (!imports.length) list.add(new Option("等待扩展推送正片列表", ""));
      list.value = imports.some(item => item.id === previous) ? previous : imports[0]?.id || "";
      use.disabled = !list.value;
    } catch (error) {
      status.textContent = error.message;
    } finally {
      refreshButton.disabled = false;
    }
  }

  list.onchange = () => { use.disabled = !list.value; };
  refreshButton.onclick = refresh;
  root.ontoggle = () => { if (root.open) void refresh(); };
  use.onclick = () => open(list.value);
  if (initialKey) {
    use.disabled = false;
    void open(initialKey);
  } else if (root.open) void refresh();
}
