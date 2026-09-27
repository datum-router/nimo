# nimo dispatch worker

Tiny Cloudflare Worker (free tier) that lets the static nimo site trigger
`redroid-test.yml` without exposing a GitHub token in the browser.

## Deploy

```bash
npm i -g wrangler
wrangler login            # your Cloudflare account
cd worker
wrangler deploy           # prints https://nimo-dispatch.<you>.workers.dev
wrangler secret put GH_PAT
# paste a token with Actions:write on datum-router/nimo
# (classic PAT: `repo` scope. Fine-grained: Actions read+write.)
```

Then set `WORKER_URL` at the top of `docs/run.html` to the workers.dev URL
and re-push. Until then, the run page shows the manual `curl` fallback.

## API

`POST /` with JSON:

```json
{
  "android_version": "13.0.0",
  "arch": "amd64",
  "mode": "pipeline",
  "apk_source": "01-notepad",
  "bug_report": "...",
  "max_minutes": 20
}
```

`apk_source` is a demo id (`01-notepad`, `02-atimetracker`, `03-asciicam`,
`04-comicviewer`, `05-kiss`) or an `https://` URL to an APK.
`mode=repro` requires `bug_report`.

Returns `{ok: true, dispatched_at}` or `{error}`.
