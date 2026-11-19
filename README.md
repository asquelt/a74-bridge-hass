# A74 Bridge for Home Assistant

Home Assistant integration for `a74-bridged`: a Raspberry Pi bridge that controls one Sony A7 IV (ILCE-7M4)
over the Sony Camera Remote SDK and exposes it as a small REST API. One config entry per bridge/camera.
Since 0.2.0 the integration can also control a **RØDECaster Video** console (OSC over TCP 10024, protocol as
reverse-engineered by [rodecaster-video-linux](https://github.com/miquelbaixas/rodecaster-video-linux) and used by the
[Companion module](https://github.com/bitfocus/companion-module-rode-rcv)): record switch, HDMI input and
recording state, program scene, disk space. `a74_bridge.record` then starts or stops the cameras and the console together.

This repository holds only the Home Assistant side (pure Python, no Sony code). The bridge daemon, the
settings-file tooling and the design notes live in a separate, private repository.

## Install (Home Assistant Marketplace)

1. In the Home Assistant Marketplace (the successor of HACS in current releases; on older versions use HACS the same way)
   add `https://github.com/asquelt/a74-bridge-hass` as a custom repository, category *Integration*.
2. Install **A74 Bridge (Sony A7 IV)**, restart Home Assistant. Updates arrive the same way; new code is loaded only after a restart.
3. Settings → Devices & services → *Add integration* → **A74 Bridge** → host of the bridge, port (8080) and the token
   from `/etc/a74-bridge/bridge.env` on the Pi.

Releases: every `vX.Y.Z` tag pushed to the source repository is mirrored here and turned into a GitHub Release by the `release` workflow.

Brand icon/logo ship inside the integration (`custom_components/a74_bridge/brand/`, Home Assistant ≥ 2026.3 serves them locally).

Install and update only through the Marketplace. Copying files into `config/custom_components/` by hand leaves the
Marketplace's record of the installed version out of step with what is on disk.

## What you get per camera

- Device named after the bridge host, identified by the camera serial, firmware as sw version.
- Binary sensors: SDK session, recording, USB in Remote Shooting mode, preset load in progress, clock OK, overheating.
- Sensors: USB mode, firmware, battery level, power source, active preset, last load job, camera clock offset,
  Pi temperature, USB disconnect counter, bridge uptime.
- Buttons: sync camera clock, restart the SDK session. Switch: recording (manual use from the panel only; the studio rule is that automations never start or stop recording).
- Services (`a74_bridge.*`): `preset_save`, `preset_load` (settings files kept in `/share/camera_presets/<preset>/<camera>.DAT`,
  load waits for the camera to reboot and verifies), `clock_sync`, `set_properties` (raw SDK values), `record`.
- Studio hub (add it once from the integration menu): preset name text, preset list select, buttons Save / Load / Delete preset,
  Sync parameters / Sync full settings from the source camera (default `a74-bridge-1`, changeable in the entry options), last-action sensor.
  Presets live in `/share/camera_presets/<name>/` on the HA host (covered by HA backups when the `share` folder is included).
- Recording upload (bridge ≥ 0.4.1 with `A74_MEDIA_REMOTE` set): services `media_list`, `media_upload`, `media_job`, `media_cancel`
  for the camera cards and `console_media_list`, `console_media_upload`, `console_media_job`, `console_media_cancel` for the
  console's ISO disk (through the one bridge that has `A74_CONSOLE_SSH`). Per camera: *Card upload* (job state, attributes with
  bytes and error), *Card upload progress* (%), *Card upload running*; the console bridge also gets the three *Console ISO upload* entities.
  Listing cards and uploading take the camera out of Remote mode, so both are refused while the studio guard entity is on or a
  camera records, and turning the guard on cancels running uploads. `media_upload` waits up to 30 s for a camera that is still
  returning from a card listing.
  `a74_bridge.record` with `action: start` first cancels running card uploads and waits (up to 3 min) for those cameras to
  return, then starts everything together; a stop that arrives meanwhile aborts the pending start.
- Deleting from the sources (bridge ≥ 0.5.0): `media_delete` (one camera, `files: [{name, size}]`) and `console_media_delete`
  (`files: [{path, size}]`). Only the named files go, and only when the size matches too; the bridge lists the source again and
  answers per file `deleted`, `missing` (already gone), `mismatch` or `failed`. On the console, capture folders left empty are
  removed. A card delete takes the camera out of Remote mode for a few seconds, so the same guards apply as for uploads.
  Nothing formats a card.
- Events: `a74_bridge_preset_applied`, `a74_bridge_preset_failed`, `a74_bridge_camera_disconnected`, `a74_bridge_camera_power_loss`,
  `a74_bridge_media_job_finished` (`camera_id`, `source` = `camera` or `console`, `job`).

The bridge polls `GET /status` every 10 s. Preset load reboots the camera and interrupts HDMI for about 11 s,
so it is meant to run before a session, never during one.
