/* Optional standalone host. Uses the existing login session and shared FileBrowser. */
(function (global) {
  "use strict";
  const status = document.querySelector('[data-entry-status]');
  const api = new global.BiliWorkbenchAPI();
  global.element = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = String(text);
    return element;
  };
  global.formatBytes = value => {
    const bytes = Number(value);
    if (!Number.isFinite(bytes) || bytes < 0) return "未知";
    if (bytes === 0) return "0 B";
    const index = Math.min(3, Math.floor(Math.log(bytes) / Math.log(1024)));
    return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${["B", "KiB", "MiB", "GiB"][index]}`;
  };
  global.api = (path, options = {}) => api.request(path, options.body ? JSON.parse(options.body) : undefined, options.method || "GET");
  const dialog = document.querySelector('[data-entry-dialog]');
  global.closeModal = () => dialog.close();
  global.showModal = content => {
    const host = dialog.querySelector('[data-entry-dialog-content]'); host.replaceChildren(content);
    if (!dialog.open) dialog.showModal();
  };
  dialog.querySelector('button').addEventListener("click", global.closeModal);
  (async () => {
    try {
      const session = await api.request("/api/auth/session");
      document.querySelector('meta[name="csrf-token"]').content = session.csrf;
      document.querySelector('[data-workbench]').hidden = false;
      global.workbench = await global.BiliWorkbench.mount(document.querySelector('[data-workbench]'), {api});
      status.hidden = true;
    } catch (error) {
      status.replaceChildren(document.createTextNode(error.status === 401 ? "请先登录 Bili Remote，再打开工作台。" : error.message));
      const login = global.element("a", null, "返回 Bili Remote"); login.href = "/"; status.append(document.createTextNode(" "), login);
    }
  })();
  global.addEventListener("pagehide", () => global.workbench?.destroy());
})(window);
