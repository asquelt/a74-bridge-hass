# A74 Bridge for Home Assistant

Home Assistant integration for `a74-bridged`: a Raspberry Pi bridge that controls one Sony A7 IV (ILCE-7M4)
over the Sony Camera Remote SDK and exposes it as a small REST API. One config entry per bridge/camera.

This repository holds only the Home Assistant side (pure Python, no Sony code). The bridge daemon, the
settings-file tooling and the design notes live in a separate, private repository.

## Install (HACS)

1. HACS → Integrations → ⋮ → *Custom repositories* → add `https://github.com/asquelt/a74-bridge-hass`, category *Integration*.
2. Download **A74 Bridge (Sony A7 IV)**, restart Home Assistant.
3. Settings → Devices & services → *Add integration* → **A74 Bridge** → host of the bridge, port (8080) and the token
   from `/etc/a74-bridge/bridge.env` on the Pi.

Without HACS: copy `custom_components/a74_bridge` into your `config/custom_components/` (see `install.sh`).

## What you get per camera

- Device named after the bridge host, identified by the camera serial, firmware as sw version.
- Binary sensors: SDK session, recording, USB in Remote Shooting mode, preset load in progress, clock OK, overheating.
- Sensors: USB mode, firmware, battery level, power source, active preset, last load job, camera clock offset,
  Pi temperature, USB disconnect counter, bridge uptime.
- Buttons: sync camera clock, restart the SDK session.
- Services (`a74_bridge.*`): `preset_save`, `preset_load` (settings files kept in `/share/camera_presets/<preset>/<camera>.DAT`,
  load waits for the camera to reboot and verifies), `clock_sync`, `set_properties` (raw SDK values), `record`.
- Events: `a74_bridge_preset_applied`, `a74_bridge_preset_failed`, `a74_bridge_camera_disconnected`, `a74_bridge_camera_power_loss`.

The bridge polls `GET /status` every 10 s. Preset load reboots the camera and interrupts HDMI for about 11 s,
so it is meant to run before a session, never during one.
