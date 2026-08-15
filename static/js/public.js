(() => {
  const root = document.getElementById("app");
  if (!root) return;
  const tid = root.dataset.tid;
  let state = null;
  let tab = "home";
  let eventId = null;
  let query = "";
  let eventFilter = "";

  const esc = (s) =>
    String(s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");

  function t() {
    return state.tournament || {};
  }

  function events() {
    return t().events || [];
  }

  function currentEvent() {
    return events().find((e) => e.id === eventId) || state.event || events()[0] || null;
  }

  function loadUrl() {
    return eventId ? `/api/tournaments/${tid}?event_id=${eventId}` : `/api/tournaments/${tid}`;
  }

  async function load() {
    const res = await fetch(loadUrl());
    state = await res.json();
    if (!eventId && state.event) eventId = state.event.id;
    pickTab();
    render();
  }

  function pickTab() {
    const hash = (location.hash || "").replace("#", "");
    if (["home", "draw", "players", "schedule", "results"].includes(hash)) {
      tab = hash;
      return;
    }
    tab = t().status === "completed" ? "results" : "home";
  }

  function setTab(name) {
    tab = name;
    history.replaceState(null, "", `#${name}`);
    render();
  }

  function prettyDate(raw) {
    const m = String(raw || "").match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return raw || "Date TBC";
    const months = [
      "January", "February", "March", "April", "May", "June",
      "July", "August", "September", "October", "November", "December",
    ];
    return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1]}`;
  }

  function personLink(p) {
    if (!p) return "";
    if (p.people && p.people.length) {
      return p.people
        .map((person) => `<a href="/tournaments/${tid}/people/${person.id}">${esc(person.name)}</a>`)
        .join(" / ");
    }
    if (p.person_id) return `<a href="/tournaments/${tid}/people/${p.person_id}">${esc(p.name)}</a>`;
    return `<a href="/tournaments/${tid}/players/${p.id}">${esc(p.name)}</a>`;
  }

  function seedTag(p) {
    return p && p.seed ? `<span class="seed">${p.seed}</span>` : "";
  }

  function slotLabel(match, side) {
    const bye = match[`player${side}_is_bye`];
    const p = match[`player${side}`];
    const src = match[`player${side}_source`];
    if (bye || src === "BYE") return `<span class="bye-label">BYE</span>`;
    if (p) return `${seedTag(p)} <span class="player-name">${personLink(p)}</span>`;
    return `<span class="muted">${esc(src || "TBD")}</span>`;
  }

  function scoreLine(match) {
    if (!match.scores || !match.scores.length) {
      return match.result_type && match.result_type !== "normal" ? match.result_type : "";
    }
    return match.scores.map((g) => `${g[0]}–${g[1]}`).join(", ");
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

  function timeStamp(m) {
    const stamp = m.expected_time || m.scheduled_time;
    if (!stamp) return "";
    const label = m.time_label || (m.time_locked ? "CONFIRMED" : "EXPECTED");
    return `<span class="time-tag">${esc(label)}</span>${esc(stamp)}`;
  }

  function eventPicker() {
    const list = events();
    if (list.length <= 1) return "";
    return `<select class="event-select" id="event-select">
      ${list
        .map((e) => `<option value="${e.id}" ${e.id === eventId ? "selected" : ""}>${esc(e.name)}</option>`)
        .join("")}
    </select>`;
  }

  function header() {
    const tr = t();
    const pill = document.getElementById("status-pill");
    if (pill) {
      pill.textContent = tr.status === "live" ? "LIVE" : tr.status;
      pill.className = `status-pill status-${tr.status}`;
    }
    const ev = currentEvent();
    const meta = [tr.sport, ev && ev.name, ev && (ev.format_label || ev.format)]
      .filter(Boolean)
      .map((s) => String(s).replace(/_/g, " "))
      .join(" · ");
    return `
      <div class="dash-head">
        <div>
          <p class="eyebrow">Public tournament</p>
          <h1>${esc(tr.name)}</h1>
          <p class="dash-kicker">${esc(meta)}</p>
          <p class="muted">${esc(prettyDate(tr.date))}${tr.venue ? " · " + esc(tr.venue) : ""}</p>
        </div>
        <div class="actions">${eventPicker()}</div>
      </div>
      <nav class="tabs">
        ${["home", "draw", "players", "schedule", "results"]
          .map((name) => `<button class="tab ${tab === name ? "active" : ""}" data-tab="${name}">${name}</button>`)
          .join("")}
      </nav>
    `;
  }

  function viewHome() {
    const tr = t();
    const live = state.live || [];
    const upcoming = state.upcoming || [];
    return `
      <div class="summary-grid">
        <div><span>Venue</span><strong>${esc(tr.venue || "TBC")}</strong></div>
        <div><span>Date</span><strong>${esc(prettyDate(tr.date))}</strong></div>
        <div><span>Events</span><strong>${events().map((e) => e.name).join(", ") || "—"}</strong></div>
        <div><span>Status</span><strong>${esc(tr.status)}</strong></div>
      </div>
      ${tr.description ? `<p class="lede">${esc(tr.description)}</p>` : ""}
      <div class="layout">
        <div class="panel">
          <h2>Live now</h2>
          ${live.length ? live.map((m) => `<div class="side-match"><strong>${esc(m.event_name || "")} Match ${m.match_number}</strong><div>${namesOf(m)}</div><div class="muted">${esc(courtName(m) || "Court TBC")}</div></div>`).join("") : `<p class="muted">No live matches.</p>`}
        </div>
        <div class="panel">
          <h2>Up next</h2>
          ${upcoming.length ? upcoming.slice(0, 6).map((m) => `<div class="side-match"><strong>${esc(m.event_name || "")} Match ${m.match_number}</strong><div>${namesOf(m)}</div><div class="muted">${timeStamp(m) || esc(m.status)}</div></div>`).join("") : `<p class="muted">No upcoming matches yet.</p>`}
        </div>
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

  function viewDraw() {
    const tr = t();
    if (!tr.draw) {
      return `<div class="panel"><h2>Draw</h2><p class="muted">The draw for this event has not been published yet.</p></div>`;
    }
    if (tr.format === "round_robin") {
      return `<div class="table-wrap"><table class="data">
        <thead><tr><th>Match</th><th>Round</th><th>Players</th><th>Score</th></tr></thead>
        <tbody>
          ${(state.matches || []).map((m) => `<tr><td>${m.match_number}</td><td>${esc(m.round_name)}</td><td>${namesOf(m)}</td><td>${esc(scoreLine(m))}</td></tr>`).join("")}
        </tbody></table></div>`;
    }
    return `<div class="bracket">
      ${rounds()
        .map((name) => {
          const ms = (state.matches || []).filter((m) => m.round_name === name);
          return `<div class="round"><h3>${esc(name)}</h3>
            <div class="round-matches" style="--count:${ms.length}">
              ${ms
                .map(
                  (m) => `
                <div class="match-wrap">
                  <article class="match-card ${esc(m.status)}${m.winner_id ? " has-winner" : ""}">
                    <div class="slot ${m.winner_id && m.player1 && m.winner_id === m.player1.id ? "winner" : ""}">${slotLabel(m, 1)}</div>
                    <div class="slot ${m.winner_id && m.player2 && m.winner_id === m.player2.id ? "winner" : ""}">${slotLabel(m, 2)}</div>
                    <div class="match-meta"><span>M${m.match_number}</span><span>${esc(scoreLine(m) || "")} ${timeStamp(m)}</span></div>
                  </article>
                </div>`
                )
                .join("")}
            </div></div>`;
        })
        .join("")}
    </div>`;
  }

  function viewPlayers() {
    const players = (t().players || []).filter((p) => {
      const q = query.trim().toLowerCase();
      if (!q) return true;
      return [p.name, p.club, String(p.seed || "")].join(" ").toLowerCase().includes(q);
    });
    return `
      <input class="search" id="player-search" placeholder="Search this event" value="${esc(query)}">
      <div class="table-wrap"><table class="data">
        <thead><tr><th>#</th><th>Entry</th><th>Club</th><th>Seed</th></tr></thead>
        <tbody>
          ${players
            .map(
              (p, i) => `<tr>
                <td>${i + 1}</td>
                <td>${personLink(p)}</td>
                <td>${esc(p.club)}</td>
                <td>${p.seed ?? ""}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table></div>
    `;
  }

  function viewSchedule() {
    const rows = (state.schedule || []).filter((m) => {
      if (eventFilter && String(m.event_id) !== String(eventFilter)) return false;
      return true;
    });
    const list = events();
    return `
      <div class="actions" style="margin-bottom:12px">
        <select id="schedule-event">
          <option value="">All events</option>
          ${list.map((e) => `<option value="${e.id}" ${String(eventFilter) === String(e.id) ? "selected" : ""}>${esc(e.name)}</option>`).join("")}
        </select>
        <span class="muted">Times marked EXPECTED are estimates until an admin confirms them.</span>
      </div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Time</th><th>Event</th><th>Match</th><th>Players</th><th>Court</th><th>Status</th></tr></thead>
        <tbody>
          ${rows
            .map(
              (m) => `<tr>
                <td>${timeStamp(m) || "TBC"}</td>
                <td>${esc(m.event_name)}</td>
                <td>${m.match_number} · ${esc(m.round_name)}</td>
                <td>${namesOf(m)}</td>
                <td>${esc(courtName(m) || "TBC")}</td>
                <td>${esc(m.status)}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table></div>
    `;
  }

  function viewResults() {
    const r = state.results || {};
    return `
      <div class="summary-grid">
        <div><span>Champion</span><strong>${r.champion ? esc(r.champion.name) : "—"}</strong></div>
        <div><span>Runner-up</span><strong>${r.runner_up ? esc(r.runner_up.name) : "—"}</strong></div>
        <div><span>Semifinalists</span><strong>${(r.semifinalists || []).map((p) => p.name).join(", ") || "—"}</strong></div>
      </div>
      <div class="table-wrap"><table class="data">
        <thead><tr><th>Match</th><th>Round</th><th>Result</th></tr></thead>
        <tbody>
          ${(r.matches || []).map((m) => `<tr><td>${m.match_number}</td><td>${esc(m.round_name)}</td><td>${namesOf(m)} · ${esc(scoreLine(m))}</td></tr>`).join("")}
        </tbody>
      </table></div>
    `;
  }

  function render() {
    if (!state || !state.ok) {
      root.innerHTML = `<p class="muted">Could not load tournament.</p>`;
      return;
    }
    const views = { home: viewHome, draw: viewDraw, players: viewPlayers, schedule: viewSchedule, results: viewResults };
    root.innerHTML = header() + (views[tab] || viewHome)();
    bind();
  }

  function bind() {
    root.querySelectorAll("[data-tab]").forEach((el) => el.addEventListener("click", () => setTab(el.dataset.tab)));
    const sel = root.querySelector("#event-select");
    if (sel) {
      sel.addEventListener("change", async () => {
        eventId = Number(sel.value);
        await load();
      });
    }
    const search = root.querySelector("#player-search");
    if (search) {
      search.addEventListener("input", () => {
        query = search.value;
        render();
        const again = root.querySelector("#player-search");
        if (again) {
          again.focus();
          again.setSelectionRange(query.length, query.length);
        }
      });
    }
    const sched = root.querySelector("#schedule-event");
    if (sched) {
      sched.addEventListener("change", () => {
        eventFilter = sched.value;
        render();
      });
    }
  }

  load().catch((err) => {
    root.innerHTML = `<p>${esc(err.message)}</p>`;
  });
})();
