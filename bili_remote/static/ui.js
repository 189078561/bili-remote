/* Shared shell and file-tool dialogs. Existing tool DOM and event bindings are reused. */
(function () {
  "use strict";
  let returnContext = null;
  const aliases = {uploads:"uploads", torrents:"torrents", "release-info":"release-info"};
  function openTool(kind) {
    if (kind === "screenshots") return window.CollageTool.openRecent();
    const content = document.getElementById(`${aliases[kind] || kind}-view`);
    if (!content) return;
    // Restore the previous tool before replacing the dialog, including stacked selectors.
    if (document.querySelector("#modal .file-tool-dialog")) closeModal();
    const panel = document.createElement("div");
    panel.className = "file-tool-dialog";
    const dock = document.getElementById("file-tool-dock");
    content.classList.remove("hidden");
    panel.append(content);
    showModal(panel);
    const restore = event => {
      if (event.detail?.panel !== panel) return;
      content.classList.add("hidden");
      dock.append(content);
      document.removeEventListener("app:modal-close", restore);
    };
    document.addEventListener("app:modal-close", restore);
    const settings = document.createElement("button");
    settings.type = "button";
    settings.className = "button ghost small file-tool-settings";
    settings.textContent = "前往设置";
    settings.addEventListener("click", () => {returnContext={kind}; closeModal(); openSettingsPage();});
    panel.append(settings);
    document.dispatchEvent(new CustomEvent("file-tool:open", {detail:{kind}}));
  }
  window.FileTools = {open:openTool,returnTo:context=>returnContext=context};
  document.addEventListener("click", event => {
    const tool = event.target.closest("[data-file-tool]");
    if (tool) { document.querySelectorAll(".file-history-menu").forEach(n=>n.open=false); openTool(tool.dataset.fileTool); }
    if (event.target.closest("[data-open-settings]")) openSettingsPage();
  });
  document.addEventListener("app:view", event => {
    if(returnContext && event.detail.view !== "settings") {
      const context=returnContext; returnContext=null;
      if(event.detail.view === "files") setTimeout(()=>{
        if(context.kind === "screenshots" && context.sessionId) CollageTool.openSession(context.sessionId);
        else openTool(context.kind);
      },0);
    }
  });
})();
