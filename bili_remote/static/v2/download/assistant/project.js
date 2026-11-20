import {mountPicker} from "./picker.js";

export function mountAssistant(root, api, refreshJobs) {
  const find = selector => root.querySelector(selector);
  const pickerHost = find("[data-assistant-picker]");
  const list = find("[data-assistant-lists]");
  const status = find("[data-assistant-status]");
  const submit = find("[data-assistant-download]");
  const refreshButton = find("[data-assistant-refresh]");
  let picker;
  let queued;
  let revision = 0;
  let submitting = false;
  function syncButton(picks = picker?.choices() || []) {
    submit.disabled = submitting || !picks.length || !picks.every(pick =>
      pickerEpisodes[pick.episode].video[pick.video] && pickerEpisodes[pick.episode].audio[pick.audio]);
    submit.textContent = "开始下载" + (picks.length ? " · " + picks.length + " 项" : "");
  }
  let pickerEpisodes = [];
  async function openList() {
    const token = ++revision;
    const id = list.value;
    picker = null;pickerEpisodes = [];
    pickerHost.replaceChildren();
    syncButton();
    if (!id) return;
    try {
      const data = await api("/api/parse-assistant/imports/" + encodeURIComponent(id));
      if (token !== revision) return;
      pickerEpisodes = data.episodes;
      queued = data.queued;
      picker = mountPicker(pickerHost, data.episodes, {onChange: syncButton, queued});
      status.textContent = data.title + " · 本机 Web";
    } catch (error) {
      if (token === revision) status.textContent = error.message;
    }
  }
  async function refresh() {
    if (submitting || refreshButton.disabled) return;
    refreshButton.disabled = true;
    try {
      const previous = list.value;
      const result = await api("/api/parse-assistant");
      list.replaceChildren(...result.imports.map(item => new Option(item.title + " · " + item.count + " 项", item.id)));
      if (!result.imports.length) list.add(new Option("等待扩展推送正片列表", ""));
      if (result.imports.some(i => i.id === previous)) list.value = previous;
      if (!picker || list.value !== previous) await openList();
    } catch (error) { status.textContent = error.message; }
    finally { refreshButton.disabled = false; }
  }
  list.onchange = openList;
  refreshButton.onclick = refresh;
  root.ontoggle = () => {if (root.open) refresh();};
  submit.onclick = async () => {
    if (!picker || submitting) return;
    const choices = picker.choices();
    submitting = true;list.disabled = true;
    syncButton();
    try {
      const result = await api("/api/parse-assistant/imports/" + encodeURIComponent(list.value) + "/jobs", {
        method: "POST", body: JSON.stringify({choices}),
      });
      queued.push(...choices.map(({episode, video, audio}) => [episode, video, audio]));
      picker.refresh();
      status.textContent = "已加入 " + result.ids.length + " 个下载任务。";
      await refreshJobs({force: true});
    } catch (error) { status.textContent = error.message; }
    finally { submitting = false;list.disabled = false;syncButton(); }
  };
  if (root.open) refresh();
}
