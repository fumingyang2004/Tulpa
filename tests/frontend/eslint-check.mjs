import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Linter } from 'eslint';
import globals from 'globals';

const root = path.resolve(fileURLToPath(new URL('../../', import.meta.url)));
const web = path.join(root, 'web', 'desktop');
const files = ['app.js', 'live.js', 'desktop.js', 'chat.js', 'reply.js', 'group-admin.js', 'watch.js', 'data.js', 'artifacts.js', 'voice.js', 'collections.js', 'workspaces.js', 'snowluma.js', 'snowluma-macos.js', 'tulpa.js', 'mcp.js'];
const linter = new Linter();
let failed = false;
for (const file of files) {
  const code = fs.readFileSync(path.join(web, file), 'utf8');
  const messages = linter.verify(code, [{
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.es2021 },
    },
    rules: {
      'no-undef': 'error',
      'no-redeclare': 'error',
      'no-dupe-keys': 'error',
      'no-unreachable': 'error',
      'no-constant-condition': 'error',
      'no-fallthrough': 'error',
    },
  }], { filename: file });
  if (messages.length) {
    failed = true;
    console.log('--- ' + file);
    for (const message of messages) console.log(`${message.line}:${message.column} ${message.ruleId} ${message.message}`);
  }
}
console.log(failed ? 'ESLint found issues' : 'ESLint clean');
process.exit(failed ? 1 : 0);
