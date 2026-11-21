/**
 * Admin authentication: verifies a Google Sign-In ID token (RS256 JWT) and checks the
 * signed-in account against the hardcoded admin allowlist. This is the real security
 * boundary — the admin page only hides the UI.
 *
 * Env: GOOGLE_CLIENT_ID (the OAuth web client ID), ADMIN_EMAILS (comma-separated secret).
 */

import { HttpError } from './errors.js';

const GOOGLE_CERTS_URL = 'https://www.googleapis.com/oauth2/v3/certs';
const ISSUERS = new Set(['accounts.google.com', 'https://accounts.google.com']);
const CLOCK_SKEW_SECONDS = 60;
const DEFAULT_CERTS_TTL_MS = 60 * 60 * 1000;

let certsCache = { keys: null, expires: 0 };

function base64UrlToBytes(value) {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(value.length / 4) * 4, '=');
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function decodeJson(part) {
  return JSON.parse(new TextDecoder().decode(base64UrlToBytes(part)));
}

async function loadCerts(fetchFn, now, force) {
  if (!force && certsCache.keys && certsCache.expires > now) return certsCache.keys;
  const resp = await fetchFn(GOOGLE_CERTS_URL);
  if (!resp.ok) throw new HttpError(503, 'Could not load Google signing keys');
  const { keys } = await resp.json();
  const maxAge = /max-age=(\d+)/.exec(resp.headers.get('Cache-Control') || '');
  certsCache = { keys, expires: now + (maxAge ? Number(maxAge[1]) * 1000 : DEFAULT_CERTS_TTL_MS) };
  return keys;
}

export function resetCertsCache() {
  certsCache = { keys: null, expires: 0 };
}

export async function verifyGoogleIdToken(token, env, { now = Date.now(), fetchFn = fetch } = {}) {
  const invalid = (reason) => new HttpError(401, `Invalid sign-in token (${reason})`);
  if (!env.GOOGLE_CLIENT_ID) throw new HttpError(500, 'Server is missing GOOGLE_CLIENT_ID');

  const parts = String(token || '').split('.');
  if (parts.length !== 3) throw invalid('format');

  let header;
  let payload;
  try {
    header = decodeJson(parts[0]);
    payload = decodeJson(parts[1]);
  } catch {
    throw invalid('format');
  }
  if (header.alg !== 'RS256' || !header.kid) throw invalid('algorithm');

  let keys = await loadCerts(fetchFn, now, false);
  let jwk = keys.find((k) => k.kid === header.kid);
  if (!jwk) {
    keys = await loadCerts(fetchFn, now, true); // Google rotates keys
    jwk = keys.find((k) => k.kid === header.kid);
  }
  if (!jwk) throw invalid('unknown key');

  const key = await crypto.subtle.importKey(
    'jwk', jwk, { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }, false, ['verify']);
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  const valid = await crypto.subtle.verify(
    'RSASSA-PKCS1-v1_5', key, base64UrlToBytes(parts[2]), signed);
  if (!valid) throw invalid('signature');

  const nowSeconds = Math.floor(now / 1000);
  if (!ISSUERS.has(payload.iss)) throw invalid('issuer');
  if (payload.aud !== env.GOOGLE_CLIENT_ID) throw invalid('audience');
  if (typeof payload.exp !== 'number' || payload.exp + CLOCK_SKEW_SECONDS < nowSeconds) throw invalid('expired');
  if (typeof payload.iat === 'number' && payload.iat - CLOCK_SKEW_SECONDS > nowSeconds) throw invalid('issued in the future');
  if (!(payload.email_verified === true || payload.email_verified === 'true')) throw invalid('email not verified');
  return payload;
}

export function adminEmails(env) {
  return String(env.ADMIN_EMAILS || '')
    .split(/[\s,;]+/)
    .map((email) => email.trim().toLowerCase())
    .filter(Boolean);
}

/** Returns { email, name } for a request from an allowlisted admin; throws HttpError(401/403) otherwise. */
export async function requireAdmin(request, env, options) {
  const match = /^Bearer\s+(.+)$/i.exec(request.headers.get('Authorization') || '');
  if (!match) throw new HttpError(401, 'Sign-in required');

  const payload = await verifyGoogleIdToken(match[1].trim(), env, options);
  const email = String(payload.email || '').toLowerCase();
  if (!email || !adminEmails(env).includes(email)) throw new HttpError(403, 'This Google account is not an administrator');
  return { email, name: payload.name || email.split('@')[0] };
}
