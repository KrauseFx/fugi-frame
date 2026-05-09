'use strict';

const Homey = require('homey');
const { FugiClient } = require('../../lib/fugi-client');

const DEFAULT_BASE_URL = '';

class FugiFrameDriver extends Homey.Driver {
  async onInit() {
    this.log('Fugi Frame driver initialized');
  }

  async onPair(session) {
    let pairingConfig = {
      base_url: DEFAULT_BASE_URL,
      token: '',
    };

    session.setHandler('test_connection', async (data) => {
      const baseUrl = String(data?.base_url || DEFAULT_BASE_URL).trim().replace(/\/+$/, '');
      if (!baseUrl) {
        throw new Error('Missing Fugi Frame base URL');
      }
      const token = String(data?.token || '');
      if (!token) {
        throw new Error('Missing Fugi Frame control token');
      }
      pairingConfig = {
        base_url: baseUrl,
        token,
      };
      const client = new FugiClient({ baseUrl: pairingConfig.base_url, token: pairingConfig.token });
      await client.status();
      await Promise.all([client.getDisplay(), client.getBrightness()]);
      return { ok: true };
    });

    session.setHandler('list_devices', async () => {
      return [
        {
          name: 'Pexar Frame',
          data: {
            id: 'pexar-frameo-fugi-frame',
          },
          settings: pairingConfig,
        },
      ];
    });
  }
}

module.exports = FugiFrameDriver;
