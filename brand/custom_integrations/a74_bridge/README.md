# Brand assets for Home Assistant

Home Assistant shows integration icons from the central `home-assistant/brands` repository; custom integrations
cannot ship their own. To get rid of "icon not available":

1. Fork https://github.com/home-assistant/brands and add this folder as `custom_integrations/a74_bridge/`
   (files: `icon.png` 256×256, `icon@2x.png` 512×512, `logo.png`, `logo@2x.png`, transparent PNG).
2. Open a pull request; after it is merged the icon appears in every HA instance within a day (CDN cache).
