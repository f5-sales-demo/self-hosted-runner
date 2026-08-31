#!/usr/bin/env node
import { readFile, stat, writeFile, chmod } from 'node:fs/promises';
import { appJwt, positiveId, validateAppMetadata, validateBot, validateDeadline, validateExpiry, validateInstallation, validateObservedScope, validateRepositoryNames } from './github-app.mjs';

const env = process.env;
const appId = positiveId(env.RENOVATE_GITHUB_APP_ID, 'RENOVATE_GITHUB_APP_ID');
const installationId = positiveId(env.RENOVATE_GITHUB_INSTALLATION_ID, 'RENOVATE_GITHUB_INSTALLATION_ID');
const botId = positiveId(env.RENOVATE_GITHUB_APP_BOT_ID, 'RENOVATE_GITHUB_APP_BOT_ID');
const botLogin = env.RENOVATE_GITHUB_APP_BOT_LOGIN;
if (!/^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\[bot\]$/i.test(botLogin ?? '')) throw new Error('RENOVATE_GITHUB_APP_BOT_LOGIN must be an exact GitHub App bot login');
const { deadline, margin } = validateDeadline(env.RENOVATE_JOB_DEADLINE_SECONDS ?? '2700', env.RENOVATE_TOKEN_SAFETY_MARGIN_SECONDS ?? '120');
const keyPath = env.RENOVATE_GITHUB_APP_KEY_PATH ?? '/github-app/private-key.pem';
const tokenPath = env.RENOVATE_TOKEN_FILE ?? '/token/installation-token';
const repositoriesPath = env.RENOVATE_REPOSITORIES_FILE ?? '/config/repositories.json';
const keyStat = await stat(keyPath);
if (!keyStat.isFile() || (keyStat.mode & 0o777) !== 0o440) throw new Error('private key must be a regular file with exact mode 0440');
const repositories = validateRepositoryNames(JSON.parse(await readFile(repositoriesPath, 'utf8')));
const jwt = appJwt(appId, await readFile(keyPath));
const api = env.GITHUB_API_URL ?? 'https://api.github.com';
const request = async (path, authorization, options = {}) => {
  const response = await fetch(`${api}${path}`, { ...options, headers: { Accept: 'application/vnd.github+json', Authorization: authorization, 'User-Agent': 'f5-renovate-aks', 'X-GitHub-Api-Version': '2022-11-28', ...options.headers } });
  if (!response.ok) throw new Error(`GitHub API ${path} failed with ${response.status}`);
  return response.json();
};
const bearer = `Bearer ${jwt}`;
const app = await request('/app', bearer);
validateAppMetadata(app, appId, botLogin);
const installation = await request(`/app/installations/${installationId}`, bearer);
validateInstallation(installation, installationId, appId);
const issued = await request(`/app/installations/${installationId}/access_tokens`, bearer, { method: 'POST', body: JSON.stringify({ repositories: repositories.map((repository) => repository.split('/')[1]) }), headers: { 'Content-Type': 'application/json' } });
if (!/^ghs_[A-Za-z0-9]{20,}$/.test(issued.token ?? '')) throw new Error('GitHub returned a malformed installation token');
validateExpiry(issued.expires_at, deadline, margin);
const observed = await request('/installation/repositories?per_page=100', `Bearer ${issued.token}`);
validateObservedScope(observed, repositories);
const bot = await request(`/users/${encodeURIComponent(botLogin)}`, `Bearer ${issued.token}`);
validateBot(bot, botId, botLogin);
await writeFile(tokenPath, issued.token, { mode: 0o600, flag: 'wx' });
await chmod(tokenPath, 0o600);
console.log(`Verified GitHub App ${appId}, installation ${installationId}, bot ${botLogin}/${botId}, and 39 repositories.`);
