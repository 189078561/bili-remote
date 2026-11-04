/* Transport only. Business generation belongs to the V2 services. */
(function (global) {
  "use strict";
  class WorkbenchAPI {
    constructor({fetch: transport = global.fetch.bind(global), csrf = () => global.document?.querySelector('meta[name="csrf-token"]')?.content || ""} = {}) {
      this.transport = transport;
      this.csrf = csrf;
    }
    async request(path, body, method = body === undefined ? "GET" : "POST") {
      const headers = {Accept: "application/json", "Cache-Control": "no-store"};
      if (method !== "GET") {
        headers["X-CSRF-Token"] = this.csrf();
        headers["Content-Type"] = "application/json";
      }
      const response = await this.transport(path, {method, headers, credentials: "same-origin", cache: "no-store", ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      const data = (response.headers.get("content-type") || "").includes("application/json") ? await response.json() : null;
      if (!response.ok) {
        const detail = data?.detail;
        let message = typeof detail === "string" ? detail : detail?.message;
        if (Array.isArray(detail)) message = detail.map(x => x.msg).join("；");
        if (response.status === 401) message = "登录已失效，请重新登录后重试";
        if (response.status === 403) message = message || "权限或会话校验失败，请刷新登录状态";
        if (response.status === 404 && path.startsWith("/api/v2/")) message = "此能力尚未接入服务，请等待后端接口就绪";
        const error = new Error(message || `请求失败（${response.status}）`);
        error.status = response.status;
        error.code = detail?.code;
        throw error;
      }
      if (data === null) throw new Error("服务未返回有效的 JSON 数据");
      return data;
    }
    resolve(body) { return this.request("/api/v2/resources/resolve", body); }
    probe(body) { return this.request("/api/v2/resources/probe", body); }
    search(query, media_type) { return this.request("/api/v2/metadata/search?" + new URLSearchParams({query, media_type, providers: "tmdb,bangumi", page: "1"})); }
    naming(context, rules) { return this.request("/api/v2/naming/preview", {context, overrides: {file_values: {}, episode_overrides: {}, manual_names: {}}, rules}); }
    apply(preview, key) { return this.request("/api/v2/naming/apply", {preview_token: preview.preview_token, expected_revision: preview.context_revision, confirm: true, idempotency_key: key}); }
    title(context, rule) { return this.request("/api/v2/release-title/render", {context, rule}); }
    subtitle(context, rule) { return this.request("/api/v2/subtitles/render", {context, rule}); }
    compose(body) { return this.request("/api/v2/publishing/preview", body); }
    async tasks() {
      if (this.tasksMode === "legacy") return (await this.request("/api/tasks?limit=100")).map(task => this.legacyTask(task));
      try { const result = await this.request("/api/v2/tasks?limit=100&offset=0"); this.tasksMode = "v2"; return result.items; }
      catch (error) {
        if (error.status !== 404) throw error;
        // Existing processing queue remains usable before the aggregate is registered.
        this.tasksMode = "legacy";
        return (await this.request("/api/tasks?limit=100")).map(task => this.legacyTask(task));
      }
    }
    legacyTask(task) {
      const terminal = ["completed", "failed", "cancelled"].includes(task.status);
      return {...task, kind: "processing", progress: typeof task.progress === "number" ? task.progress : null,
        cancel_allowed: !terminal, retry_allowed: ["failed", "cancelled"].includes(task.status) && ["screenshot", "screenshot_collage", "image_upload", "torrent_build"].includes(task.task_type), legacy: true};
    }
    async taskAction(task, action) {
      const path = task.legacy ? `/api/tasks/${task.id}/${action}` : `/api/v2/tasks/${task.kind}/${task.id}/${action}`;
      const result = await this.request(path, {});
      return task.legacy ? this.legacyTask(result.task) : result.task;
    }
  }
  global.BiliWorkbenchAPI = WorkbenchAPI;
})(typeof window === "undefined" ? globalThis : window);
