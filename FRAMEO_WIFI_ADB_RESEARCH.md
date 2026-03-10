# Persistent Wi-Fi ADB on the Pexar Frameo Frame

## Summary

Standard `adb tcpip 5555` is not persistent across reboot because it sets the non-persistent Android property `service.adb.tcp.port`.

The researched workaround is to set the persistent property instead:

```bash
adb shell setprop persist.adb.tcp.port 5555
```

After a reboot, `adbd` should read `persist.adb.tcp.port` and start listening on TCP automatically, which should remove the need to reconnect USB after every restart.

Sleep is not expected to matter because Frameo sleep only dims the display rather than rebooting the device.

## Relevant Findings

- `adb tcpip 5555` is ephemeral and does not survive reboot.
- `persist.adb.tcp.port=5555` should survive reboot because it uses Android's persistent property store.
- Community reports indicate this has worked on Frameo devices.
- The Pexar by Lexar 11-inch 2K frame reportedly exposes root over ADB, which is important because setting `persist.*` properties generally requires elevated privileges.
- Auto-updates may still reset or interfere with this setup, so router-level WAN blocking during setup may be prudent.

## Practical Next Steps

1. While USB ADB is available, run:
   ```bash
   adb shell setprop persist.adb.tcp.port 5555
   ```
2. Reboot the frame.
3. Verify Wi-Fi ADB comes back automatically:
   ```bash
   adb connect 192.168.4.26:5555
   ```
4. If that works, the cable should no longer be needed after reboot.

## Notes

- If the persist-property approach fails on this specific firmware, fallback options from the research include a permanently attached USB host, Magisk boot scripts, or Home Assistant automation.
- This note records external research and should be treated as a working operational hypothesis until verified on this exact device.

## Source

- Claude research, March 10, 2026
