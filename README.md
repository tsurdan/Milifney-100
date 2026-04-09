# Milifney-100

News website, but news from 100 years ago.  
Auto-synced from [@Milifney100](https://x.com/Milifney100) on X/Twitter.

## How It Works

1. **GitHub Actions** runs a cron job every 6 hours
2. A Python script fetches new tweets from `@Milifney100` via the [FxTwitter API](https://github.com/FxEmbed/FxEmbed) (free, no API key needed)
3. Each tweet becomes a Jekyll post (markdown + downloaded image)
4. The commit triggers **GitHub Pages** to rebuild the static site

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

After that, the cron runs every 6 hours to pick up new tweets. No API key or secrets required.

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
│   ├── fetch_tweets.py          # Tweet fetcher script
│   └── requirements.txt         # Python deps
├── .github/workflows/
│   └── fetch-tweets.yml         # Cron + manual trigger workflow
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
