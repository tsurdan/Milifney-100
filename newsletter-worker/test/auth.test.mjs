import assert from 'node:assert/strict';
import { beforeEach, describe, it } from 'node:test';

import { adminEmails, createSession, requireAdmin, resetCertsCache, SESSION_TTL_SECONDS, verifyGoogleIdToken } from '../src/auth.js';

const CLIENT_ID = 'client-123.apps.googleusercontent.com';
const ENV = { GOOGLE_CLIENT_ID: CLIENT_ID, ADMIN_EMAILS: 'Admin1@gmail.com, admin2@gmail.com;admin3@gmail.com' };
const NOW = 1_800_000_000_000;

const b64url = (data) => Buffer.from(data).toString('base64url');

const { publicKey, privateKey } = await crypto.subtle.generateKey(
  { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
  true, ['sign', 'verify']);
const jwk = { ...(await crypto.subtle.exportKey('jwk', publicKey)), kid: 'key-1', alg: 'RS256', use: 'sig' };

async function makeToken(overrides = {}, { header = { alg: 'RS256', kid: 'key-1' }, key = privateKey } = {}) {
  const payload = {
    iss: 'https://accounts.google.com',
    aud: CLIENT_ID,
    email: 'admin1@gmail.com',
    email_verified: true,
    name: 'Admin One',
    iat: Math.floor(NOW / 1000) - 10,
    exp: Math.floor(NOW / 1000) + 3000,
    ...overrides,
  };
  const signingInput = `${b64url(JSON.stringify(header))}.${b64url(JSON.stringify(payload))}`;
  const signature = await crypto.subtle.sign('RSASSA-PKCS1-v1_5', key, new TextEncoder().encode(signingInput));
  return `${signingInput}.${b64url(Buffer.from(signature))}`;
}

const fetchFn = async () => new Response(JSON.stringify({ keys: [jwk] }), {
  headers: { 'Cache-Control': 'public, max-age=3600' },
});
const requestWith = (token) => new Request('https://worker.test/admin/whoami', {
  method: 'POST',
  headers: token ? { Authorization: `Bearer ${token}` } : {},
});

describe('verifyGoogleIdToken', () => {
  beforeEach(resetCertsCache);

  it('accepts a valid token', async () => {
    const payload = await verifyGoogleIdToken(await makeToken(), ENV, { now: NOW, fetchFn });
    assert.equal(payload.email, 'admin1@gmail.com');
  });

  for (const [name, overrides] of [
    ['wrong audience', { aud: 'someone-else' }],
    ['wrong issuer', { iss: 'https://evil.example' }],
    ['expired token', { exp: Math.floor(NOW / 1000) - 600 }],
    ['unverified email', { email_verified: false }],
  ]) {
    it(`rejects ${name}`, async () => {
      await assert.rejects(
        verifyGoogleIdToken(await makeToken(overrides), ENV, { now: NOW, fetchFn }),
        { status: 401 });
    });
  }

  it('rejects a token signed with a different key', async () => {
    const other = await crypto.subtle.generateKey(
      { name: 'RSASSA-PKCS1-v1_5', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256' },
      true, ['sign']);
    const token = await makeToken({}, { key: other.privateKey });
    await assert.rejects(verifyGoogleIdToken(token, ENV, { now: NOW, fetchFn }), { status: 401 });
  });

  it('rejects a tampered payload', async () => {
    const [header, , signature] = (await makeToken()).split('.');
    const forged = b64url(JSON.stringify({ iss: 'accounts.google.com', aud: CLIENT_ID, email: 'evil@gmail.com', email_verified: true, exp: 9e9 }));
    await assert.rejects(verifyGoogleIdToken(`${header}.${forged}.${signature}`, ENV, { now: NOW, fetchFn }), { status: 401 });
  });

  it('rejects alg none and garbage', async () => {
    const none = `${b64url(JSON.stringify({ alg: 'none', kid: 'key-1' }))}.${b64url('{}')}.`;
    await assert.rejects(verifyGoogleIdToken(none, ENV, { now: NOW, fetchFn }), { status: 401 });
    await assert.rejects(verifyGoogleIdToken('not-a-jwt', ENV, { now: NOW, fetchFn }), { status: 401 });
  });
});

describe('admin sessions', () => {
  beforeEach(resetCertsCache);
  const SESSION_ENV = { ...ENV, SESSION_SECRET: 'secret-one' };
  const admin = { email: 'admin1@gmail.com', name: 'Admin One' };
  const bearer = (token) => new Request('https://worker.test/admin/post/get', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });

  it('a session works without a Google token, long after the Google token would have expired', async () => {
    const session = await createSession(admin, SESSION_ENV, NOW);
    const later = NOW + 20 * 24 * 60 * 60 * 1000;
    const result = await requireAdmin(bearer(session), SESSION_ENV, { now: later, fetchFn: () => { throw new Error('no network expected'); } });
    assert.equal(result.email, 'admin1@gmail.com');
  });

  it('a session expires after 30 days', async () => {
    const session = await createSession(admin, SESSION_ENV, NOW);
    await assert.rejects(
      requireAdmin(bearer(session), SESSION_ENV, { now: NOW + (SESSION_TTL_SECONDS + 60) * 1000 }), { status: 401 });
  });

  it('rejects a tampered or foreign-secret session', async () => {
    const session = await createSession(admin, SESSION_ENV, NOW);
    const [prefix, , signature] = session.split('.');
    const forged = Buffer.from(JSON.stringify({ email: 'evil@gmail.com', exp: 9e9 })).toString('base64url');
    await assert.rejects(requireAdmin(bearer(`${prefix}.${forged}.${signature}`), SESSION_ENV, { now: NOW }), { status: 401 });
    await assert.rejects(requireAdmin(bearer(session), { ...SESSION_ENV, SESSION_SECRET: 'other' }, { now: NOW }), { status: 401 });
  });

  it('removing an admin from the allowlist ends their session', async () => {
    const session = await createSession(admin, SESSION_ENV, NOW);
    await assert.rejects(
      requireAdmin(bearer(session), { ...SESSION_ENV, ADMIN_EMAILS: 'admin2@gmail.com' }, { now: NOW }), { status: 403 });
  });

  it('falls back to GITHUB_PAT as the signing secret', async () => {
    const env = { ...ENV, GITHUB_PAT: 'pat' };
    const session = await createSession(admin, env, NOW);
    assert.equal((await requireAdmin(bearer(session), env, { now: NOW })).email, 'admin1@gmail.com');
  });
});

describe('requireAdmin', () => {
  beforeEach(resetCertsCache);

  it('parses the allowlist', () => {
    assert.deepEqual(adminEmails(ENV), ['admin1@gmail.com', 'admin2@gmail.com', 'admin3@gmail.com']);
  });

  it('lets an allowlisted admin in (case-insensitive)', async () => {
    const token = await makeToken({ email: 'ADMIN2@gmail.com' });
    const admin = await requireAdmin(requestWith(token), ENV, { now: NOW, fetchFn });
    assert.equal(admin.email, 'admin2@gmail.com');
  });

  it('rejects a valid Google account that is not an admin', async () => {
    const token = await makeToken({ email: 'stranger@gmail.com' });
    await assert.rejects(requireAdmin(requestWith(token), ENV, { now: NOW, fetchFn }), { status: 403 });
  });

  it('rejects everyone when the allowlist is empty', async () => {
    await assert.rejects(
      requireAdmin(requestWith(await makeToken()), { ...ENV, ADMIN_EMAILS: '' }, { now: NOW, fetchFn }),
      { status: 403 });
  });

  it('requires a bearer token', async () => {
    await assert.rejects(requireAdmin(requestWith(null), ENV, { now: NOW, fetchFn }), { status: 401 });
  });
});
