/**
 * Admin panel endpoints (all POST, all behind requireAdmin — see index.js):
 *   /admin/whoami        sign in (Google token -> session token) and renew the session
 *   /admin/post/get      load a post for editing
 *   /admin/post/create   new post (title + markdown body + images + caption) — exactly like a tweet
 *   /admin/post/edit     change title / body / image / caption
 *   /admin/post/delete   hide a post, or delete it permanently ({ hard: true })
 *   /admin/post/restore  un-hide a hidden post
 *   /admin/post/deleted  list hidden posts that can be restored
 *   /admin/status        latest runs of the admin-post workflow
 *
 * Everything that changes a post is queued as a request file in _admin_inbox/ and applied
 * by scripts/admin_apply.py in the admin-post workflow, so tags, SEO title, sources, URL,
 * etc. are all derived by the same pipeline as tweets.
 */

import { load as loadYaml } from 'js-yaml';
import { createSession } from './auth.js';
import { HttpError } from './errors.js';
import { commitFiles, dispatchAdminWorkflow, findPostFile, latestAdminRuns, readPostFile } from './github.js';

const MAX_TEXT = 20000;
const MAX_TITLE = 300;
const MAX_CAPTION = 300;
const MAX_IMAGES = 6;
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const TWITTER_EPOCH = 1288834974657n;
const DELETED_PREFIX = 'admin:deleted:';
const SLUG_RE = /^\d{10,25}$/;
const MD_IMAGE_LINE_RE = /^\s*!\[[^\]]*\]\([^)]*\)\s*$/;

// ─── helpers (exported for tests) ──────────────────────────────────────────────

/** Tweet-style ID derived from the time, so manual posts sort among real tweets by filename. */
export function makeSnowflake(nowMs, randomBits = crypto.getRandomValues(new Uint32Array(1))[0]) {
  return String(((BigInt(nowMs) - TWITTER_EPOCH) << 22n) | BigInt(randomBits & 0x3fffff));
}

/** Detects the image type from its first bytes; returns the file extension or null. */
export function sniffImage(bytes) {
  const startsWith = (...sig) => sig.every((b, i) => bytes[i] === b);
  if (startsWith(0xff, 0xd8, 0xff)) return 'jpg';
  if (startsWith(0x89, 0x50, 0x4e, 0x47)) return 'png';
  if (startsWith(0x47, 0x49, 0x46, 0x38)) return 'gif';
  if (startsWith(0x52, 0x49, 0x46, 0x46) && bytes[8] === 0x57 && bytes[9] === 0x45 && bytes[10] === 0x42 && bytes[11] === 0x50) return 'webp';
  return null;
}

/** Splits a post file into its parsed front matter and body. */
export function parsePostFile(raw) {
  const match = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(raw.replace(/\r\n/g, '\n'));
  if (!match) throw new HttpError(422, 'Unreadable post file');
  let meta;
  try {
    meta = loadYaml(match[1]) || {};
  } catch {
    throw new HttpError(422, 'Unreadable post front matter');
  }
  return { meta, body: match[2] };
}

/** The editable body of a post: markdown without the extra-image lines (those are managed separately). */
export function editableBody(body) {
  return body.split('\n').filter((line) => !MD_IMAGE_LINE_RE.test(line)).join('\n').trim();
}

function requireSlug(value) {
  const slug = String(value || '');
  if (!SLUG_RE.test(slug)) throw new HttpError(400, 'Invalid post id');
  return slug;
}

function requireTitle(value) {
  const title = String(value || '').replace(/\s+/g, ' ').trim();
  if (!title) throw new HttpError(400, 'חסרה כותרת');
  if (title.length > MAX_TITLE) throw new HttpError(400, 'הכותרת ארוכה מדי');
  return title;
}

function optionalBody(value) {
  if (value === undefined || value === null) return undefined;
  const body = String(value).replace(/\r\n?/g, '\n').trim();
  if (body.length > MAX_TEXT) throw new HttpError(400, 'הטקסט ארוך מדי');
  return body;
}

function optionalCaption(value) {
  if (value === undefined || value === null) return undefined;
  const caption = String(value).replace(/\s+/g, ' ').trim();
  if (caption.length > MAX_CAPTION) throw new HttpError(400, 'הכיתוב ארוך מדי');
  return caption;
}

function decodeImage(input) {
  const base64 = String(input?.data || '').replace(/^data:[^,]*,/, '');
  let bytes;
  try {
    const binary = atob(base64);
    bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
  } catch {
    throw new HttpError(400, 'תמונה לא תקינה');
  }
  const ext = sniffImage(bytes);
  if (!ext) throw new HttpError(400, 'סוג תמונה לא נתמך (JPG, PNG, GIF או WEBP)');
  if (bytes.length > MAX_IMAGE_BYTES) throw new HttpError(400, 'התמונה גדולה מדי');
  return { ext, base64 };
}

function inboxFile(action, slug, request, now) {
  return {
    path: `_admin_inbox/${now}-${action}-${slug}.json`,
    content: JSON.stringify({ action, ...request, created_ms: now }),
  };
}

// Commit messages and request files deliberately carry no admin identity: the repo is public.
async function submit(env, files, message) {
  await commitFiles(env, files, message);
  await dispatchAdminWorkflow(env);
}

async function requireExistingPost(env, slug) {
  const filename = await findPostFile(env, slug);
  if (!filename) throw new HttpError(404, 'הידיעה לא נמצאה');
  return filename;
}

// ─── handlers ──────────────────────────────────────────────────────────────────

// Also the sign-in endpoint: called with a Google token it starts a session, with a session it renews it.
async function whoami(_body, env, admin) {
  return { success: true, email: admin.email, name: admin.name, session: await createSession(admin, env) };
}

async function getPost(body, env) {
  const slug = requireSlug(body.slug);
  const filename = await requireExistingPost(env, slug);
  const { meta, body: postBody } = parsePostFile(await readPostFile(env, filename));
  return {
    success: true,
    slug,
    title: String(meta.title || ''),
    body: editableBody(postBody),
    caption: String(meta.image_alt || ''),
    image: meta.image || '',
    published: meta.published !== false,
    manual: meta.manual === true,
  };
}

async function createPost(body, env) {
  const title = requireTitle(body.title);
  const text = optionalBody(body.body) || '';
  const caption = optionalCaption(body.caption) || '';
  const inputs = Array.isArray(body.images) ? body.images : [];
  if (inputs.length > MAX_IMAGES) throw new HttpError(400, 'יותר מדי תמונות');
  const images = inputs.map(decodeImage);

  const now = Date.now();
  const id = makeSnowflake(now);
  const files = images.map((image, index) => ({
    path: `assets/images/tweets/${index === 0 ? id : `${id}_${index}`}.${image.ext}`,
    base64: image.base64,
  }));
  files.push(inboxFile('create', id, { id, title, body: text, caption, images: files.map((f) => f.path) }, now));

  await submit(env, files, `Admin: new post ${id}`);

  const date = new Date(now);
  const pad = (n) => String(n).padStart(2, '0');
  const url = `/${date.getUTCFullYear() - 100}/${pad(date.getUTCMonth() + 1)}/${pad(date.getUTCDate())}/${id}/`;
  return { success: true, id, url };
}

async function editPost(body, env) {
  const slug = requireSlug(body.slug);
  const title = requireTitle(body.title);
  const text = optionalBody(body.body); // undefined = body untouched
  const caption = optionalCaption(body.caption);
  await requireExistingPost(env, slug);

  const now = Date.now();
  const files = [];
  const request = { slug, title };
  if (text !== undefined) request.body = text;
  if (caption !== undefined) request.caption = caption;
  if (body.image) {
    const image = decodeImage(body.image);
    request.image = `assets/images/tweets/${slug}-e${now}.${image.ext}`;
    files.push({ path: request.image, base64: image.base64 });
  }
  files.push(inboxFile('edit', slug, request, now));

  await submit(env, files, `Admin: edit post ${slug}`);
  return { success: true };
}

async function deletePost(body, env) {
  const slug = requireSlug(body.slug);
  const hard = body.hard === true;
  await requireExistingPost(env, slug);

  const now = Date.now();
  await submit(env, [inboxFile('delete', slug, { slug, hard }, now)],
    `Admin: ${hard ? 'delete' : 'hide'} post ${slug}`);

  if (env.DOCTORATE_KV) {
    if (hard) {
      await env.DOCTORATE_KV.delete(DELETED_PREFIX + slug);
    } else {
      await env.DOCTORATE_KV.put(DELETED_PREFIX + slug, JSON.stringify({
        slug, title: String(body.title || '').slice(0, 300), date: String(body.date || '').slice(0, 20), at: now,
      }));
    }
  }
  return { success: true };
}

async function restorePost(body, env) {
  const slug = requireSlug(body.slug);
  await requireExistingPost(env, slug);
  await submit(env, [inboxFile('restore', slug, { slug }, Date.now())], `Admin: restore post ${slug}`);
  if (env.DOCTORATE_KV) await env.DOCTORATE_KV.delete(DELETED_PREFIX + slug);
  return { success: true };
}

async function listDeleted(_body, env) {
  if (!env.DOCTORATE_KV) return { success: true, posts: [] };
  const { keys } = await env.DOCTORATE_KV.list({ prefix: DELETED_PREFIX, limit: 100 });
  const posts = (await Promise.all(keys.map((k) => env.DOCTORATE_KV.get(k.name, 'json')))).filter(Boolean);
  posts.sort((a, b) => b.at - a.at);
  return { success: true, posts };
}

async function status(_body, env) {
  return { success: true, runs: await latestAdminRuns(env) };
}

const ROUTES = {
  '/admin/whoami': whoami,
  '/admin/post/get': getPost,
  '/admin/post/create': createPost,
  '/admin/post/edit': editPost,
  '/admin/post/delete': deletePost,
  '/admin/post/restore': restorePost,
  '/admin/post/deleted': listDeleted,
  '/admin/status': status,
};

export function adminRoute(pathname) {
  return ROUTES[pathname] || null;
}
