/**
 * Minimal GitHub REST helpers for the admin panel. The Worker never edits posts itself:
 * it commits a request file (+ uploaded images) and dispatches the admin-post workflow,
 * which applies it with the same Python code that builds posts from tweets.
 *
 * Needs the GITHUB_PAT secret with "Contents: Read and write" and "Actions: Read and write".
 */

import { HttpError } from './errors.js';

export const GITHUB_REPO = 'tsurdan/Milifney-100';
export const BRANCH = 'main';
export const ADMIN_WORKFLOW = 'admin-post.yml';
const API = 'https://api.github.com';

async function github(env, path, init = {}, accept = 'application/vnd.github+json') {
  const resp = await fetch(`${API}/repos/${GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      'Authorization': `Bearer ${env.GITHUB_PAT}`,
      'Accept': accept,
      'User-Agent': 'milifney100-admin-worker',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
    },
  });
  if (!resp.ok && resp.status !== 204) {
    console.error('GitHub API error', path, resp.status, await resp.text());
    throw new HttpError(resp.status === 404 ? 404 : 502, 'GitHub request failed');
  }
  return resp;
}

const githubJson = async (env, path, init) => (await github(env, path, init)).json();

let postsListing = { sha: null, names: [] };

/** Finds the filename of a post by its id (the part after the date in YYYY-MM-DD-<id>.md). */
export async function findPostFile(env, slug) {
  // Contents API caps a directory at 1000 files; the git tree API does not.
  const root = await githubJson(env, `/git/trees/${BRANCH}`);
  const postsEntry = root.tree.find((entry) => entry.path === '_posts');
  if (!postsEntry) throw new HttpError(404, 'Posts folder not found');
  if (postsListing.sha !== postsEntry.sha) {
    const tree = await githubJson(env, `/git/trees/${postsEntry.sha}`);
    postsListing = { sha: postsEntry.sha, names: tree.tree.map((entry) => entry.path) };
  }
  return postsListing.names.find((name) => name.endsWith(`-${slug}.md`) && name.slice(11, -3) === slug) || null;
}

export async function readPostFile(env, filename) {
  const resp = await github(env, `/contents/_posts/${encodeURIComponent(filename)}?ref=${BRANCH}`, {}, 'application/vnd.github.raw+json');
  return resp.text();
}

/**
 * Commits several files in one commit. Each file is { path, content } (text) or
 * { path, base64 }. Retries when the branch moved while we were building the commit.
 */
export async function commitFiles(env, files, message) {
  const entries = [];
  for (const file of files) {
    const blob = await githubJson(env, '/git/blobs', {
      method: 'POST',
      body: JSON.stringify(file.base64 !== undefined
        ? { content: file.base64, encoding: 'base64' }
        : { content: file.content, encoding: 'utf-8' }),
    });
    entries.push({ path: file.path, mode: '100644', type: 'blob', sha: blob.sha });
  }

  for (let attempt = 0; attempt < 3; attempt++) {
    const ref = await githubJson(env, `/git/ref/heads/${BRANCH}`);
    const parent = await githubJson(env, `/git/commits/${ref.object.sha}`);
    const tree = await githubJson(env, '/git/trees', {
      method: 'POST',
      body: JSON.stringify({ base_tree: parent.tree.sha, tree: entries }),
    });
    const commit = await githubJson(env, '/git/commits', {
      method: 'POST',
      body: JSON.stringify({ message, tree: tree.sha, parents: [ref.object.sha] }),
    });
    try {
      await githubJson(env, `/git/refs/heads/${BRANCH}`, {
        method: 'PATCH',
        body: JSON.stringify({ sha: commit.sha, force: false }),
      });
      return commit.sha;
    } catch (err) {
      if (attempt === 2) throw err;
    }
  }
}

export async function dispatchAdminWorkflow(env) {
  await github(env, `/actions/workflows/${ADMIN_WORKFLOW}/dispatches`, {
    method: 'POST',
    body: JSON.stringify({ ref: BRANCH }),
  });
}

export async function latestAdminRuns(env) {
  const data = await githubJson(env, `/actions/workflows/${ADMIN_WORKFLOW}/runs?per_page=5`);
  return data.workflow_runs.map((run) => ({
    id: run.id,
    status: run.status,
    conclusion: run.conclusion,
    created_at: run.created_at,
    url: run.html_url,
  }));
}
