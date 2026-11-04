const test = require('node:test');
const assert = require('node:assert/strict');
require('../../../bili_remote/static/v2/workbench/api.js');
const json = (data, status = 200) => new Response(JSON.stringify(data), {status, headers: {'content-type': 'application/json'}});
test('V2 generation uses session, CSRF, no-store and exact schema without project writes', async () => {
  const calls = [];
  const api = new BiliWorkbenchAPI({csrf: () => 'test-csrf', fetch: async (path, options) => {calls.push({path, options}); return json({text: 'Title', context_revision: 3});}});
  const context = {context_id: 'test', revision: 3};
  await api.title(context, {}); await api.subtitle(context, {}); await api.naming(context, {});
  await api.apply({preview_token: 'token', context_revision: 3}, 'once');
  assert.deepEqual(calls.map(x => x.path), ['/api/v2/release-title/render', '/api/v2/subtitles/render', '/api/v2/naming/preview', '/api/v2/naming/apply']);
  for (const call of calls) {assert.equal(call.options.headers['X-CSRF-Token'], 'test-csrf'); assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.credentials, 'same-origin');}
  assert.deepEqual(JSON.parse(calls[3].options.body), {preview_token: 'token', expected_revision: 3, confirm: true, idempotency_key: 'once'});
});
test('only a missing aggregate falls back to the existing processing queue array', async () => {
  const api = new BiliWorkbenchAPI({fetch: async path => path.startsWith('/api/v2/') ? json({detail: 'Not Found'}, 404) : json([{id: 4, status: 'failed', task_type: 'naming_apply'}])});
  const [task] = await api.tasks(); assert.equal(task.kind, 'processing'); assert.equal(task.id, 4); assert.equal(task.retry_allowed, false);
  const failed = new BiliWorkbenchAPI({fetch: async () => json({detail: 'denied'}, 403)});
  await assert.rejects(() => failed.tasks(), error => error.status === 403);
});
test('unavailable and structured conflict errors preserve actual failure', async () => {
  const api = new BiliWorkbenchAPI({fetch: async () => json({detail: {code: 'stale_resource', message: '源文件已变化'}}, 409)});
  await assert.rejects(() => api.naming({}, {}), error => error.status === 409 && error.code === 'stale_resource' && error.message === '源文件已变化');
  const offline = new BiliWorkbenchAPI({fetch: async () => json({}, 404)});
  await assert.rejects(() => offline.title({}, {}), /尚未接入/);
});
