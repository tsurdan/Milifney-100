/**
 * Milifney-100 Newsletter Worker
 * Cloudflare Worker that proxies requests to Brevo (Sendinblue) API.
 * 
 * Endpoints:
 *   POST /subscribe       - Add email to newsletter list
 *   POST /unsubscribe     - Remove email from list
 *   POST /send-newsletter - Admin: send newsletter to all subscribers
 */

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const origin = request.headers.get('Origin') || '';
    const allowedOrigins = [env.CORS_ORIGIN, 'http://localhost:4000', 'http://127.0.0.1:4000'];
    const corsOrigin = allowedOrigins.includes(origin) ? origin : env.CORS_ORIGIN;
    const corsHeaders = {
      'Access-Control-Allow-Origin': corsOrigin,
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    // Handle CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders });
    }

    if (request.method !== 'POST') {
      return jsonResponse({ error: 'Method not allowed' }, 405, corsHeaders);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return jsonResponse({ error: 'Invalid JSON' }, 400, corsHeaders);
    }

    try {
      switch (url.pathname) {
        case '/subscribe':
          return await handleSubscribe(body, env, corsHeaders);
        case '/unsubscribe':
          return await handleUnsubscribe(body, env, corsHeaders);
        case '/send-newsletter':
          return await handleSendNewsletter(body, env, corsHeaders);
        default:
          return jsonResponse({ error: 'Not found' }, 404, corsHeaders);
      }
    } catch (err) {
      return jsonResponse({ error: 'Internal error' }, 500, corsHeaders);
    }
  }
};

// ─── Subscribe ─────────────────────────────────────────────────────────────────

async function handleSubscribe(body, env, corsHeaders) {
  const { email } = body;
  if (!email || !isValidEmail(email)) {
    return jsonResponse({ error: 'כתובת מייל לא תקינה' }, 400, corsHeaders);
  }

  const resp = await brevoRequest(env, 'POST', '/contacts', {
    email: email.trim().toLowerCase(),
    listIds: [parseInt(env.BREVO_LIST_ID)],
    updateEnabled: true,
  });

  if (resp.ok || resp.status === 204) {
    return jsonResponse({ success: true, message: 'נרשמת בהצלחה!' }, 200, corsHeaders);
  }

  const data = await resp.json().catch(() => ({}));
  if (data.code === 'duplicate_parameter') {
    return jsonResponse({ success: true, message: 'כבר רשום/ה לניוזלטר' }, 200, corsHeaders);
  }

  return jsonResponse({ error: 'שגיאה בהרשמה, נסו שוב' }, 500, corsHeaders);
}

// ─── Unsubscribe ───────────────────────────────────────────────────────────────

async function handleUnsubscribe(body, env, corsHeaders) {
  const { email } = body;
  if (!email || !isValidEmail(email)) {
    return jsonResponse({ error: 'כתובת מייל לא תקינה' }, 400, corsHeaders);
  }

  const resp = await brevoRequest(env, 'POST', `/contacts/${encodeURIComponent(email.trim().toLowerCase())}`, {
    listIds: [parseInt(env.BREVO_LIST_ID)],
    unlinkListIds: [parseInt(env.BREVO_LIST_ID)],
  });

  return jsonResponse({ success: true, message: 'הוסרת מרשימת התפוצה' }, 200, corsHeaders);
}

// ─── Send Newsletter (Admin) ───────────────────────────────────────────────────

async function handleSendNewsletter(body, env, corsHeaders) {
  const { password, subject, intro, articles } = body;

  // Validate admin password
  if (!password || password !== env.ADMIN_PASSWORD) {
    return jsonResponse({ error: 'סיסמה שגויה' }, 401, corsHeaders);
  }

  if (!subject || !intro || !articles || !Array.isArray(articles) || articles.length === 0) {
    return jsonResponse({ error: 'חסרים שדות: subject, intro, articles' }, 400, corsHeaders);
  }

  // Build HTML email
  const htmlContent = buildEmailHtml(subject, intro, articles);

  // Create and send campaign via Brevo
  const campaignResp = await brevoRequest(env, 'POST', '/emailCampaigns', {
    name: `Newsletter - ${new Date().toISOString().slice(0, 10)}`,
    subject: subject,
    sender: { name: 'חדשות מלפני מאה', email: 'milifney100@gmail.com' },
    type: 'classic',
    htmlContent: htmlContent,
    recipients: { listIds: [parseInt(env.BREVO_LIST_ID)] },
  });

  if (!campaignResp.ok) {
    const err = await campaignResp.json().catch(() => ({}));
    return jsonResponse({ error: 'שגיאה ביצירת קמפיין', details: err }, 500, corsHeaders);
  }

  const campaign = await campaignResp.json();

  // Send the campaign immediately
  const sendResp = await brevoRequest(env, 'POST', `/emailCampaigns/${campaign.id}/sendNow`, {});

  if (!sendResp.ok && sendResp.status !== 204) {
    return jsonResponse({ error: 'הקמפיין נוצר אך השליחה נכשלה' }, 500, corsHeaders);
  }

  return jsonResponse({ success: true, message: 'הניוזלטר נשלח בהצלחה!' }, 200, corsHeaders);
}

// ─── Email HTML Builder ────────────────────────────────────────────────────────

function buildEmailHtml(subject, intro, articles) {
  const articleCards = articles.map(a => `
    <tr>
      <td style="padding: 0 0 24px 0;">
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background: #ffffff; border: 1px solid #d4c9b8; border-radius: 6px; overflow: hidden;">
          ${a.image ? `<tr><td><img src="${escapeHtml(a.image)}" alt="${escapeHtml(a.title)}" style="width: 100%; max-height: 200px; object-fit: cover; display: block;" /></td></tr>` : ''}
          <tr>
            <td style="padding: 16px 20px;">
              <p style="margin: 0 0 4px 0; font-size: 12px; color: #6b5b4f;">${escapeHtml(a.date || '')}</p>
              <h3 style="margin: 0 0 8px 0; font-size: 18px; color: #1a1a1a; font-weight: 700;">${escapeHtml(a.title)}</h3>
              <p style="margin: 0 0 12px 0; font-size: 14px; color: #333; line-height: 1.6;">${escapeHtml(a.excerpt || '')}</p>
              <a href="${escapeHtml(a.url)}" style="display: inline-block; padding: 8px 16px; background: #8b1a1a; color: #ffffff; text-decoration: none; border-radius: 4px; font-size: 13px; font-weight: 600;">קראו עוד ←</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  `).join('');

  return `<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="margin: 0; padding: 0; background: #f5f0e8; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; direction: rtl;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background: #f5f0e8; padding: 24px 16px;">
    <tr>
      <td align="center">
        <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; width: 100%;">
          <!-- Header -->
          <tr>
            <td style="padding: 24px; text-align: center; background: #1a1714; border-radius: 8px 8px 0 0;">
              <h1 style="margin: 0; font-size: 24px; color: #ffffff; font-weight: 800;">חדשות מלפני מאה</h1>
              <p style="margin: 4px 0 0 0; font-size: 13px; color: #d4c9b8;">ההיסטוריה חוזרת ∞</p>
            </td>
          </tr>
          <!-- Intro -->
          <tr>
            <td style="padding: 24px 24px 16px 24px; background: #ffffff; border-right: 1px solid #d4c9b8; border-left: 1px solid #d4c9b8;">
              <p style="margin: 0; font-size: 15px; color: #1a1a1a; line-height: 1.8; white-space: pre-line;">${escapeHtml(intro)}</p>
            </td>
          </tr>
          <!-- Articles -->
          <tr>
            <td style="padding: 8px 24px 24px 24px; background: #ffffff; border-right: 1px solid #d4c9b8; border-left: 1px solid #d4c9b8;">
              <table width="100%" cellpadding="0" cellspacing="0" border="0">
                ${articleCards}
              </table>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="padding: 20px 24px; background: #1a1714; border-radius: 0 0 8px 8px; text-align: center;">
              <p style="margin: 0 0 8px 0; font-size: 12px; color: #d4c9b8;">
                <a href="https://milifney100.com" style="color: #d4c9b8; text-decoration: underline;">לאתר</a> · 
                <a href="https://x.com/Milifney100" style="color: #d4c9b8; text-decoration: underline;">X/Twitter</a> · 
                <a href="https://t.me/milifney100" style="color: #d4c9b8; text-decoration: underline;">Telegram</a>
              </p>
              <p style="margin: 0; font-size: 11px; color: #8a7f72;">
                <a href="{{ unsubscribe }}" style="color: #8a7f72; text-decoration: underline;">הסרה מרשימת התפוצה</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>`;
}

// ─── Helpers ───────────────────────────────────────────────────────────────────

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function brevoRequest(env, method, path, body) {
  return fetch(`https://api.brevo.com/v3${path}`, {
    method,
    headers: {
      'api-key': env.BREVO_API_KEY,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: method !== 'GET' ? JSON.stringify(body) : undefined,
  });
}

function jsonResponse(data, status, corsHeaders) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders },
  });
}
