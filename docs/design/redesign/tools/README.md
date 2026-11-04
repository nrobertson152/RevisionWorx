# Local preview of the mockup

`shoot.js` renders the mockup in Chromium with the canvas's own runtime and checks the flows in `steps2.js`. Nothing here ships with the app.

Arguments: `node shoot.js <work folder> <output folder> <steps file>`. It expects these under the work folder:
- `design/project/`: the files from `../mockup/`
- `preview/site/support.js`: the canvas runtime, `artifact-type/dc-runtime.js`, read from the canvas artifact
- `preview/fonts/x_*/package/files/`: unpacked `@fontsource/ibm-plex-sans` and `@fontsource/ibm-plex-mono`
- `preview/thin/`: optional, the state files if they are not in `design/project/`

Run it with `NODE_PATH=$(npm root -g)` where Playwright is installed globally. Console messages about `<path> attribute d` come from the browser parsing template placeholders and are harmless.
