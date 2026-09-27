// nimo dispatch worker — Cloudflare Workers (free tier).
//
// Why this exists: a static page cannot hold a GitHub token, so the
// "Run test" button POSTs here, and this worker calls workflow_dispatch
// with a token stored as a Worker secret. The token never reaches the browser.
//
// Deploy (one time, on YOUR Cloudflare account):
//   npm i -g wrangler
//   wrangler login
//   cd worker && wrangler deploy
//   wrangler secret put GH_PAT        # classic PAT, scope: repo (or fine-grained: Actions write + Contents read on datum-router/nimo)
//   # set WORKER_URL in docs/run.html to the printed *.workers.dev URL

const REPO = "datum-router/nimo";
const WORKFLOW = "redroid-test.yml";

const VERSIONS = ["16.0.0","15.0.0","14.0.0","13.0.0","12.0.0","11.0.0","10.0.0","9.0.0","8.1.0"];
const ARCHES = ["amd64","arm64"];
const MODES = ["pipeline","repro","discover"];
const DEMOS = ["01-notepad","02-atimetracker","03-asciicam","04-comicviewer","05-kiss"];

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};
const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json", ...cors } });

export default {
  async fetch(req, env) {
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });
    if (req.method !== "POST") return json({ error: "POST only" }, 405);
    if (!env.GH_PAT) return json({ error: "server misconfigured: GH_PAT missing" }, 500);

    let b;
    try { b = await req.json(); } catch { return json({ error: "invalid JSON" }, 400); }

    const version = String(b.android_version || "13.0.0");
    const arch = String(b.arch || "amd64");
    const mode = String(b.mode || "pipeline");
    const apk = String(b.apk_source || "01-notepad").slice(0, 500);
    const bug = String(b.bug_report || "").slice(0, 20000);
    const minutes = Math.min(60, Math.max(5, parseInt(b.max_minutes || "20", 10) || 20));

    if (!VERSIONS.includes(version)) return json({ error: "bad android_version" }, 400);
    if (!ARCHES.includes(arch)) return json({ error: "bad arch" }, 400);
    if (!MODES.includes(mode)) return json({ error: "bad mode" }, 400);
    const isUrl = /^https:\/\//.test(apk);
    if (!isUrl && !DEMOS.includes(apk)) return json({ error: "apk_source must be a demo id or https URL" }, 400);
    if (mode === "repro" && !bug.trim()) return json({ error: "repro mode needs a bug report" }, 400);

    const res = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_PAT}`,
          Accept: "application/vnd.github+json",
          "Content-Type": "application/json",
          "User-Agent": "nimo-dispatch-worker",
        },
        body: JSON.stringify({
          ref: "main",
          inputs: {
            android_version: version,
            arch,
            mode,
            apk_source: apk,
            bug_report: bug,
            max_minutes: String(minutes),
          },
        }),
      }
    );
    if (res.status !== 204) {
      const t = await res.text();
      return json({ error: `github dispatch failed (${res.status}): ${t.slice(0, 200)}` }, 502);
    }
    return json({ ok: true, dispatched_at: new Date().toISOString() });
  },
};
