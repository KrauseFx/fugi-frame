'use strict';

const Homey = require('homey');
const { FugiClient } = require('../../lib/fugi-client');

class FugiFrameDevice extends Homey.Device {
  async onInit() {
    this.log('Fugi Frame device initialized');
    this.registerCapabilityListener('onoff', this.onCapabilityOnoff.bind(this));
    this.registerCapabilityListener('dim', this.onCapabilityDim.bind(this));
    await this.refreshState().catch(error => this.error('Initial refresh failed', error));
  }

  getClient() {
    const settings = this.getSettings();
    return new FugiClient({
      baseUrl: settings.base_url,
      token: settings.token,
    });
  }

  async onCapabilityOnoff(value) {
    const result = await this.getClient().setDisplay(Boolean(value));
    await this.setCapabilityValue('onoff', Boolean(result.on));
  }

  async onCapabilityDim(value) {
    const result = await this.getClient().setBrightness(Number(value));
    await this.setCapabilityValue('dim', Number(result.brightness));
  }

  async skipPhoto() {
    await this.getClient().skip();
  }

  async refreshState() {
    const client = this.getClient();
    const [display, brightness] = await Promise.all([
      client.getDisplay(),
      client.getBrightness(),
    ]);
    await this.setCapabilityValue('onoff', Boolean(display.on));
    await this.setCapabilityValue('dim', Number(brightness.brightness));
  }

  async onSettings({ newSettings, changedKeys }) {
    if (changedKeys.includes('base_url') || changedKeys.includes('token')) {
      if (!newSettings.token) {
        throw new Error('Missing Fugi Frame control token');
      }
      const client = new FugiClient({
        baseUrl: newSettings.base_url,
        token: newSettings.token,
      });
      await client.status();
      await Promise.all([client.getDisplay(), client.getBrightness()]);
    }
  }
}

module.exports = FugiFrameDevice;
