'use strict';

class FugiClient {
  constructor({ baseUrl, token, fetchImpl } = {}) {
    if (!baseUrl) throw new Error('Missing Fugi Frame base URL');
    this.baseUrl = String(baseUrl).replace(/\/+$/, '');
    this.token = token || '';
    this.fetch = fetchImpl || global.fetch;
    if (typeof this.fetch !== 'function') throw new Error('Missing fetch implementation');
  }

  async status() {
    return this.request('/api/frameo/status', { method: 'GET', auth: false });
  }

  async getDisplay() {
    return this.request('/api/frameo/display', { method: 'GET' });
  }

  async setDisplay(on) {
    return this.request('/api/frameo/display', {
      method: 'POST',
      body: { on: Boolean(on) },
    });
  }

  async getBrightness() {
    return this.request('/api/frameo/brightness', { method: 'GET' });
  }

  async setBrightness(brightness) {
    const value = Number(brightness);
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      throw new Error('Brightness must be in range 0..1');
    }
    return this.request('/api/frameo/brightness', {
      method: 'POST',
      body: { brightness: value },
    });
  }

  async skip() {
    return this.request('/api/frameo/skip', { method: 'POST' });
  }

  async request(path, { method = 'GET', body, auth = true } = {}) {
    const headers = {};
    if (auth) headers['X-Fugi-Frame-Token'] = this.token;
    if (body !== undefined) headers['Content-Type'] = 'application/json';

    const response = await this.fetch(`${this.baseUrl}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    const text = await response.text?.();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch (error) {
        payload = { raw: text };
      }
    } else if (typeof response.json === 'function') {
      payload = await response.json();
    }

    if (!response.ok) {
      const detail = payload?.detail || payload?.raw || response.statusText || 'Request failed';
      throw new Error(`Fugi Frame API ${method} ${path} failed (${response.status}): ${detail}`);
    }
    return payload || { ok: true };
  }
}

module.exports = { FugiClient };
