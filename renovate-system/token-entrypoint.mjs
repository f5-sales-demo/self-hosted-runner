import { readFile, unlink } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { validateInstallationToken } from './github-app.mjs';

const tokenPath = process.env.RENOVATE_TOKEN_FILE ?? '/token/installation-token';
const token = await readFile(tokenPath, 'utf8');
await unlink(tokenPath);
validateInstallationToken(token);
const childEnv = { ...process.env, RENOVATE_TOKEN: token };
delete childEnv.RENOVATE_TOKEN_FILE;
delete childEnv.RENOVATE_GITHUB_APP_KEY_PATH;
const child = spawn('renovate', process.argv.slice(2), { stdio: 'inherit', env: childEnv });
for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(signal, () => child.kill(signal));
child.on('error', (error) => { throw error; });
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 1);
});
