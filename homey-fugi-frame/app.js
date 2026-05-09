'use strict';

const Homey = require('homey');

class FugiFrameApp extends Homey.App {
  async onInit() {
    this.log('Fugi Frame app initialized');

    this.homey.flow.getActionCard('skip_photo').registerRunListener(async ({ device }) => {
      await device.skipPhoto();
      return true;
    });
  }
}

module.exports = FugiFrameApp;
