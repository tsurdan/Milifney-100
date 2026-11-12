/**
 * Standalone job (unrelated to Milifney-100): searches X for new tweets that
 * start with "דוקטורט:" or "דוקטורט-" and posts their links to a Telegram group.
 * Runs from the same cron trigger as the rest of the worker.
 *
 * Requires: KV binding DOCTORATE_KV, secrets TELEGRAM_BOT_TOKEN and DOCTORATE_CHAT_ID.
 */

const SEARCH_URL = 'https://api.fxtwitter.com/2/search';
const PREFIXES = ['דוקטורט:', 'דוקטורט-'];
const MAX_AGE_MS = 3 * 60 * 60 * 1000;
const SENT_KEY = 'sent_ids';
const MAX_SENT = 100;

export async function runDoctorateJob(env) {
  if (!env.DOCTORATE_KV || !env.TELEGRAM_BOT_TOKEN || !env.DOCTORATE_CHAT_ID) {
    console.error('Doctorate job: missing DOCTORATE_KV / TELEGRAM_BOT_TOKEN / DOCTORATE_CHAT_ID');
    return;
  }

  const resp = await fetch(`${SEARCH_URL}?q=${encodeURIComponent('דוקטורט')}&feed=latest`, {
    headers: { 'User-Agent': 'milifney100-cron-worker' },
  });
  if (!resp.ok) {
    console.error('Doctorate job: search failed', resp.status);
    return;
  }

  const { results = [] } = await resp.json();
  const sent = JSON.parse((await env.DOCTORATE_KV.get(SENT_KEY)) || '[]');
  const cutoff = Date.now() - MAX_AGE_MS;

  const fresh = results
    .filter(t => t && t.id && typeof t.text === 'string')
    .filter(t => PREFIXES.some(p => t.text.startsWith(p)))
    .filter(t => (t.created_timestamp || 0) * 1000 >= cutoff)
    .filter(t => !sent.includes(t.id))
    .sort((a, b) => (BigInt(a.id) < BigInt(b.id) ? -1 : 1));

  for (const tweet of fresh) {
    const ok = await sendToTelegram(env, tweet.url || `https://x.com/i/status/${tweet.id}`);
    if (!ok) break;
    sent.push(tweet.id);
    await env.DOCTORATE_KV.put(SENT_KEY, JSON.stringify(sent.slice(-MAX_SENT)));
  }
}

async function sendToTelegram(env, text) {
  const resp = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: env.DOCTORATE_CHAT_ID, text }),
  });
  if (!resp.ok) {
    console.error('Doctorate job: Telegram send failed', resp.status, await resp.text());
  }
  return resp.ok;
}
