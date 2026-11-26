# Social media autopost

`.github/workflows/social-media.yml` runs every hour and publishes anything in
`posts/queue.yml` whose date has passed. It also runs when you push a change
to the queue, and you can run it by hand (Actions → Social media autopost →
Run workflow, tick "dry run" to preview).

## Connect accounts

Add these under **Settings → Secrets and variables → Actions**. Only set the
ones for platforms you use; the others are skipped.

| Platform | Secrets |
|---|---|
| LinkedIn | `LINKEDIN_ACCESS_TOKEN`, `LINKEDIN_AUTHOR_URN` (`urn:li:organization:123` for a company page, or `urn:li:person:abc`) |
| Facebook Page | `FACEBOOK_PAGE_ID`, `FACEBOOK_PAGE_TOKEN` |
| X | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` (app needs Read and Write) |
| Bluesky | `BLUESKY_HANDLE`, `BLUESKY_APP_PASSWORD` (create an app password in Bluesky settings) |

Notes:
- LinkedIn tokens expire after about 60 days and need refreshing. If LinkedIn
  rejects the API version, set a repo **variable** `LINKEDIN_VERSION` (YYYYMM).
- X limits posting on its free API tier; check your plan if posts fail.
- X allows 280 characters and Bluesky 300 (including the link).
