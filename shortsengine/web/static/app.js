// ShortsEngine dashboard - vanilla JS, every view reads live API state.
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtT = (ts) => (ts ? new Date(ts * 1000).toLocaleString() : "");
const STAGES = ["topic", "research", "script", "storyboard", "assets", "voice", "edit", "qc", "metadata", "publish"];
let timer = null, currentView = "overview", selectedJob = null;

async function api(path, opts = {}) {
  const o = { headers: { "Content-Type": "application/json" }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const t = await r.text();
  let d; try { d = t ? JSON.parse(t) : null; } catch { d = t; }
  if (!r.ok) throw new Error((d && d.detail) || r.statusText);
  return d;
}
function toast(msg, isErr) {
  const t = $("#toast"); t.textContent = msg; t.style.display = "block"; t.style.borderColor = isErr ? "var(--err)" : "var(--line)";
  clearTimeout(t._h); t._h = setTimeout(() => (t.style.display = "none"), 4000);
}
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(ok); render(); return r; } catch (e) { toast(e.message, true); } }
const stageChips = (stages) => `<div class="stages">${stages.map((s) => `<span class="stage ${s.status}" title="${esc(s.error || "")}">${s.stage}${s.attempts > 1 ? "×" + s.attempts : ""}</span>`).join("")}</div>`;
const stat = (k, v, cls = "") => `<div class="card stat"><div class="k">${k}</div><div class="v ${cls}">${v}</div></div>`;
function table(rows, cols) {
  if (!rows || !rows.length) return `<div class="card muted">Nothing yet.</div>`;
  return `<div class="tablewrap"><table><thead><tr>${cols.map((c) => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${cols.map((c) => `<td>${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function md(text) { // minimal markdown for research reports
  const lines = text.split("\n"); let html = "", inTable = false, inCode = false;
  for (const l of lines) {
    if (l.startsWith("```")) { html += inCode ? "</pre>" : "<pre>"; inCode = !inCode; continue; }
    if (inCode) { html += esc(l) + "\n"; continue; }
    if (/^\|/.test(l)) {
      if (/^\|[-\s|:]+\|$/.test(l)) continue;
      const cells = l.split("|").slice(1, -1).map((c) => esc(c.trim()).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>"));
      if (!inTable) { html += "<table>"; inTable = true; html += "<tr>" + cells.map((c) => `<th>${c}</th>`).join("") + "</tr>"; }
      else html += "<tr>" + cells.map((c) => `<td>${c}</td>`).join("") + "</tr>";
      continue;
    } else if (inTable) { html += "</table>"; inTable = false; }
    let h = esc(l).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
    if (/^### /.test(l)) html += `<h3>${h.slice(4)}</h3>`; else if (/^## /.test(l)) html += `<h2>${h.slice(3)}</h2>`;
    else if (/^# /.test(l)) html += `<h1>${h.slice(2)}</h1>`; else if (/^[*-] /.test(l)) html += `<div>• ${h.slice(2)}</div>`;
    else if (/^> /.test(l)) html += `<div class="card small">${h.slice(5)}</div>`; else html += h ? `<p>${h}</p>` : "";
  }
  return `<div class="md">${html}${inTable ? "</table>" : ""}</div>`;
}
async function jobPicker(onlyWith) {
  const jobs = await api("/api/jobs?limit=100");
  const list = jobs.filter((j) => !onlyWith || j.stages.find((s) => s.stage === onlyWith && s.status === "done"));
  if (!selectedJob && list.length) selectedJob = list[0].id;
  return { list, html: `<select id="jobSel">${list.map((j) => `<option value="${j.id}" ${j.id == selectedJob ? "selected" : ""}>Job ${j.id} — ${esc(j.moral || "(topic pending)")}</option>`).join("")}</select>` };
}
function bindJobSel() { const s = $("#jobSel"); if (s) s.onchange = () => { selectedJob = +s.value; render(); }; }

const views = {
  async overview() {
    const d = await api("/api/overview");
    return `<h1>Overview</h1><div class="sub">Live production state.</div>
    <div class="row" style="margin-bottom:14px">
      <button class="primary" onclick="act(()=>api('/api/automation',{method:'POST',body:{enabled:${!d.automation_enabled}}}),'Automation ${d.automation_enabled ? "paused" : "started"}')">${d.automation_enabled ? "Pause automation" : "Start automation"}</button>
      <button onclick="act(()=>api('/api/jobs',{method:'POST',body:{run:true}}),'Job created and started')">Produce one Short now</button>
    </div>
    <div class="grid">
      ${stat("Automation", d.automation_enabled ? "Running" : "Paused", d.automation_enabled ? "ok" : "warn")}
      ${stat("Worker", d.worker.alive ? "Alive" : "Not running", d.worker.alive ? "ok" : "err")}
      ${stat("Videos rendered", d.videos.n || 0)}${stat("Uploaded", d.videos.up || 0)}
      ${stat("Topics ready", d.topics_available)}${stat("Avg QC score", d.avg_quality ?? "–")}
      ${stat("Cost today", "$" + d.cost_today.toFixed(3) + " / $" + d.daily_budget)}${stat("Errors (24h)", d.errors_24h, d.errors_24h ? "err" : "")}
    </div>
    <h2>Jobs by status</h2><div class="row">${Object.entries(d.jobs_by_status).map(([k, v]) => `<span class="pill s-${k}">${k}: ${v}</span>`).join("") || '<span class="muted">No jobs yet</span>'}</div>
    <h2>Recent jobs</h2>${table(d.recent_jobs, [["#", (r) => r.id], ["Moral", (r) => esc(r.moral || "")], ["Status", (r) => `<span class="s-${r.status}">${r.status}</span>`],
      ["Stage", (r) => r.current_stage || ""], ["QC", (r) => r.quality_score ?? ""], ["Updated", (r) => fmtT(r.updated_at)], ["Error", (r) => `<span class="err small">${esc((r.error || "").slice(0, 120))}</span>`]])}`;
  },
  async channel() {
    const d = await api("/api/channel");
    const f = d.dna.formats[d.dna.active_format], s = d.summary;
    const eraA = s.A_movie_recap || {}, eraB = s.B_moral_drama || {};
    return `<h1>Channel Intelligence</h1><div class="sub">${esc(d.dna.generated_from.channel)} · ${d.dna.generated_from.videos_analysed} Shorts · snapshot ${esc(d.dna.generated_from.snapshot_utc)}</div>
    <div class="grid">${stat("Subscribers", (s.subscribers || 0).toLocaleString())}${stat("Active format", d.dna.active_format)}
      ${stat("Target duration", f.duration_s.target + " s")}${stat("Mean shot", f.visual.mean_shot_s.target + " s")}
      ${stat("Era B median views", (eraB.views_median || 0).toLocaleString())}${stat("Era A median views", (eraA.views_median || 0).toLocaleString())}</div>
    <h2>Narrative beats (DNA)</h2>${table(f.narrative.structure, [["Beat", (r) => r.beat], ["Start", (r) => Math.round(r.start_pct * 100) + "%"], ["End", (r) => Math.round(r.end_pct * 100) + "%"], ["Notes", (r) => esc(r.notes)]])}
    <h2>Theme evidence</h2>${table(f.narrative.themes_ranked_by_evidence, [["Theme", (r) => r.theme], ["n", (r) => r.n], ["Age-adjusted median", (r) => `<div class="row"><div class="bar" style="width:120px"><i style="width:${Math.max(0, Math.min(100, 50 + r.age_adj_median * 25))}%"></i></div>${r.age_adj_median.toFixed(2)}</div>`]])}
    <h2>Reports</h2><div class="row">${d.reports.map((r) => `<button onclick="showReport('${r}')">${r}</button>`).join("")}</div><div id="report"></div>
    <h2>Video dataset (${d.dataset.length})</h2>${table(d.dataset, [["Era", (r) => r.era[0]], ["Title", (r) => `<a href="https://www.youtube.com/shorts/${r.video_id}" target="_blank">${esc(r.clean_title)}</a>`], ["Published", (r) => (r.published || "").slice(0, 10)],
      ["Dur", (r) => r.duration_s], ["Views", (r) => r.views.toLocaleString()], ["Views/day", (r) => Math.round(r.views_per_day).toLocaleString()], ["Like %", (r) => (r.like_rate * 100).toFixed(2)],
      ["Age-adj", (r) => r.age_adjusted_score], ["Tier", (r) => r.performance_tier], ["Theme", (r) => r.hook_category], ["Setting", (r) => r.setting || ""]])}`;
  },
  async research() {
    const [t, jobs] = await Promise.all([api("/api/trends").catch(() => []), api("/api/jobs?limit=30")]);
    const withR = jobs.filter((j) => j.stages.find((s) => s.stage === "research" && s.status === "done"));
    let detail = "";
    if (withR.length) {
      const id = selectedJob && withR.find((j) => j.id == selectedJob) ? selectedJob : withR[0].id;
      const r = await api(`/api/jobs/${id}/stage/research`);
      detail = `<h2>Fact-check for job ${id}</h2><div class="sub">Fact angle: ${esc(r.fact_angle.fact)}</div>
      ${table(r.claims, [["Id", (c) => c.id], ["Claim", (c) => esc(c.claim)], ["Verified", (c) => c.verified ? '<span class="ok">yes</span>' : `<span class="err">no</span> <span class="small muted">${esc(c.reason || "")}</span>`],
        ["Quote (verbatim from source)", (c) => `<span class="small">${esc(c.quote)}</span>`], ["Source", (c) => c.permalink ? `<a href="${c.permalink}" target="_blank">${esc(c.page_title)}</a>` : esc(c.page_title)]])}
      ${r.contradictions.length ? `<h2>Contradictions</h2><pre>${esc(r.contradictions.join("\n"))}</pre>` : ""}`;
    }
    return `<h1>Research &amp; Trending Topics</h1><div class="sub">Wikipedia most-read (yesterday) is a trend signal; facts are verified against permanent revisions.</div>
    ${detail}<h2>Trending (Wikipedia most read)</h2>${table(t, [["Article", (r) => esc(r.title)], ["Views", (r) => (r.views || 0).toLocaleString()], ["Description", (r) => esc(r.description)]])}`;
  },
  async ideas() {
    const t = await api("/api/topics");
    return `<h1>Content Ideas</h1><div class="sub">Scored candidates (relevance, novelty, audience evidence, curiosity, research reliability, visual potential − overuse).</div>
    <div class="row"><button class="primary" onclick="act(()=>api('/api/topics/discover',{method:'POST',body:{n:8}}),'Discovery started (refresh in a minute)')">Discover new topics</button></div><br>
    ${table(t, [["Score", (r) => (r.score ?? 0).toFixed(3)], ["Moral", (r) => `<b>${esc(r.moral)}</b><div class="small muted">${esc(r.premise)}</div>`], ["Theme / setting", (r) => `${r.theme}<br><span class="muted">${r.setting}</span>`],
      ["Breakdown", (r) => `<span class="small">${Object.entries(r.score_breakdown || {}).map(([k, v]) => `${k}: ${v}`).join("<br>")}</span>`], ["Fact angle", (r) => `<span class="small">${esc(r.fact_angle?.fact || "")}</span>`],
      ["Status", (r) => `<span class="s-${r.status}">${r.status}</span>${r.reject_reason ? `<div class="small err">${esc(r.reject_reason.slice(0, 160))}</div>` : ""}`],
      ["", (r) => r.status === "candidate" ? `<button onclick="act(()=>api('/api/topics/${r.id}/produce',{method:'POST'}),'Production started')">Produce</button> <button class="danger" onclick="act(()=>api('/api/topics/${r.id}/reject',{method:'POST',body:{}}),'Rejected')">Reject</button>` : ""]])}`;
  },
  async script() {
    const p = await jobPicker("script");
    if (!p.list.length) return `<h1>Script Studio</h1><div class="card muted">No scripts yet.</div>`;
    const s = await api(`/api/jobs/${selectedJob}/stage/script`);
    const chars = Object.fromEntries(s.characters.map((c) => [c.id, c]));
    return `<h1>Script Studio</h1><div class="row">${p.html}<span class="pill">${s.word_count} words</span><span class="pill">est. ${s.est_duration}s</span></div>
    <h2>Hook options</h2>${table(s.hooks.map((h, i) => ({ ...h, i })), [["", (h) => (h.i === s.recommended_hook ? "★" : "")], ["Moral", (h) => esc(h.moral)], ["Grammar", (h) => h.grammar], ["Score", (h) => JSON.stringify(h.score)], ["Rationale", (h) => `<span class="small">${esc(h.rationale)}</span>`]])}
    <label>POV title bar</label><input id="pov" style="width:100%" value="${esc(s.pov_text)}">
    <h2>Characters</h2>${table(s.characters, [["Id", (c) => c.id], ["Name", (c) => esc(c.name)], ["Role", (c) => c.role], ["Look", (c) => `<span class="small">${esc(c.look)}</span>`], ["Voice", (c) => esc(c.voice_style)]])}
    <h2>Dialogue</h2>${table(s.lines, [["Beat", (l) => `<span class="small">${l.beat}</span>`], ["Speaker", (l) => esc(chars[l.speaker]?.name || l.speaker)], ["Emotion", (l) => l.emotion],
      ["Line", (l) => `<textarea data-id="${l.id}" rows="2">${esc(l.text)}</textarea>`], ["Flags", (l) => `${l.is_reveal ? '<span class="pill warn">reveal</span>' : ""}${l.claim_id ? `<span class="pill ok">${l.claim_id}</span>` : ""}`],
      ["Visual / SFX", (l) => `<span class="small muted">${esc(l.visual_note)} ${l.sfx ? "· " + esc(l.sfx) : ""}</span>`]])}
    <br><button class="primary" onclick="saveScript()">Save edits &amp; re-render from storyboard</button>
    <h2>Fact card</h2><div class="card">${esc(s.fact_card.claim_id)}: ${esc(s.fact_card.text)}</div>`;
  },
  async storyboard() {
    const p = await jobPicker("storyboard");
    if (!p.list.length) return `<h1>Storyboard Editor</h1><div class="card muted">No storyboards yet.</div>`;
    const sb = await api(`/api/jobs/${selectedJob}/stage/storyboard`);
    const assetsByKey = Object.fromEntries(sb.assets.map((a) => [a.key, a]));
    const ass = await api(`/api/assets?job_id=${selectedJob}`);
    const img = Object.fromEntries(ass.map((a) => [a.asset_key, a.id]));
    return `<h1>Storyboard Editor</h1><div class="row">${p.html}<span class="pill">${sb.shots.length} shots</span><span class="pill">${sb.assets.length} unique images</span><span class="pill">DNA mean shot ${sb.mean_shot_s_target}s</span></div><br>
    ${table(sb.shots, [["#", (s) => s.n], ["Time (est.)", (s) => `${s.est_start}–${s.est_end}s`], ["Beat", (s) => `<span class="small">${s.beat}</span>`],
      ["Image", (s) => (img[s.asset_key] ? `<img src="/asset/${img[s.asset_key]}" style="width:60px;border-radius:6px">` : `<span class="muted small">${s.asset_key}</span>`)],
      ["Framing", (s) => `<select data-n="${s.n}" data-k="framing">${["two", "cu", "medium", "wide"].map((f) => `<option ${f === s.framing ? "selected" : ""}>${f}</option>`).join("")}</select>`],
      ["Camera", (s) => `<select data-n="${s.n}" data-k="camera">${["push_in", "push_in_fast", "pan_left", "pan_right"].map((f) => `<option ${f === s.camera ? "selected" : ""}>${f}</option>`).join("")}</select>`],
      ["Transition", (s) => `<select data-n="${s.n}" data-k="transition_in">${["hard_cut", "flash_white", "cross_dissolve"].map((f) => `<option ${f === s.transition_in ? "selected" : ""}>${f}</option>`).join("")}</select>`],
      ["Narration", (s) => `<span class="small">${esc(s.narration)}</span>`], ["SFX", (s) => s.sfx],
      ["Generation prompt", (s) => `<textarea rows="3" data-n="${s.n}" data-k="prompt">${esc(assetsByKey[s.asset_key]?.prompt || "")}</textarea>`]])}
    <br><button class="primary" onclick="saveStoryboard()">Save &amp; re-render</button>`;
  },
  async queue() {
    const jobs = await api("/api/jobs?limit=50");
    return `<h1>Video Generation Queue</h1><div class="sub">Real stage state for every job. Hover a stage for its last error.</div>
    <div class="row"><button class="primary" onclick="act(()=>api('/api/jobs',{method:'POST',body:{run:true}}),'Job started')">New job</button></div><br>
    ${table(jobs, [["#", (j) => j.id], ["Moral", (j) => esc(j.moral || "")], ["Status", (j) => `<span class="s-${j.status}">${j.status}</span>`], ["Stages", (j) => stageChips(j.stages)],
      ["QC", (j) => j.quality_score ?? ""], ["Loops", (j) => j.qc_loops], ["Cost", (j) => "$" + j.cost_usd], ["Error", (j) => `<span class="small err">${esc((j.error || "").slice(0, 160))}</span>`],
      ["", (j) => `<div class="row">${["failed", "paused", "queued"].includes(j.status) ? `<button onclick="act(()=>api('/api/jobs/${j.id}/retry',{method:'POST',body:{}}),'Retrying')">Retry</button>` : ""}
        <select id="rf${j.id}"><option value="">from stage…</option>${STAGES.map((s) => `<option>${s}</option>`).join("")}</select>
        <button onclick="const v=$('#rf${j.id}').value; if(v) act(()=>api('/api/jobs/${j.id}/retry',{method:'POST',body:{from_stage:v}}),'Re-running from '+v)">Re-run</button>
        ${j.status !== "running" && j.status !== "done" ? `<button class="danger" onclick="act(()=>api('/api/jobs/${j.id}/cancel',{method:'POST'}),'Cancelled')">Cancel</button>` : ""}
        <button onclick="showQC(${j.id})">QC</button></div>`]])}<div id="qcbox"></div>`;
  },
  async videos() {
    const v = await api("/api/videos");
    if (!v.length) return `<h1>Rendered Videos</h1><div class="card muted">No rendered videos yet.</div>`;
    return `<h1>Rendered Videos</h1><div class="videos">${v.map((x) => `<div class="card"><video controls preload="metadata" poster="/media/${x.job_id}/thumbnail.jpg" src="/media/${x.job_id}/final.mp4"></video>
      <div style="margin-top:8px"><b>${esc(x.title)}</b></div><div class="small muted">${x.duration_s?.toFixed(1)}s · QC ${x.quality_score} · ${x.publish_status}${x.youtube_id ? ` · <a target="_blank" href="https://youtu.be/${x.youtube_id}">${x.youtube_id}</a>` : ""}</div>
      <div class="row" style="margin-top:8px"><a class="btn" href="/media/${x.job_id}/final.mp4" download>Export MP4</a><a class="btn" href="/media/${x.job_id}/captions.srt">SRT</a></div></div>`).join("")}</div>`;
  },
  async assets() {
    const a = await api("/api/assets");
    return `<h1>Asset Library</h1><div class="sub">Every asset with source, license and usage restrictions. Third-party footage is never used.</div>
    <div class="thumbs">${a.filter((x) => x.kind === "image").slice(0, 60).map((x) => `<div><img src="/asset/${x.id}" title="${esc(x.prompt)}"><div class="small muted">${esc(x.asset_key)}</div></div>`).join("")}</div>
    <h2>Registry</h2>${table(a, [["Job", (r) => r.job_id], ["Key", (r) => esc(r.asset_key)], ["Kind", (r) => r.kind], ["Source", (r) => esc(r.source)], ["License", (r) => `<span class="${r.allowed ? "ok" : "err"}">${esc(r.license)}</span>`],
      ["Restrictions", (r) => `<span class="small">${esc(r.usage_restrictions)}</span>`], ["Scenes", (r) => r.scene], ["Created", (r) => fmtT(r.created_at)]])}`;
  },
  async publishing() {
    const [st, v] = await Promise.all([api("/api/publish/status"), api("/api/videos")]);
    return `<h1>Publishing Manager</h1><div class="sub">Official YouTube Data API v3 with OAuth for your own channel. Uploads default to private.</div>
    <div class="grid">${stat("OAuth client", st.client_secrets_configured ? "Configured" : "Missing", st.client_secrets_configured ? "ok" : "warn")}${stat("Authorised", st.authorized ? "Yes" : "No", st.authorized ? "ok" : "warn")}
      ${stat("Auto-publish", st.auto_publish ? "On" : "Off")}${stat("Default privacy", st.default_privacy)}</div>
    ${!st.authorized ? `<div class="card" style="margin-top:12px">To authorise: set <code>YOUTUBE_CLIENT_SECRETS</code> in <code>.env</code> and run <code>python -m shortsengine youtube-auth</code>.</div>` : ""}
    <h2>Videos</h2>${table(v, [["Title", (x) => esc(x.title)], ["Status", (x) => x.publish_status + (x.publish_error ? `<div class="small err">${esc(x.publish_error.slice(0, 200))}</div>` : "")], ["YouTube", (x) => x.youtube_id || ""],
      ["", (x) => x.youtube_id ? "" : `<select id="pv${x.id}"><option>private</option><option>unlisted</option><option>public</option></select> <button ${st.authorized ? "" : "disabled"} onclick="act(()=>api('/api/videos/${x.id}/publish',{method:'POST',body:{privacy:$('#pv${x.id}').value}}),'Uploaded')">Upload</button>`]])}`;
  },
  async analytics() {
    const a = await api("/api/analytics");
    const s = a.strategy || {};
    return `<h1>Analytics</h1><div class="sub">${esc(s.caveat || "Learning starts once published videos have statistics.")}</div>
    <div class="row"><button onclick="act(()=>api('/api/analytics/refresh',{method:'POST'}),'Analytics refreshed')">Refresh from YouTube</button></div>
    <h2>Own videos</h2>${table(a.videos, [["Title", (r) => esc(r.title)], ["Views", (r) => r.views], ["Likes", (r) => r.likes], ["Comments", (r) => r.comments], ["Avg view %", (r) => r.avg_view_pct ?? "–"], ["Age-adjusted", (r) => (r.score ?? 0).toFixed(2)]])}
    <h2>Learned strategy adjustments</h2><pre>${esc(JSON.stringify({ themes: s.themes, settings: s.settings, hooks: s.hooks, durations: s.durations, pacing: s.pacing, openings: s.openings, retention_drop_beats: s.retention_drop_beats, test_next: s.test_next, n_videos: s.n_videos }, null, 1))}</pre>`;
  },
  async automation() {
    const s = await api("/api/settings");
    const a = s.automation, v = s.video, vo = s.voice;
    const f = (k, val, type = "number", step = "any") => `<label>${k}</label><input data-path="${k}" type="${type}" step="${step}" value="${esc(val)}">`;
    const b = (k, val) => `<label>${k}</label><select data-path="${k}" data-bool="1"><option ${val ? "selected" : ""}>true</option><option ${!val ? "selected" : ""}>false</option></select>`;
    return `<h1>Automation Settings</h1><div class="sub">Validated server-side; changes apply to the next job.</div>
    <div class="grid">
      <div class="card"><b>Schedule</b>${b("automation.enabled", a.enabled)}${f("automation.videos_per_day", a.videos_per_day)}${f("automation.max_videos_per_day", a.max_videos_per_day)}
        <label>automation.run_times_utc (comma separated HH:MM)</label><input data-path="automation.run_times_utc" data-list="1" value="${esc(a.run_times_utc.join(","))}"></div>
      <div class="card"><b>Budgets</b>${f("automation.daily_budget_usd", a.daily_budget_usd)}${f("automation.per_video_budget_usd", a.per_video_budget_usd)}${f("automation.min_topic_score", a.min_topic_score)}
        ${f("automation.max_stage_attempts", a.max_stage_attempts)}${f("automation.max_qc_loops", a.max_qc_loops)}${b("automation.require_fact_anchor", a.require_fact_anchor)}</div>
      <div class="card"><b>Publishing</b>${b("automation.auto_publish", a.auto_publish)}<label>automation.publish_privacy</label><select data-path="automation.publish_privacy">${["private", "unlisted", "public"].map((p) => `<option ${p === a.publish_privacy ? "selected" : ""}>${p}</option>`).join("")}</select>
        ${f("automation.publish_hour_utc", a.publish_hour_utc)}${f("automation.analytics_refresh_hours", a.analytics_refresh_hours)}</div>
      <div class="card"><b>Video</b>${f("video.width", v.width)}${f("video.height", v.height)}${f("video.fps", v.fps)}${f("video.crf", v.crf)}
        <label>video.preset</label><input data-path="video.preset" type="text" value="${esc(v.preset)}">${f("video.motion_strength", v.motion_strength)}
        <label>video.brand_handle</label><input data-path="video.brand_handle" type="text" value="${esc(v.brand_handle)}">${b("video.music_enabled", v.music_enabled)}${f("video.music_duck_db", v.music_duck_db)}${b("video.sfx_enabled", v.sfx_enabled)}
        ${f("video.duration_override_s", v.duration_override_s ?? "")}</div>
      <div class="card"><b>Voice</b><label>voice.provider</label><select data-path="voice.provider">${["kokoro", "edge", "elevenlabs", "openai", "espeak"].map((p) => `<option ${p === vo.provider ? "selected" : ""}>${p}</option>`).join("")}</select>
        ${f("voice.words_per_minute", vo.words_per_minute)}${f("voice.line_gap_s", vo.line_gap_s)}${f("voice.beat_gap_s", vo.beat_gap_s)}${f("voice.pre_reveal_pause_s", vo.pre_reveal_pause_s)}${f("voice.loudness_lufs", vo.loudness_lufs)}
        <label>voice.male_voices</label><input data-path="voice.male_voices" data-list="1" value="${esc(vo.male_voices.join(","))}"><label>voice.female_voices</label><input data-path="voice.female_voices" data-list="1" value="${esc(vo.female_voices.join(","))}">
        <label>voice.kokoro_male_voices</label><input data-path="voice.kokoro_male_voices" data-list="1" value="${esc(vo.kokoro_male_voices.join(","))}">
        <label>voice.kokoro_female_voices</label><input data-path="voice.kokoro_female_voices" data-list="1" value="${esc(vo.kokoro_female_voices.join(","))}">
        <label>voice.pronunciations (JSON)</label><textarea data-path="voice.pronunciations" data-json="1" rows="3">${esc(JSON.stringify(vo.pronunciations))}</textarea></div>
    </div><br><button class="primary" onclick="saveSettings()">Save settings</button>`;
  },
  async providers() {
    const p = await api("/api/providers");
    const f2 = (k, val) => `<label>${k}</label><input data-path="${k}" type="number" value="${esc(val)}">`;
    const sel = (k, opts, cur) => `<label>providers.${k}</label><select data-path="providers.${k}">${opts.map((o) => `<option ${o === cur ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    const s = p.selected;
    const doc = await api("/api/doctor").catch(() => []);
    return `<h1>API &amp; Provider Settings</h1><div class="sub">Default profile is fully local (Ollama + FLUX via mflux + Kokoro): no API keys. Secrets, if any, are read from <code>.env</code> and never shown.</div>
    <div class="row">${p.profiles.map((n) => `<button onclick="act(()=>api('/api/profile',{method:'POST',body:{name:'${n}'}}),'Profile ${n} applied')">Use profile: ${n}</button>`).join("")}</div>
    <h2>Machine check</h2>${table(doc, [["Check", (r) => r.check], ["Status", (r) => (r.ok ? '<span class="ok">OK</span>' : '<span class="err">Needs fix</span>')], ["Detail", (r) => `<span class="small">${esc(r.detail)}</span>`], ["Fix", (r) => `<code class="small">${esc(r.fix)}</code>`]])}
    <h2>Secrets (optional)</h2>
    ${table(Object.entries(p.secrets).map(([k, v]) => ({ k, ...v })), [["Variable", (r) => `<code>${r.k}</code>`], ["Purpose", (r) => r.purpose], ["Configured", (r) => r.configured ? '<span class="ok">yes</span>' : '<span class="muted">no</span>']])}
    <div class="grid" style="margin-top:14px"><div class="card"><b>LLM</b>${sel("llm", p.available.llm, s.llm)}<label>providers.llm_model</label><input data-path="providers.llm_model" type="text" value="${esc(s.llm_model)}">
      ${sel("llm_effort", ["low", "medium", "high", "xhigh", "max"], s.llm_effort)}${sel("llm_fallback", ["none", "ollama", "pollinations", "openai_compatible"], s.llm_fallback)}
      <label>providers.openai_compatible_base_url</label><input data-path="providers.openai_compatible_base_url" type="text" value="${esc(s.openai_compatible_base_url)}">
      <label>providers.openai_compatible_model</label><input data-path="providers.openai_compatible_model" type="text" value="${esc(s.openai_compatible_model)}">
      <label>providers.ollama_base_url</label><input data-path="providers.ollama_base_url" type="text" value="${esc(s.ollama_base_url)}">
      <label>providers.ollama_model</label><input data-path="providers.ollama_model" type="text" value="${esc(s.ollama_model)}">${f2("providers.ollama_num_ctx", s.ollama_num_ctx)}</div>
      <div class="card"><b>Images</b>${sel("image", p.available.image, s.image)}${sel("image_fallback", ["none", "procedural"], s.image_fallback)}<label>providers.image_model</label><input data-path="providers.image_model" type="text" value="${esc(s.image_model)}">
      <label>providers.mflux_model</label><input data-path="providers.mflux_model" type="text" value="${esc(s.mflux_model)}">${f2("providers.mflux_quantize", s.mflux_quantize)}${f2("providers.mflux_steps", s.mflux_steps)}
      <label>providers.mflux_model_path</label><input data-path="providers.mflux_model_path" type="text" value="${esc(s.mflux_model_path)}">
      <label>providers.a1111_base_url</label><input data-path="providers.a1111_base_url" type="text" value="${esc(s.a1111_base_url)}">${f2("providers.a1111_steps", s.a1111_steps)}
      <label>providers.local_image_license</label><input data-path="providers.local_image_license" type="text" value="${esc(s.local_image_license)}"></div>
      <div class="card"><b>Test providers</b><div class="row" style="margin-top:8px">${["llm", "tts", "image", "research"].map((k) => `<button onclick="testProv('${k}')">${k}</button>`).join("")}</div><pre id="provres" class="small">–</pre></div></div>
    <br><button class="primary" onclick="saveSettings()">Save provider settings</button>`;
  },
  async logs() {
    const l = await api("/api/logs?limit=300");
    return `<h1>Logs &amp; Error Monitoring</h1><div class="sub">Workflow events and all warnings/errors (persisted).</div>
    ${table(l, [["Time", (r) => `<span class="small">${fmtT(r.ts)}</span>`], ["Level", (r) => `<span class="${r.level === "ERROR" ? "err" : r.level === "WARNING" ? "warn" : ""}">${r.level}</span>`], ["Job", (r) => r.job_id ?? ""], ["Stage", (r) => r.stage ?? ""],
      ["Message", (r) => `<span class="small">${esc((r.message || "").slice(0, 600))}</span>`]])}`;
  },
  async costs() {
    const c = await api("/api/costs");
    return `<h1>Cost Tracking</h1><div class="grid">${stat("Today", "$" + c.today_usd)}${stat("Daily budget", "$" + c.daily_budget_usd)}${stat("Per-video budget", "$" + c.per_video_budget_usd)}</div>
    <h2>By day</h2>${table(c.by_day, [["Day", (r) => r.d], ["USD", (r) => "$" + r.usd], ["Calls", (r) => r.n]])}
    <h2>By provider</h2>${table(c.by_provider, [["Provider", (r) => r.provider], ["Kind", (r) => r.kind], ["Units", (r) => r.units + " " + r.unit], ["Calls", (r) => r.calls], ["USD", (r) => "$" + r.usd]])}
    <h2>By job</h2>${table(c.by_job, [["Job", (r) => r.job_id], ["Calls", (r) => r.calls], ["USD", (r) => "$" + r.usd]])}`;
  },
};

async function showReport(name) { const r = await api(`/api/channel/report/${name}`); $("#report").innerHTML = `<div class="card">${md(r.markdown)}</div>`; }
async function showQC(id) {
  const d = await api(`/api/jobs/${id}`);
  $("#qcbox").innerHTML = `<h2>Job ${id} — QC ${d.qc ? d.qc.score + (d.qc.passed ? " (passed)" : " (rejected)") : "not run"}</h2>${d.qc ? table(d.qc.checks, [["Check", (c) => c.name], ["Category", (c) => c.category], ["Result", (c) => (c.passed ? '<span class="ok">pass</span>' : '<span class="err">fail</span>')], ["Severity", (c) => c.severity], ["Detail", (c) => `<span class="small">${esc(c.detail)}</span>`], ["Send back", (c) => (c.passed ? "" : c.send_back)]]) : ""}
  <h2>Events</h2>${table(d.events, [["Time", (e) => fmtT(e.ts)], ["Level", (e) => e.level], ["Stage", (e) => e.stage || ""], ["Message", (e) => `<span class="small">${esc(e.message.slice(0, 400))}</span>`]])}`;
}
async function saveScript() {
  const lines = [...document.querySelectorAll("textarea[data-id]")].map((t) => ({ id: t.dataset.id, text: t.value }));
  await act(() => api(`/api/jobs/${selectedJob}/script`, { method: "PUT", body: { lines, pov_text: $("#pov").value } }), "Saved; job will re-render from storyboard");
  await api(`/api/jobs/${selectedJob}/run`, { method: "POST", body: {} }).catch(() => {});
}
async function saveStoryboard() {
  const by = {};
  document.querySelectorAll("[data-n]").forEach((el) => { (by[el.dataset.n] ??= { n: +el.dataset.n })[el.dataset.k] = el.value; });
  await act(() => api(`/api/jobs/${selectedJob}/storyboard`, { method: "PUT", body: { shots: Object.values(by) } }), "Saved");
  await api(`/api/jobs/${selectedJob}/run`, { method: "POST", body: {} }).catch(() => {});
}
async function saveSettings() {
  const patch = {};
  document.querySelectorAll("[data-path]").forEach((el) => {
    let v = el.value;
    if (el.dataset.bool) v = v === "true"; else if (el.dataset.list) v = v.split(",").map((x) => x.trim()).filter(Boolean);
    else if (el.dataset.json) { try { v = JSON.parse(v || "{}"); } catch { v = {}; } }
    else if (el.type === "number") v = v === "" ? null : Number(v);
    const ks = el.dataset.path.split("."); let o = patch; ks.slice(0, -1).forEach((k) => (o = o[k] ??= {})); o[ks.at(-1)] = v;
  });
  await act(() => api("/api/settings", { method: "PUT", body: patch }), "Settings saved");
}
async function testProv(k) { $("#provres").textContent = "testing " + k + "…"; const r = await api("/api/providers/test", { method: "POST", body: { kind: k } }); $("#provres").textContent = JSON.stringify(r, null, 1); }

async function render() {
  document.querySelectorAll("nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === currentView));
  try {
    const active = document.activeElement;
    if (active && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName) && active.closest("#view")) return; // don't clobber edits
    $("#view").innerHTML = await views[currentView]();
    bindJobSel();
  } catch (e) { $("#view").innerHTML = `<div class="card err">${esc(e.message)}</div>`; }
  api("/api/overview").then((d) => { const p = $("#autoState"); p.textContent = d.automation_enabled ? "Automation: running" : "Automation: paused"; p.className = "pill " + (d.automation_enabled ? "ok" : "warn"); }).catch(() => {});
}
document.querySelectorAll("nav a").forEach((a) => (a.onclick = () => { currentView = a.dataset.view; location.hash = currentView; render(); }));
currentView = location.hash.slice(1) || "overview";
render();
timer = setInterval(() => { if (["overview", "queue", "logs", "costs", "videos"].includes(currentView)) render(); }, 6000);
