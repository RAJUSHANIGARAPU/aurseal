# Apps under test

Binaries are downloaded here, never committed (see `.gitignore`).

`make app` fetches the Android build of
[webdriverio/native-demo-app](https://github.com/webdriverio/native-demo-app) (MIT),
release `v2.2.0`, and refuses it unless its SHA-256 matches the one pinned in the
`Makefile`. To test another app, put its APK here and set `APP_APP_PATH`; pin its
checksum the same way if a script downloads it.
