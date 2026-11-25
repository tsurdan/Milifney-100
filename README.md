# Milifney-100

News website, but news from 100 years ago.  
Auto-synced from [@Milifney100](https://x.com/Milifney100) on X/Twitter.

## How It Works

1. **GitHub Actions** runs a cron job every 6 hours
2. A Python script fetches new tweets from `@Milifney100` via the [FxTwitter API](https://github.com/FxEmbed/FxEmbed) (free, no API key needed)
3. Each tweet becomes a Jekyll post (markdown + downloaded image)
4. If a `GEMINI_API_KEY` repo secret is set, new posts are auto-tagged (1-3 tags from the fixed vocabulary in `_data/tags.yml`) via the Gemini API — optional, the fetch still works without it
5. The commit triggers **GitHub Pages** to rebuild the static site

Everything is 100% free: FxTwitter API (no key) + GitHub Actions (free for public repos) + GitHub Pages (free).

## Setup

### 1. Enable GitHub Pages

1. Go to **Settings → Pages** in this repo
2. Under **Source**, select **Deploy from a branch**
3. Choose the `main` branch, root folder (`/`)
4. Save — your site will be live at `https://<username>.github.io/Milifney-100/`

> If you use a custom domain or a `username.github.io` repo, set `baseurl` in `_config.yml` accordingly.

### 2. Run the First Fetch

1. Go to **Actions → Fetch Tweets**
2. Click **Run workflow** (manual trigger)
3. The script fetches recent tweets, creates markdown posts, and pushes them
4. GitHub Pages will rebuild the site automatically

After that, the cron runs every 6 hours to pick up new tweets. No API key or secrets required (unless you also want auto-tagging — see below).

### 3. (Optional) Enable Tag Auto-Tagging

New posts can be auto-tagged (1-3 tags from the fixed vocabulary in `_data/tags.yml`) using the free-tier Gemini API:

1. Get a free key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Add it to **Settings → Secrets and variables → Actions** as `GEMINI_API_KEY`
3. Existing posts can be backfilled locally with `python scripts/tag_posts.py` (see the script's docstring)

Without this secret, `fetch_tweets.py` still runs normally — it just skips tagging.

### 4. (Optional) Admin panel: post, edit and delete articles from the site

Type `admin` in the site search to open `/admin/`. Three hardcoded Google accounts can sign in and then: **1.** post a new article, **2.** edit an article, **3.** delete (hide, or permanently delete) an article, **4.** send the newsletter.

Writing an article works like tweeting: you give the text (first line = title), an image and its caption. The date is now minus 100 years, and the tags, standfirst, SEO title, entities, stories, sources and URL are produced by the same pipeline as for tweets. Admin-written posts are flagged `manual: true` and are **website only** (never sent to Telegram, Facebook or Instagram).

How it works:

1. `/admin/` signs the admin in with Google and sends the Google ID token to the Cloudflare Worker (`newsletter-worker/`), which verifies it and checks the email against the `ADMIN_EMAILS` allowlist.
2. The Worker commits a request file (`_admin_inbox/*.json`, plus any uploaded images) and dispatches the **Admin Post** workflow.
3. `scripts/admin_apply.py` applies the request with the same code used for tweets (create / edit / hide / restore / hard delete), then the workflow tags and enriches the post, regenerates the site data and pushes. `fetch-tweets.yml` also applies any pending requests, and both workflows share one concurrency group.

One-time setup:

1. **Google OAuth client** — in [Google Cloud Console](https://console.cloud.google.com/apis/credentials) create an OAuth client ID of type *Web application*, with authorized JavaScript origins `https://milifney100.com` and `http://localhost:4000`. Put the client ID in `admin_google_client_id` in `_config.yml` and in `GOOGLE_CLIENT_ID` in `newsletter-worker/wrangler.toml`.
2. **Worker secrets** (from `newsletter-worker/`): `npx wrangler secret put ADMIN_EMAILS` (the three Google emails, comma-separated). Make sure `GITHUB_PAT` has **Contents: Read and write** and **Actions: Read and write**. `ADMIN_PASSWORD` is no longer used and can be removed.
3. Deploy the Worker (`npx wrangler deploy`). Run its tests with `npm test`.

Hard-deleted tweet IDs are listed in `scripts/.deleted_tweet_ids` so `fetch_tweets.py` never recreates them.

### 5. Topic intros, stories and search indexing

Topic pages (`/topics/<entity>/`) can have a hand-written intro in `_data/topic_intros.yml`, and story pages take theirs from `_data/stories.yml`. Write intros in the site's voice: present tense, as of the time of the news, never in hindsight. The first sentence also becomes the page's meta description.

Indexing rules: day pages (`/1926/09/06/`) are always `noindex, follow`. To hide a single post from search (for example an announcement about the project's channels), add `robots: "noindex, follow"` to its front matter. `sitemap.xml` is a sitemap index that points to one sitemap per page type (`sitemap-pages`, `-hubs`, `-archive`, `-posts`), and pages marked `noindex` are left out of it.

Each fetch run also notifies Bing and other IndexNow search engines (`scripts/ping_indexnow.py`). The public key file is `/cfcfbc2649b10760072d46c39a562bb7.txt`, and `scripts/.last_indexnow_post` records the last post sent. Its first run sends every indexable URL once; after that it sends only new posts, and only once they are live.

## Project Structure

```
├── _config.yml                  # Jekyll config
├── _layouts/
│   ├── default.html             # Base HTML layout (RTL, Hebrew fonts)
│   └── post.html                # Single article page
├── _posts/                      # Auto-generated markdown posts
├── assets/
│   ├── css/style.css            # Newspaper-style theme
│   └── images/tweets/           # Downloaded tweet images
├── scripts/
│   ├── fetch_tweets.py          # Tweet fetcher script (also tags new posts, if configured)
│   ├── admin_apply.py           # Applies admin-panel requests (_admin_inbox/) to posts
│   ├── gemini_tagging.py        # Shared Gemini auto-tagging logic
│   ├── tag_posts.py             # One-time local backfill CLI for tagging existing posts
│   └── requirements.txt         # Python deps
├── .github/workflows/
│   ├── fetch-tweets.yml         # Cron + manual trigger workflow
│   └── admin-post.yml           # Applies admin-panel requests, dispatched by the Worker
├── admin.html                   # Admin panel (/admin/)
└── index.html                   # Home page (article grid)
```

## Local Development

```bash
# Install Jekyll
gem install bundler jekyll

# Serve locally
bundle exec jekyll serve
# → http://localhost:4000

# Test the fetcher (no API key needed!)
python scripts/fetch_tweets.py
```
