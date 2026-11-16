/* Optional standalone host. Uses the existing login session. */
(function (global) {
  "use strict";
  const status = document.querySelector('[data-entry-status]');
  const api = new global.BiliWorkbenchAPI();
  (async () => {
    try {
      const session = await api.request("/api/auth/session");
      document.querySelector('meta[name="csrf-token"]').content = session.csrf;
      document.querySelector('[data-workbench]').hidden = false;
      global.workbench = await global.BiliWorkbench.mount(document.querySelector('[data-workbench]'), {api});
      status.hidden = true;
    } catch (error) {
      status.replaceChildren(document.createTextNode(error.status === 401 ? "请先登录 Bili Remote，再打开工作台。" : error.message));
      const login = document.createElement("a"); login.textContent = "返回 Bili Remote"; login.href = "/"; status.append(document.createTextNode(" "), login);
    }
  })();
  global.addEventListener("pagehide", () => global.workbench?.destroy());
})(window);
