```bash
make test-offline   # no device needed: capabilities, screen objects and settings
```

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`.{% if docker %} Or, with only Docker:
`docker compose run --rm tests` (offline tier only; the container has no emulator).{% endif %}
Expected runtime: a few seconds.

The live tests need an Android emulator or device and an Appium 3 server; they skip
themselves until `APP_APPIUM_URL` is set. Against the demo app
([webdriverio/native-demo-app](https://github.com/webdriverio/native-demo-app), MIT):

```bash
npm install --global appium@3 && appium driver install uiautomator2
make doctor                 # Android SDK, Java and driver preflight
make app                    # downloads the APK, pinned by release and SHA-256
make appium                 # in a second terminal; start an emulator from Android Studio first
APP_APPIUM_URL=http://127.0.0.1:4723 make live
```

`make live` runs one test at a time, because one device runs one session. Under
`make test` (parallel) the live tests skip and say so.

Every setting: [.env.example](.env.example).
