import assert from 'node:assert/strict';
import test from 'node:test';
import { generateKeyPairSync, verify } from 'node:crypto';
import { appJwt, exactPermissions, positiveId, validateAppMetadata, validateBot, validateDeadline, validateExpiry, validateInstallation, validateObservedScope, validateRepositoryNames } from './github-app.mjs';

test('IDs and the deadline are strict positive integers', () => {
  assert.equal(42, positiveId('42', 'id'));
  for (const value of ['', '0', '-1', '1.0', 'abc']) assert.throws(() => positiveId(value, 'id'));
  assert.deepEqual({ deadline: 2700, margin: 120 }, validateDeadline('2700', '120'));
  assert.throws(() => validateDeadline('3500', '100'));
});

test('permissions must equal the minimal App permission set', () => {
  assert.equal(true, exactPermissions({ metadata: 'read', contents: 'write', pull_requests: 'write', workflows: 'write' }));
  assert.equal(false, exactPermissions({ metadata: 'read', contents: 'write', pull_requests: 'write', workflows: 'write', issues: 'read' }));
});

test('token must outlive the deadline and safety margin', () => {
  const now = Date.parse('2026-08-31T12:00:00Z');
  validateExpiry('2026-08-31T12:47:00Z', 2700, 120, now);
  assert.throws(() => validateExpiry('2026-08-31T12:46:59Z', 2700, 120, now));
});

test('repository scope is exact and excludes self-hosted-runner', () => {
  const repositories = Array.from({ length: 39 }, (_, index) => `f5-sales-demo/repo-${String(index).padStart(2, '0')}`);
  assert.equal(repositories, validateRepositoryNames(repositories));
  assert.throws(() => validateRepositoryNames(repositories.slice(1)));
  assert.throws(() => validateRepositoryNames([...repositories.slice(1), 'f5-sales-demo/self-hosted-runner'].sort()));
});

test('JWT has bounded claims and a valid RS256 signature', () => {
  const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 });
  const jwt = appJwt(123, privateKey, 1_000);
  const [header, payload, signature] = jwt.split('.');
  assert.deepEqual({ alg: 'RS256', typ: 'JWT' }, JSON.parse(Buffer.from(header, 'base64url')));
  assert.deepEqual({ iat: 940, exp: 1540, iss: 123 }, JSON.parse(Buffer.from(payload, 'base64url')));
  assert.equal(true, verify('RSA-SHA256', Buffer.from(`${header}.${payload}`), publicKey, Buffer.from(signature, 'base64url')));
});

test('App, installation, bot, and repository API mismatches fail closed', () => {
  const permissions = { metadata: 'read', contents: 'write', pull_requests: 'write', workflows: 'write' };
  validateAppMetadata({ id: 1, slug: 'f5-renovate-aks' }, 1, 'f5-renovate-aks[bot]');
  assert.throws(() => validateAppMetadata({ id: 2, slug: 'f5-renovate-aks' }, 1, 'f5-renovate-aks[bot]'));
  validateInstallation({ id: 2, app_id: 1, repository_selection: 'selected', permissions }, 2, 1);
  assert.throws(() => validateInstallation({ id: 2, app_id: 1, repository_selection: 'all', permissions }, 2, 1));
  validateBot({ id: 3, login: 'f5-renovate-aks[bot]', type: 'Bot' }, 3, 'f5-renovate-aks[bot]');
  assert.throws(() => validateBot({ id: 3, login: 'lookalike[bot]', type: 'Bot' }, 3, 'f5-renovate-aks[bot]'));
  const expected = Array.from({ length: 39 }, (_, index) => `f5-sales-demo/repo-${String(index).padStart(2, '0')}`);
  validateObservedScope({ total_count: 39, repositories: expected.map((full_name) => ({ full_name })) }, expected);
  assert.throws(() => validateObservedScope({ total_count: 38, repositories: expected.slice(1).map((full_name) => ({ full_name })) }, expected));
});
