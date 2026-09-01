import { createSign } from 'node:crypto';

export const EXACT_PERMISSIONS = Object.freeze({
  checks: 'write',
  contents: 'write',
  metadata: 'read',
  pull_requests: 'write',
  statuses: 'write',
  workflows: 'write',
});

export function positiveId(value, name) {
  if (!/^[1-9][0-9]*$/.test(value ?? '') || !Number.isSafeInteger(Number(value))) {
    throw new Error(`${name} must be a positive decimal integer`);
  }
  return Number(value);
}

export function validateDeadline(deadlineValue, marginValue) {
  const deadline = positiveId(deadlineValue, 'RENOVATE_JOB_DEADLINE_SECONDS');
  const margin = positiveId(marginValue, 'RENOVATE_TOKEN_SAFETY_MARGIN_SECONDS');
  if (deadline + margin >= 3600) throw new Error('deadline plus safety margin must be less than one hour');
  return { deadline, margin };
}

export function appJwt(appId, privateKey, now = Math.floor(Date.now() / 1000)) {
  const b64 = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
  const input = `${b64({ alg: 'RS256', typ: 'JWT' })}.${b64({ iat: now - 60, exp: now + 540, iss: appId })}`;
  const signer = createSign('RSA-SHA256');
  signer.update(input);
  signer.end();
  return `${input}.${signer.sign(privateKey, 'base64url')}`;
}

export function exactPermissions(value) {
  return value && typeof value === 'object' && !Array.isArray(value) &&
    JSON.stringify(Object.fromEntries(Object.entries(value).sort())) ===
      JSON.stringify(EXACT_PERMISSIONS);
}

export function validateRepositoryNames(value) {
  if (!Array.isArray(value) || value.length !== 39 || new Set(value).size !== 39 ||
      value.some((name) => !/^f5-sales-demo\/[a-z0-9][a-z0-9-]*$/.test(name)) ||
      value.includes('f5-sales-demo/self-hosted-runner') ||
      JSON.stringify(value) !== JSON.stringify([...value].sort())) {
    throw new Error('repository scope must equal 39 sorted unique f5-sales-demo repositories');
  }
  return value;
}

export function validateExpiry(expiresAt, deadline, margin, now = Date.now()) {
  const remaining = Date.parse(expiresAt) - now;
  if (!Number.isFinite(remaining) || remaining < (deadline + margin) * 1000) {
    throw new Error('installation token lifetime is shorter than the deadline plus safety margin');
  }
}

export function validateInstallationToken(value) {
  // GitHub installation tokens use the ghs_ prefix. Newer token encodings are
  // structured and can include base64url punctuation, so do not assume the
  // legacy alphanumeric-only representation.
  if (!/^ghs_[A-Za-z0-9._-]{20,}$/.test(value ?? ''))
    throw new Error('GitHub returned a malformed installation token');
  return value;
}

export function validateAppMetadata(app, appId, botLogin) {
  if (app?.id !== appId || `${app?.slug}[bot]`.toLowerCase() !== botLogin.toLowerCase()) {
    throw new Error('GitHub App metadata does not match the configured identity');
  }
}

export function validateInstallation(installation, installationId, appId) {
  if (installation?.id !== installationId || installation?.app_id !== appId ||
      installation?.repository_selection !== 'selected' || !exactPermissions(installation?.permissions)) {
    throw new Error('GitHub App installation metadata, permissions, or selection mode differs');
  }
}

export function validateBot(bot, botId, botLogin) {
  if (bot?.id !== botId || bot?.login?.toLowerCase() !== botLogin.toLowerCase() || bot?.type !== 'Bot') {
    throw new Error('GitHub App bot identity differs from configured metadata');
  }
}

export function validateObservedScope(observed, expected) {
  const actual = validateRepositoryNames((observed?.repositories ?? []).map((repository) => repository.full_name).sort());
  if (observed?.total_count !== 39 || JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error('installation token repository scope differs from the exact inventory');
  }
}
