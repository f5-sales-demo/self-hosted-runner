import { cp, mkdir, readFile, readdir, unlink } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { validateInstallationToken } from './github-app.mjs';

const tokenPath = process.env.RENOVATE_TOKEN_FILE ?? '/token/installation-token';
async function seedEmptyDirectory(source, target) {
  await mkdir(target, { recursive: true });
  if ((await readdir(target)).length !== 0) {
    throw new Error(`Refusing to seed non-empty runtime directory: ${target}`);
  }
  for (const entry of await readdir(source)) {
    await cp(join(source, entry), join(target, entry), {
      recursive: true,
      force: false,
      errorOnExist: true,
    });
  }
}

await seedEmptyDirectory('/opt/f5-renovate/containerbase', '/tmp/containerbase');
await seedEmptyDirectory('/opt/f5-renovate/containerbase-runtime', '/opt/containerbase');
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
