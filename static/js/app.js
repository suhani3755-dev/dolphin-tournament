(() => {
  const root = document.getElementById("app");
  if (!root) return;
  const tid = root.dataset.tid;
  let state = null;
  let tab = "setup";
  let query = "";
  let roundFilter = "";
  let modal = null;

  const $ = (html) => html;
  const esc = (s) =>
    String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  function toast(msg) {
    const el = document.getElementById("toast");
    el.hidden = false;
    el.textContent = msg;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (el.hidden = true), 2800);
  }

  async function api(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      ...opts,
      body: opts.body ? JSON.stringify(opts.body) : opts.raw || undefined,
    });
    const json = await res.json().catch(() => ({ ok: false, error: "Server error" }));
    if (!json.ok) throw new Error(json.error || "Request failed");
    state = json;
    render();
    return json;
  }

  async function load() {
    const res = await fetch(`/api/tournaments/${tid}`);
    state = await res.json();
    pickTab();
    render();
  }

  function t() {
    return state.tournament;
  }

  function pickTab() {
    const hash = (location.hash || "").replace("#", "");
    if (["setup", "players", "draw", "matches", "results", "print"].includes(hash)) {
      tab = hash;
      return;
    }
    const tr = t();
    if (!tr.format || tr.status === "draft") tab = "setup";
    else if (!tr.draw) tab = "players";
    else if (tr.status === "completed") tab = "results";
    else if (tr.status === "live") tab = "draw";
    else tab = "draw";
  }

  function setTab(name) {
    tab = name;
    history.replaceState(null, "", `#${name}`);
    render();
  }

  function playerLink(p) {
    if (!p) return "";
    return `<a href="/tournaments/${tid}/players/${p.id}">${esc(p.name)}</a>`;
  }

  function seedTag(p) {
    return p && p.seed ? `<span class="seed">${p.seed}</span>` : "";
  }

  function slotLabel(match, side) {
    const bye = match[`player${side}_is_bye`];
    const p = match[`player${side}`];
    const src = match[`player${side}_source`];
    if (bye) return `<span class="muted">BYE</span>`;
    if (p) return `${seedTag(p)} <span>${playerLink(p)}</span>`;
    return `<span class="muted">${esc(src || "TBD")}</span>`;
  }

  function scoreLine(match) {
    if (!match.scores || !match.scores.length) return match.result_type && match.result_type !== "normal" ? match.result_type : "";
    return match.scores.map((g) => `${g[0]}–${g[1]}`).join(", ");
  }

  function header() {
    const tr = t();
    document.getElementById("status-pill").textContent = tr.status;
    document.getElementById("status-pill").className = `status-pill status-${tr.status}`;
    return `
      <div class="dash-head">
        <div>
          <p class="eyebrow">${esc(tr.sport)} · ${esc(tr.event_type)} · ${esc(tr.format_label || tr.format)}</p>
          <h1>${esc(tr.name)}</h1>
          <p class="muted">${esc(tr.date || "Date TBC")}${tr.venue ? " · " + esc(tr.venue) : ""}</p>
        </div>
        <div class="actions">
          ${tr.draw && !tr.draw.locked ? `<button class="btn" data-act="lock">Lock Draw</button>` : ""}
          ${tr.draw && tr.draw.locked && tr.status !== "completed" ? `<button class="btn btn-ghost" data-act="unlock">Unlock Draw</button>` : ""}
          ${tr.draw && tr.status !== "live" && tr.status !== "completed" ? `<button class="btn btn-primary" data-act="start">Start Tournament</button>` : ""}
        </div>
      </div>
      <nav class="tabs">
        ${["setup", "players", "draw", "matches", "results", "print"].map(
          (name) => `<button class="tab ${tab === name ? "active" : ""}" data-tab="${name}">${name}</button>`
        ).join("")}
      </nav>
    `;
  }

  function viewSetup() {
    const tr = t();
    return `
      <form id="setup-form" class="form-stack" style="max-width:760px">
        <div class="format-grid">
          ${[
            ["single_elimination", "Single Elimination", "Knockout bracket. First fully supported format."],
            ["round_robin", "Round Robin", "Everyone plays everyone. Standings by wins."],
            ["group_knockout", "Groups → Knockout", "Architecture ready. Not available in this version."],
          ]
            .map(
              ([v, l, d]) => `
            <button type="button" class="format-card ${tr.format === v ? "active" : ""}" data-format="${v}">
              <strong>${l}</strong><small>${d}</small>
            </button>`
            )
            .join("")}
        </div>
        <div class="form-grid">
          <label>Event type
            <select name="event_type">
              <option value="singles" ${tr.event_type === "singles" ? "selected" : ""}>Singles</option>
              <option value="doubles" ${tr.event_type === "doubles" ? "selected" : ""}>Doubles</option>
            </select>
          </label>
          <label>Expected players <span class="opt">optional</span>
            <input name="expected_players" type="number" min="2" value="${esc(tr.expected_players || "")}">
          </label>
        </div>
        <div class="form-grid">
          <label>Best of
            <select name="best_of">
              ${[1, 3, 5].map((n) => `<option value="${n}" ${Number(tr.best_of) === n ? "selected" : ""}>${n}</option>`).join("")}
            </select>
          </label>
          <label>Points per game
            <input name="points_per_game" type="number" min="1" value="${esc(tr.points_per_game)}">
          </label>
        </div>
        <div class="form-grid">
          <label>Win by
            <input name="win_by" type="number" min="1" value="${esc(tr.win_by)}">
          </label>
          <label>Maximum score
            <input name="max_score" type="number" min="1" value="${esc(tr.max_score || "")}" placeholder="30">
          </label>
        </div>
        <div class="form-grid">
          <label>Third-game points <span class="opt">optional</span>
            <input name="third_game_points" type="number" min="1" value="${esc(tr.third_game_points || "")}" placeholder="same as game">
          </label>
          <label>Courts
            <input name="num_courts" type="number" min="1" max="32" value="${esc(tr.num_courts)}">
          </label>
        </div>
        <label class="field"><span><input type="checkbox" name="deuce_enabled" ${tr.deuce_enabled ? "checked" : ""}> Deuce / win-by rules enabled</span></label>
        <h2 class="eyebrow" style="margin-top:8px">Courts & schedule</h2>
        <label class="field"><span><input type="checkbox" name="auto_assign_courts" ${tr.auto_assign_courts !== false ? "checked" : ""}> Auto-assign courts (only as many matches as you have courts; next match takes a court when a winner is entered)</span></label>
        <div class="form-grid">
          <label>Day starts
            <input name="day_start" type="time" value="${esc(tr.day_start || "09:00")}">
          </label>
          <label>Average match (minutes)
            <input name="avg_match_minutes" type="number" min="5" max="180" value="${esc(tr.avg_match_minutes || 25)}">
          </label>
        </div>
        <div class="form-grid">
          <label>Changeover (minutes)
            <input name="changeover_minutes" type="number" min="0" max="30" value="${esc(tr.changeover_minutes ?? 5)}">
          </label>
          <label>Rest after every N waves
            <input name="break_every_waves" type="number" min="0" max="20" value="${esc(tr.break_every_waves ?? 3)}" placeholder="0 = none">
          </label>
        </div>
        <div class="form-grid">
          <label>Rest length (minutes)
            <input name="break_minutes" type="number" min="0" max="120" value="${esc(tr.break_minutes || 15)}">
          </label>
          <label>Lunch starts <span class="opt">optional</span>
            <input name="lunch_start" type="time" value="${esc(tr.lunch_start || "")}">
          </label>
        </div>
        <label>Lunch length (minutes)
          <input name="lunch_minutes" type="number" min="15" max="180" value="${esc(tr.lunch_minutes || 45)}">
        </label>
        <div class="form-actions">
          <button class="btn btn-primary" type="submit">Save setup</button>
          <button class="btn" type="button" data-tab="players">Continue to players</button>
        </div>
      </form>
    `;
  }

  function viewPlayers() {
    const players = (t().players || []).filter((p) => {
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return [p.name, p.club, p.player_code, String(p.seed || "")].join(" ").toLowerCase().includes(q);
    });
    return `
      <div class="actions" style="margin-bottom:12px">
        <input class="search" id="player-search" placeholder="Search players" value="${esc(query)}">
      </div>
      <div class="layout">
        <div>
          <div class="table-wrap">
            <table class="data">
              <thead><tr><th>#</th><th>Player</th><th>Club</th><th>Ranking</th><th>Seed</th><th></th></tr></thead>
              <tbody>
                ${players
                  .map(
                    (p, i) => `
                  <tr>
                    <td>${i + 1}</td>
                    <td><a href="/tournaments/${tid}/players/${p.id}">${esc(p.name)}</a></td>
                    <td>${esc(p.club)}</td>
                    <td>${p.ranking ?? ""}</td>
                    <td><input class="inline seed-input" data-id="${p.id}" type="number" min="1" value="${p.seed ?? ""}"></td>
                    <td><button class="btn btn-ghost" data-del="${p.id}">Remove</button></td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>
        <aside class="panel">
          <h2>Add player</h2>
          <form id="add-player" class="form-stack">
            <label>Name <input name="name" required></label>
            <label>Club / Academy <input name="club"></label>
            <div class="form-grid">
              <label>Ranking <input name="ranking" type="number"></label>
              <label>Seed <input name="seed" type="number" min="1"></label>
            </div>
            <label>Player ID <input name="player_code"></label>
            <label>Contact <input name="contact"></label>
            <button class="btn btn-primary" type="submit">Add player</button>
          </form>
          <h2 style="margin-top:22px">Add several</h2>
          <form id="bulk-player" class="form-stack">
            <textarea name="names" rows="5" placeholder="One per line. Optional: Name, Club"></textarea>
            <button class="btn" type="submit">Add names</button>
          </form>
          <h2 style="margin-top:22px">Import CSV</h2>
          <input id="csv-file" type="file" accept=".csv,text/csv">
          <p class="muted"><a href="/static/player_import_template.csv">Download template</a></p>
          <button class="btn" data-act="seed-rank" type="button">Seed by ranking</button>
          <button class="btn btn-primary" data-tab="draw" type="button">Generate draw →</button>
        </aside>
      </div>
    `;
  }

  function viewDraw() {
    const tr = t();
    const preview = state.preview || {};
    if (!tr.draw) {
      return `
        <div class="panel">
          <h2>Draw summary</h2>
          <div class="summary-grid">
            <div><span>Tournament</span><strong>${esc(preview.tournament)}</strong></div>
            <div><span>Format</span><strong>${esc(preview.format)}</strong></div>
            <div><span>Players</span><strong>${preview.players}</strong></div>
            <div><span>Seeds</span><strong>${preview.seeds}</strong></div>
            <div><span>Bracket size</span><strong>${preview.bracket_size ?? "—"}</strong></div>
            <div><span>Byes</span><strong>${preview.byes ?? 0}</strong></div>
            <div><span>Scoring</span><strong>Best of ${preview.scoring.best_of} · ${preview.scoring.points_per_game} pts</strong></div>
          </div>
          <button class="btn btn-primary" data-act="generate">Generate Draw</button>
        </div>
      `;
    }
    return `
      <div class="layout">
        <div>
          ${tr.draw && !tr.draw.locked ? `<p class="muted">Lock the draw before starting matches. Positions will not change after that.</p>` : ""}
          ${tr.format === "round_robin" ? rrTable() : bracketHtml()}
        </div>
        ${sidePanel()}
      </div>
    `;
  }

  function rounds() {
    const names = [];
    for (const m of state.matches || []) {
      if (!names.includes(m.round_name)) names.push(m.round_name);
    }
    return names;
  }

  function bracketHtml() {
    return `
      <div class="bracket">
        ${rounds()
          .map((name) => {
            const ms = state.matches.filter((m) => m.round_name === name);
            return `<div class="round">
              <h3>${esc(name)}</h3>
              ${ms.map(matchCard).join("")}
            </div>`;
          })
          .join("")}
      </div>
    `;
  }

  function matchCard(m) {
    const w1 = m.winner_id && m.player1 && m.winner_id === m.player1.id;
    const w2 = m.winner_id && m.player2 && m.winner_id === m.player2.id;
    return `
      <article class="match-card ${m.status}" data-match="${m.id}">
        <div class="slot ${w1 ? "winner" : ""}">${slotLabel(m, 1)}</div>
        <div class="slot ${w2 ? "winner" : ""}">${slotLabel(m, 2)}</div>
        <div class="match-meta">
          <span>M${m.match_number} · ${esc(m.status)}${courtName(m) ? " · " + esc(courtName(m)) : ""}</span>
          <span>${esc(scoreLine(m) || m.scheduled_time || "")}</span>
        </div>
      </article>
    `;
  }

  function rrTable() {
    return `<div class="table-wrap"><table class="data">
      <thead><tr><th>Match</th><th>Round</th><th>Players</th><th>Score</th><th></th></tr></thead>
      <tbody>
        ${(state.matches || [])
          .map(
            (m) => `<tr class="clickable" data-match="${m.id}">
              <td>${m.match_number}</td><td>${esc(m.round_name)}</td>
              <td>${m.player1 ? esc(m.player1.name) : ""} vs ${m.player2 ? esc(m.player2.name) : ""}</td>
              <td>${esc(scoreLine(m))}</td>
              <td>${m.status}</td>
            </tr>`
          )
          .join("")}
      </tbody></table></div>`;
  }

  function sidePanel() {
    const live = state.live || [];
    const upcoming = state.upcoming || [];
    const courts = t().courts || [];
    return `
      <aside>
        <div class="panel court-board">
          <h2>Courts</h2>
          ${courts
            .map((c) => {
              const on = live.find((m) => m.court_id === c.id);
              const next = upcoming.find((m) => m.court_id === c.id);
              return `<div class="court-chip">
                <div class="kicker">${esc(c.name)} · ${on ? "LIVE" : next ? "UP NEXT" : "FREE"}</div>
                <div>${on ? namesOf(on) : next ? namesOf(next) : "—"}</div>
                ${on && on.scheduled_time ? `<div class="muted">${esc(on.scheduled_time)}</div>` : next && next.scheduled_time ? `<div class="muted">${esc(next.scheduled_time)}</div>` : ""}
              </div>`;
            })
            .join("")}
        </div>
        ${daySchedule()}
        <div class="panel" style="margin-top:12px">
          <h2>Upcoming matches</h2>
          ${upcoming.length ? upcoming.map((m) => `
            <div class="side-match">
              <strong>Match ${m.match_number}</strong>
              <div>${namesOf(m)}</div>
              <div class="muted">${courtName(m) ? esc(courtName(m)) : "Waiting"} · ${m.scheduled_time ? esc(m.scheduled_time) : esc(m.status)}</div>
              <button class="btn btn-primary" data-enter="${m.id}">Enter result</button>
            </div>`).join("") : `<p class="muted">No matches waiting.</p>`}
        </div>
        ${(state.waiting || []).length ? `
        <div class="panel" style="margin-top:12px">
          <h2>Waiting for a court</h2>
          ${state.waiting.slice(0, 8).map((m) => `
            <div class="side-match">
              <strong>Match ${m.match_number}</strong>
              <div>${namesOf(m)}</div>
              <div class="muted">${m.scheduled_time ? "Est. " + esc(m.scheduled_time) : "Queued"}</div>
            </div>`).join("")}
        </div>` : ""}
      </aside>
    `;
  }

  function daySchedule() {
    const tr = t();
    const matches = (state.matches || []).filter(
      (m) => m.scheduled_time && !m.player1_is_bye && !m.player2_is_bye && m.result_type !== "bye"
    );
    const by = {};
    for (const m of matches) {
      (by[m.scheduled_time] ||= []).push(m);
    }
    const times = Object.keys(by).sort();
    if (!times.length) return "";
    const lunch = tr.lunch_start;
    let lunchDone = false;
    const blocks = [];
    for (const time of times) {
      if (lunch && !lunchDone && time >= lunch) {
        blocks.push(
          `<div class="wave-row break"><div class="kicker">${esc(lunch)}</div><div>Lunch · ${esc(tr.lunch_minutes || 45)} min</div></div>`
        );
        lunchDone = true;
      }
      blocks.push(`<div class="wave-row">
        <div class="kicker">${esc(time)}</div>
        ${by[time]
          .map(
            (m) =>
              `<div>${esc(courtName(m) || "TBD")} · Match ${m.match_number} · ${namesOf(m)}</div>`
          )
          .join("")}
      </div>`);
    }
    const rest =
      tr.break_every_waves > 0
        ? ` · rest ${esc(tr.break_minutes || 15)} min every ${esc(tr.break_every_waves)} waves`
        : "";
    return `<div class="panel" style="margin-top:12px">
      <h2>Day schedule</h2>
      <p class="muted">From ${esc(tr.day_start || "09:00")} · ${esc(tr.avg_match_minutes || 25)} min matches + ${esc(tr.changeover_minutes ?? 5)} min changeover${rest}</p>
      ${blocks.join("")}
    </div>`;
  }

  function courtName(m) {
    if (m.court && m.court.name) return m.court.name;
    const found = (t().courts || []).find((c) => c.id === m.court_id);
    return found ? found.name : "";
  }

  function namesOf(m) {
    const a = m.player1 ? m.player1.name : m.player1_source || "TBD";
    const b = m.player2 ? m.player2.name : m.player2_source || "TBD";
    return `${esc(a)} vs ${esc(b)}`;
  }

  function viewMatches() {
    return `
      <div class="actions" style="margin-bottom:12px">
        <button class="btn btn-primary" data-act="auto-courts">Auto-assign courts</button>
        <span class="muted">${t().auto_assign_courts !== false ? `Only ${t().num_courts || 0} match${t().num_courts === 1 ? "" : "es"} on court at a time. Enter a winner and the next match takes that court.` : "Manual court assignment."}</span>
      </div>
      <div class="layout">
        <div class="table-wrap">
          <table class="data">
            <thead><tr><th>Match</th><th>Round</th><th>Players</th><th>Court</th><th>Time</th><th>Status</th><th></th></tr></thead>
            <tbody>
              ${(state.matches || [])
                .filter((m) => !m.player1_is_bye && !m.player2_is_bye)
                .map((m) => `
                  <tr>
                    <td>${m.match_number}</td>
                    <td>${esc(m.round_name)}</td>
                    <td>${namesOf(m)}</td>
                    <td>
                      <select data-court="${m.id}">
                        <option value="">—</option>
                        ${(t().courts || []).map((c) => `<option value="${c.id}" ${m.court_id === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}
                      </select>
                    </td>
                    <td>${esc(m.scheduled_time || "")}</td>
                    <td>${esc(m.status)}</td>
                    <td class="actions">
                      ${m.status === "ready" && (m.court_id || t().auto_assign_courts === false) ? `<button class="btn" data-start="${m.id}">Start</button>` : ""}
                      ${["ready", "live"].includes(m.status) && m.player1 && m.player2 ? `<button class="btn btn-primary" data-enter="${m.id}">Score</button>` : ""}
                    </td>
                  </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>
        ${sidePanel()}
      </div>
    `;
  }

  function viewResults() {
    const r = state.results || {};
    const rows = (r.matches || []).filter((m) => !roundFilter || m.round_name === roundFilter);
    const roundNames = [...new Set((r.matches || []).map((m) => m.round_name))];
    return `
      <div class="summary-grid">
        <div><span>Champion</span><strong>${r.champion ? esc(r.champion.name) : "—"}</strong></div>
        <div><span>Runner-up</span><strong>${r.runner_up ? esc(r.runner_up.name) : "—"}</strong></div>
        <div><span>Semifinalists</span><strong>${(r.semifinalists || []).map((p) => p.name).join(", ") || "—"}</strong></div>
      </div>
      ${
        r.standings
          ? `<div class="table-wrap"><table class="data"><thead><tr><th>Player</th><th>W</th><th>L</th><th>Played</th></tr></thead><tbody>
          ${r.standings.map((s) => `<tr><td>${esc(s.player.name)}</td><td>${s.wins}</td><td>${s.losses}</td><td>${s.played}</td></tr>`).join("")}
        </tbody></table></div>`
          : ""
      }
      <label>Filter by round
        <select id="round-filter">
          <option value="">All rounds</option>
          ${roundNames.map((n) => `<option value="${esc(n)}" ${roundFilter === n ? "selected" : ""}>${esc(n)}</option>`).join("")}
        </select>
      </label>
      <div class="table-wrap">
        <table class="data">
          <thead><tr><th>Match</th><th>Round</th><th>Result</th></tr></thead>
          <tbody>
            ${rows.map((m) => `<tr><td>${m.match_number}</td><td>${esc(m.round_name)}</td><td>${namesOf(m)} · ${esc(scoreLine(m))}</td></tr>`).join("")}
          </tbody>
        </table>
      </div>
    `;
  }

  function viewPrint() {
    const sheets = (state.matches || [])
      .filter((m) => m.player1 && m.player2 && !m.player1_is_bye)
      .map((m) => `<option value="${m.id}">Match ${m.match_number} · ${esc(m.round_name)}</option>`)
      .join("");
    return `
      <div class="panel">
        <h2>Print</h2>
        <p class="muted">Opens a paper layout. Use your browser’s print dialog (A4).</p>
        <div class="actions">
          <a class="btn" target="_blank" href="/print/${tid}/empty">Empty draw</a>
          <a class="btn" target="_blank" href="/print/${tid}/draw">Current draw</a>
          <a class="btn" target="_blank" href="/print/${tid}/final">Final draw</a>
          <a class="btn" target="_blank" href="/print/${tid}/schedule">Match schedule</a>
          <a class="btn" target="_blank" href="/print/${tid}/players">Player list</a>
          <a class="btn" target="_blank" href="/print/${tid}/results">Results</a>
        </div>
        <form class="form-stack" style="margin-top:16px" onsubmit="return false">
          <label>Match sheet
            <select id="sheet-match">${sheets}</select>
          </label>
          <button class="btn btn-primary" data-act="sheet">Print match sheet</button>
        </form>
      </div>
    `;
  }

  function scoreModal(match) {
    const games = t().best_of || 3;
    const existing = match.scores || [];
    const rows = Array.from({ length: games }, (_, i) => {
      const g = existing[i] || ["", ""];
      return `<div class="score-row">
        <div class="stepper">
          <button type="button" data-step="${i}-0--">−</button>
          <input name="g${i}a" inputmode="numeric" value="${g[0]}">
          <button type="button" data-step="${i}-0-+">+</button>
        </div>
        <span>Game ${i + 1}</span>
        <div class="stepper">
          <button type="button" data-step="${i}-1--">−</button>
          <input name="g${i}b" inputmode="numeric" value="${g[1]}">
          <button type="button" data-step="${i}-1-+">+</button>
        </div>
      </div>`;
    }).join("");
    return `
      <div class="modal-back" id="score-modal">
        <div class="modal">
          <h2>Enter score · Match ${match.match_number}</h2>
          <p>${namesOf(match)}</p>
          <form id="score-form">
            ${rows}
            <label>Result type
              <select name="result_type">
                <option value="normal">Normal</option>
                <option value="walkover">Walkover</option>
                <option value="retirement">Retirement</option>
                <option value="disqualification">Disqualification</option>
                <option value="no_show">No show</option>
              </select>
            </label>
            <p>Winner</p>
            <div class="winner-pick">
              <label class="btn"><input type="radio" name="winner_id" value="${match.player1.id}"> ${esc(match.player1.name)}</label>
              <label class="btn"><input type="radio" name="winner_id" value="${match.player2.id}"> ${esc(match.player2.name)}</label>
            </div>
            <div class="form-actions" style="margin-top:16px">
              <button class="btn btn-ghost" type="button" id="close-modal">Cancel</button>
              <button class="btn btn-primary" type="submit">Save result</button>
            </div>
          </form>
        </div>
      </div>
    `;
  }

  function render() {
    if (!state || !state.ok) {
      root.innerHTML = `<p class="muted">Could not load tournament.</p>`;
      return;
    }
    const views = { setup: viewSetup, players: viewPlayers, draw: viewDraw, matches: viewMatches, results: viewResults, print: viewPrint };
    root.innerHTML = header() + (views[tab] || viewDraw)() + (modal ? scoreModal(modal) : "");
    bind();
  }

  function bind() {
    root.querySelectorAll("[data-tab]").forEach((el) =>
      el.addEventListener("click", () => setTab(el.dataset.tab))
    );
    root.querySelectorAll("[data-format]").forEach((el) =>
      el.addEventListener("click", async () => {
        try {
          await api(`/api/tournaments/${tid}`, { method: "PATCH", body: { format: el.dataset.format } });
        } catch (err) {
          toast(err.message);
        }
      })
    );
    const setup = root.querySelector("#setup-form");
    if (setup) {
      setup.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(setup);
        const body = Object.fromEntries(fd.entries());
        body.deuce_enabled = setup.deuce_enabled.checked;
        body.auto_assign_courts = setup.auto_assign_courts.checked;
        if (body.day_start) body.day_start = String(body.day_start).slice(0, 5);
        if (body.lunch_start) body.lunch_start = String(body.lunch_start).slice(0, 5);
        ["expected_players", "best_of", "points_per_game", "win_by", "max_score", "third_game_points", "num_courts", "avg_match_minutes", "changeover_minutes", "break_every_waves", "break_minutes", "lunch_minutes", "lunch_start"].forEach((k) => {
          if (body[k] === "") body[k] = null;
        });
        try {
          await api(`/api/tournaments/${tid}`, { method: "PATCH", body });
          toast("Setup saved");
          setTab("players");
        } catch (err) {
          toast(err.message);
        }
      });
    }
    const add = root.querySelector("#add-player");
    if (add) {
      add.addEventListener("submit", async (e) => {
        e.preventDefault();
        const body = Object.fromEntries(new FormData(add).entries());
        try {
          await api(`/api/tournaments/${tid}/players`, { method: "POST", body });
          toast("Player added");
        } catch (err) {
          toast(err.message);
        }
      });
    }
    const bulk = root.querySelector("#bulk-player");
    if (bulk) {
      bulk.addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          await api(`/api/tournaments/${tid}/players`, {
            method: "POST",
            body: { bulk: true, names: bulk.names.value },
          });
          toast("Players added");
        } catch (err) {
          toast(err.message);
        }
      });
    }
    const search = root.querySelector("#player-search");
    if (search) {
      search.addEventListener("input", () => {
        query = search.value;
        const active = document.activeElement === search;
        render();
        if (active) {
          const again = root.querySelector("#player-search");
          if (again) {
            again.focus();
            again.setSelectionRange(query.length, query.length);
          }
        }
      });
    }
    root.querySelectorAll(".seed-input").forEach((el) =>
      el.addEventListener("change", async () => {
        try {
          await api(`/api/tournaments/${tid}/players/${el.dataset.id}`, {
            method: "PATCH",
            body: { seed: el.value === "" ? null : Number(el.value) },
          });
        } catch (err) {
          toast(err.message);
        }
      })
    );
    root.querySelectorAll("[data-del]").forEach((el) =>
      el.addEventListener("click", async () => {
        if (!confirm("Remove this player?")) return;
        try {
          await api(`/api/tournaments/${tid}/players/${el.dataset.del}`, { method: "DELETE" });
        } catch (err) {
          toast(err.message);
        }
      })
    );
    const csv = root.querySelector("#csv-file");
    if (csv) {
      csv.addEventListener("change", async () => {
        const file = csv.files[0];
        if (!file) return;
        const text = await file.text();
        try {
          await api(`/api/tournaments/${tid}/players`, { method: "POST", body: { csv: text } });
          toast("Imported");
        } catch (err) {
          toast(err.message);
        }
      });
    }
    root.querySelectorAll("[data-act]").forEach((el) =>
      el.addEventListener("click", () => onAct(el.dataset.act))
    );
    root.querySelectorAll("[data-enter]").forEach((el) =>
      el.addEventListener("click", () => openScore(Number(el.dataset.enter)))
    );
    root.querySelectorAll("[data-match]").forEach((el) =>
      el.addEventListener("click", () => openScore(Number(el.dataset.match)))
    );
    root.querySelectorAll("[data-start]").forEach((el) =>
      el.addEventListener("click", async (e) => {
        e.stopPropagation();
        try {
          await api(`/api/matches/${el.dataset.start}/start`, { method: "POST", body: {} });
        } catch (err) {
          toast(err.message);
        }
      })
    );
    root.querySelectorAll("[data-court]").forEach((el) =>
      el.addEventListener("change", async () => {
        try {
          await api(`/api/matches/${el.dataset.court}`, {
            method: "PATCH",
            body: { court_id: el.value ? Number(el.value) : null },
          });
        } catch (err) {
          toast(err.message);
        }
      })
    );
    const roundSel = root.querySelector("#round-filter");
    if (roundSel) {
      roundSel.addEventListener("change", () => {
        roundFilter = roundSel.value;
        render();
      });
    }
    bindModal();
  }

  async function onAct(act) {
    try {
      if (act === "generate") {
        const played = (state.matches || []).some((m) => m.winner_id && m.result_type !== "bye");
        if (played && !confirm("Changing the draw after matches have started may invalidate existing results.")) return;
        await api(`/api/tournaments/${tid}/draw`, { method: "POST", body: { confirm: true } });
        toast("Draw generated — courts and times assigned");
        setTab("matches");
      } else if (act === "lock") {
        await api(`/api/tournaments/${tid}/draw/lock`, { method: "POST", body: {} });
        toast("Draw locked");
      } else if (act === "unlock") {
        if (!confirm("Unlock draw? Changing the draw after matches have started may invalidate existing results.")) return;
        await api(`/api/tournaments/${tid}/draw/unlock`, { method: "POST", body: { confirm: true } });
      } else if (act === "start") {
        await api(`/api/tournaments/${tid}/start`, { method: "POST", body: {} });
        toast("Tournament live");
        setTab("matches");
      } else if (act === "seed-rank") {
        await api(`/api/tournaments/${tid}/seed-by-ranking`, { method: "POST", body: {} });
        toast("Seeds assigned by ranking");
      } else if (act === "auto-courts") {
        await api(`/api/tournaments/${tid}/courts/auto`, { method: "POST", body: {} });
        toast("Courts and times updated");
      } else if (act === "sheet") {
        const id = document.getElementById("sheet-match").value;
        if (id) window.open(`/print/${tid}/sheet?match=${id}`, "_blank");
      }
    } catch (err) {
      toast(err.message);
    }
  }

  function openScore(id) {
    const match = (state.matches || []).find((m) => m.id === id);
    if (!match || !match.player1 || !match.player2 || match.player1_is_bye || match.player2_is_bye) return;
    if (["completed", "walkover", "retired", "cancelled"].includes(match.status) && match.winner_id) {
      toast("This match is already completed.");
      return;
    }
    modal = match;
    render();
  }

  function bindModal() {
    const back = root.querySelector("#score-modal");
    if (!back) return;
    root.querySelector("#close-modal").addEventListener("click", () => {
      modal = null;
      render();
    });
    back.addEventListener("click", (e) => {
      if (e.target === back) {
        modal = null;
        render();
      }
    });
    back.querySelectorAll("[data-step]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const [gi, side, dir] = btn.dataset.step.split("-");
        const form = back.querySelector("#score-form");
        const input = form.querySelector(`[name=g${gi}${Number(side) === 0 ? "a" : "b"}]`);
        const cur = Number(input.value || 0);
        input.value = Math.max(0, cur + (dir === "+" ? 1 : -1));
      })
    );
    back.querySelector("#score-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const form = e.target;
      const games = [];
      for (let i = 0; i < (t().best_of || 3); i++) {
        const a = form[`g${i}a`].value;
        const b = form[`g${i}b`].value;
        if (a === "" && b === "") continue;
        games.push([a === "" ? 0 : Number(a), b === "" ? 0 : Number(b)]);
      }
      const winner = form.winner_id.value ? Number(form.winner_id.value) : null;
      try {
        await api(`/api/matches/${modal.id}/result`, {
          method: "POST",
          body: { scores: games, result_type: form.result_type.value, winner_id: winner },
        });
        modal = null;
        toast("Winner advanced");
      } catch (err) {
        toast(err.message);
      }
    });
  }

  load().catch((err) => {
    root.innerHTML = `<p>${esc(err.message)}</p>`;
  });
})();
