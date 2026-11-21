import assert from 'node:assert/strict';
import { afterEach, describe, it } from 'node:test';

import { adminRoute, editableText, makeSnowflake, parsePostFile, sniffImage } from '../src/admin.js';

const ADMIN = { email: 'a@gmail.com', name: 'Admin One' };
const JPEG_BASE64 = Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0, 0, 0, 0]).toString('base64');

describe('helpers', () => {
  it('makes tweet-style ids that increase with time and fit a snowflake', () => {
    const early = makeSnowflake(1_790_000_000_000, 5);
    const late = makeSnowflake(1_790_000_000_001, 0);
    assert.ok(BigInt(late) > BigInt(early));
    assert.match(early, /^\d{19}$/);
  });

  it('sniffs image types and rejects others', () => {
    assert.equal(sniffImage(Uint8Array.from([0xff, 0xd8, 0xff, 0x00])), 'jpg');
    assert.equal(sniffImage(Uint8Array.from([0x89, 0x50, 0x4e, 0x47])), 'png');
    assert.equal(sniffImage(Uint8Array.from([0x3c, 0x73, 0x76, 0x67])), null); // "<svg"
  });

  it('splits a post file and builds the editable text without extra-image lines', () => {
    const raw = '---\nlayout: post\ntitle: "כותרת \\"בדיקה\\""\nimage_alt: "הארץ"\nmanual: true\n---\nגוף\n\n![](/assets/images/tweets/1_1.jpg)\n';
    const { meta, body } = parsePostFile(raw);
    assert.equal(meta.title, 'כותרת "בדיקה"');
    assert.equal(editableText(meta, body), 'כותרת "בדיקה"\n\nגוף');
  });
});

describe('admin handlers (GitHub mocked)', () => {
  const realFetch = globalThis.fetch;
  afterEach(() => { globalThis.fetch = realFetch; });

  function mockGithub() {
    const calls = { blobs: [], trees: [], commits: [], dispatches: 0 };
    globalThis.fetch = async (url, init = {}) => {
      const path = new URL(url).pathname.replace('/repos/tsurdan/Milifney-100', '');
      const json = (data, status = 200) => new Response(JSON.stringify(data), { status });
      const body = init.body ? JSON.parse(init.body) : null;
      if (path === '/git/trees/main') return json({ tree: [{ path: '_posts', sha: 'posts-sha' }] });
      if (path === '/git/trees/posts-sha') return json({ tree: [{ path: '2026-01-01-1234567890123456.md' }] });
      if (path === '/git/blobs') { calls.blobs.push(body); return json({ sha: `blob${calls.blobs.length}` }); }
      if (path === '/git/ref/heads/main') return json({ object: { sha: 'head' } });
      if (path === '/git/commits/head') return json({ tree: { sha: 'base-tree' } });
      if (path === '/git/trees') { calls.trees.push(body); return json({ sha: 'new-tree' }); }
      if (path === '/git/commits') { calls.commits.push(body); return json({ sha: 'new-commit' }); }
      if (path === '/git/refs/heads/main') return json({});
      if (path.endsWith('/dispatches')) { calls.dispatches++; return new Response(null, { status: 204 }); }
      return json({ message: 'unexpected ' + path }, 500);
    };
    return calls;
  }

  it('queues a new post: images + request file in one commit, then dispatches the workflow', async () => {
    const calls = mockGithub();
    const result = await adminRoute('/admin/post/create')(
      { text: 'כותרת\n\nגוף', caption: 'הארץ, ארכיון העיתונות', images: [{ data: JPEG_BASE64 }] }, {}, ADMIN);

    assert.equal(result.success, true);
    const paths = calls.trees[0].tree.map((entry) => entry.path);
    assert.equal(paths.length, 2);
    assert.equal(paths[0], `assets/images/tweets/${result.id}.jpg`);
    assert.match(paths[1], new RegExp(`^_admin_inbox/\\d+-create-${result.id}\\.json$`));
    const request = JSON.parse(calls.blobs[1].content);
    assert.deepEqual(
      { action: request.action, id: request.id, text: request.text, caption: request.caption },
      { action: 'create', id: result.id, text: 'כותרת\n\nגוף', caption: 'הארץ, ארכיון העיתונות' });
    assert.equal('admin' in request, false);
    assert.doesNotMatch(calls.commits[0].message, /Admin One|a@gmail/);
    assert.equal(calls.dispatches, 1);
    assert.match(result.url, new RegExp(`^/\\d{4}/\\d{2}/\\d{2}/${result.id}/$`));
  });

  it('rejects empty text, non-image uploads and bad ids without touching GitHub', async () => {
    const calls = mockGithub();
    await assert.rejects(adminRoute('/admin/post/create')({ text: '   ' }, {}, ADMIN), { status: 400 });
    await assert.rejects(adminRoute('/admin/post/create')(
      { text: 'x', images: [{ data: Buffer.from('<svg/>').toString('base64') }] }, {}, ADMIN), { status: 400 });
    await assert.rejects(adminRoute('/admin/post/delete')({ slug: '../../etc' }, {}, ADMIN), { status: 400 });
    assert.equal(calls.blobs.length, 0);
    assert.equal(calls.dispatches, 0);
  });

  it('returns 404 when editing or deleting a post that does not exist', async () => {
    mockGithub();
    await assert.rejects(adminRoute('/admin/post/edit')({ slug: '9999999999999999', text: 'x' }, {}, ADMIN), { status: 404 });
    await assert.rejects(adminRoute('/admin/post/delete')({ slug: '9999999999999999' }, {}, ADMIN), { status: 404 });
  });

  it('queues a soft delete and remembers it for restore', async () => {
    const calls = mockGithub();
    const store = new Map();
    const env = { DOCTORATE_KV: { put: async (k, v) => store.set(k, v), delete: async (k) => store.delete(k) } };
    await adminRoute('/admin/post/delete')({ slug: '1234567890123456', title: 'כותרת' }, env, ADMIN);

    const request = JSON.parse(calls.blobs[0].content);
    assert.equal(request.action, 'delete');
    assert.equal(request.hard, false);
    assert.ok(store.has('admin:deleted:1234567890123456'));
  });
});
