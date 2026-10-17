# Shared full frontend checks

The full desktop/browser app serves `web/desktop/` through `/ui/`. The lightweight MCP app retains the classic resources in `web/`. These checks exercise the rebuilt full UI; existing `scripts/*_ui.cjs` checks continue covering the classic MCP components.

Node is a development dependency only. From a source checkout with Node 24:

```sh
npm ci --ignore-scripts
npm test
npx playwright-core install chromium
npm run test:browser
```

On Windows the browser check uses installed Microsoft Edge. On Linux/macOS it uses Playwright Chromium; `BROWSER_EXECUTABLE` can select an existing Chromium binary.

`npm test` checks loaded modules, all eleven routes, API writes, stale asynchronous mounts, permission dependencies, platform-specific SnowLuma flows and VNC interaction with JSDOM. The real browser check covers layout, sidebar collapse/reload/mobile navigation, both logo themes, CSP and native-browser VNC callbacks.

All API responses, accounts, tokens and model callbacks in these tests are synthetic. They do not connect to QQ, send messages, invoke paid models or validate Windows WebView2/Cocoa integration. Native Intel Mac verification and remaining limits are recorded in `macos/ACCEPTANCE.md`.
