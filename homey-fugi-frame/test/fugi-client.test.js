const assert = require('node:assert/strict');
const test = require('node:test');
const { FugiClient } = require('../lib/fugi-client');

test('sets display state through Fugi control API', async () => {
  const calls = [];
  const client = new FugiClient({ baseUrl: 'http://frame.local:8767', token: 'test', fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ ok: true, on: true }) };
  }});

  const result = await client.setDisplay(true);

  assert.deepEqual(result, { ok: true, on: true });
  assert.equal(calls[0].url, 'http://frame.local:8767/api/frameo/display');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.headers['X-Fugi-Frame-Token'], 'test');
  assert.equal(calls[0].options.body, JSON.stringify({ on: true }));
});

test('sets brightness as a 0..1 Homey dim value', async () => {
  const calls = [];
  const client = new FugiClient({ baseUrl: 'http://frame.local:8767/', token: 'test', fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ ok: true, brightness: 0.42, raw: 107 }) };
  }});

  const result = await client.setBrightness(0.42);

  assert.deepEqual(result, { ok: true, brightness: 0.42, raw: 107 });
  assert.equal(calls[0].url, 'http://frame.local:8767/api/frameo/brightness');
  assert.equal(calls[0].options.body, JSON.stringify({ brightness: 0.42 }));
});

test('rejects brightness values outside Homey dim range', async () => {
  const client = new FugiClient({ baseUrl: 'http://frame.local:8767', token: 'test', fetchImpl: async () => { throw new Error('should not fetch'); } });

  await assert.rejects(() => client.setBrightness(1.2), /range 0\.\.1/);
});

test('skips current photo with authenticated POST', async () => {
  const calls = [];
  const client = new FugiClient({ baseUrl: 'http://frame.local:8767', token: 'test', fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ ok: true, reason: 'manual_skip' }) };
  }});

  const result = await client.skip();

  assert.equal(result.reason, 'manual_skip');
  assert.equal(calls[0].url, 'http://frame.local:8767/api/frameo/skip');
  assert.equal(calls[0].options.method, 'POST');
});
