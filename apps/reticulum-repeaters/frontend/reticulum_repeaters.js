/**
 * Reticulum Repeaters -- sidebar page (Radio section).
 *
 * Repeaters tab: per added ScotMesh repeater, the same card layout as the
 * built-in MeshCore Repeaters page (core repeaters.css .rp-* classes,
 * loaded globally) -- status, radio, health, sensors, neighbours/routes and
 * trends (core's shared NodeMetricsChart for battery/voltage/temp/humidity/
 * pressure, plus a small own chart for noise and the link pool).
 * Settings tab (admins only): add/remove repeaters, poll intervals, on/off.
 *
 * Data: GET /api/reticulum-repeaters/repeaters (latest snapshots) and
 * .../repeaters/{hash}/history. Live: the "reticulum_repeater" WebSocket
 * event after each poll, plus a 30 s refresh (5 s while one is fetching).
 */

class ReticulumRepeatersPage {
    constructor() {
        this._root = null;
        this._timer = null;
        this._tab = 'repeaters';
        this._isAdmin = window.meshpointIdentity?.role !== 'viewer';
        this._data = { repeaters: [], settings: {} };
        this._charts = {};
        this._selected = this._loadSelected();
        this._onWs = () => this._load();
    }

    mount(rootEl) {
        this._root = rootEl;
        rootEl.innerHTML = `
            <header class="lw-panel__head">
                <h2 class="lw-panel__title">Reticulum Repeaters</h2>
            </header>
            <section class="lw-stats" data-rr-summary></section>
            <section class="lw-section">
                <div class="panel">
                    <div class="panel__header panel__header--tabs">
                        <div class="lw-tabs" role="tablist">
                            <button class="lw-tab lw-tab--active" type="button" role="tab" data-rr-tab="repeaters">Repeaters</button>
                            <button class="lw-tab" type="button" role="tab" data-rr-tab="settings" ${this._isAdmin ? '' : 'hidden'}>Settings</button>
                        </div>
                    </div>
                    <div class="panel__body">
                        <div data-rr-pane="repeaters">
                            <div class="rr-overview-wrap" data-rr-overview></div>
                            <div class="rp-grid" data-rr-grid></div>
                            <p class="lw-empty" data-rr-empty style="display:none"></p>
                        </div>
                        <div data-rr-pane="settings" hidden></div>
                    </div>
                </div>
            </section>
        `;
        rootEl.querySelectorAll('[data-rr-tab]').forEach((b) =>
            b.addEventListener('click', () => this._switch(b.dataset.rrTab)));
        rootEl.addEventListener('click', (e) => this._onClick(e));
    }

    show() {
        this._load();
        this._schedule();
        if (window.concentratorWS) window.concentratorWS.on('reticulum_repeater', this._onWs);
    }

    hide() {
        clearTimeout(this._timer);
        this._timer = null;
        Object.values(this._charts).forEach((c) => c.destroy());
        this._charts = {};
    }

    _schedule() {
        clearTimeout(this._timer);
        const busy = this._data.repeaters.some((r) => r.snapshot?.polling);
        this._timer = setTimeout(() => { this._load(); this._schedule(); }, busy ? 5000 : 30000);
    }

    _switch(tab) {
        this._tab = tab;
        this._root.querySelectorAll('[data-rr-tab]').forEach((b) =>
            b.classList.toggle('lw-tab--active', b.dataset.rrTab === tab));
        this._root.querySelectorAll('[data-rr-pane]').forEach((p) => { p.hidden = p.dataset.rrPane !== tab; });
        if (tab === 'settings') this._renderSettings(true);
    }

    async _load() {
        try {
            const r = await fetch('/api/reticulum-repeaters/repeaters', { credentials: 'same-origin' });
            if (!r.ok) return;
            this._data = await r.json();
        } catch (_) { return; }
        this._renderSummary();
        this._renderGrid();
        if (this._tab === 'settings') this._renderSettings(false);
    }

    // --- Repeaters tab -------------------------------------------------------

    _renderSummary() {
        const el = this._root.querySelector('[data-rr-summary]');
        const reps = this._data.repeaters;
        const ok = reps.filter((r) => r.snapshot?.ok).length;
        const times = reps.map((r) => r.snapshot?.polled_at).filter(Boolean);
        const oldest = times.length ? this._ago(Math.min(...times)) : '--';
        const stat = (l, v) => `<div class="stat-card"><div class="stat-card__label">${this._esc(l)}</div><div class="stat-card__value">${this._esc(v)}</div></div>`;
        el.innerHTML = stat('Repeaters', reps.length) + stat('Reporting', `${ok} / ${reps.length}`) + stat('Oldest poll', oldest);
    }

    _renderGrid() {
        const grid = this._root.querySelector('[data-rr-grid]');
        const empty = this._root.querySelector('[data-rr-empty]');
        const reps = this._data.repeaters;
        const overview = this._root.querySelector('[data-rr-overview]');
        if (!reps.length) {
            grid.innerHTML = '';
            overview.innerHTML = '';
            empty.style.display = '';
            empty.innerHTML = this._isAdmin
                ? 'No repeaters yet. <button type="button" class="lw-link-btn" data-rr-goto-settings>Add one in Settings</button>.'
                : 'No repeaters yet -- an admin can add them in Settings.';
            return;
        }
        empty.style.display = 'none';
        // Overview of all, details of one: the selected repeater (kept per
        // browser), else the first. One block is 2-3 screens tall, so with
        // several repeaters stacking them all made the page hard to use.
        const sel = reps.find((r) => r.hash === this._selected) || reps[0];
        overview.innerHTML = this._overviewHtml(reps, sel.hash);
        Object.values(this._charts).forEach((c) => c.destroy());
        this._charts = {};
        grid.innerHTML = this._card(sel);
        this._renderTrends(sel.hash, sel.snapshot?.polled_at);
    }

    _overviewHtml(reps, selHash) {
        const now = Date.now() / 1000;
        const rows = reps.map((r) => {
            const s = r.snapshot || {};
            const a = s.all || {}, ra = s.radio || {}, h = s.health || {};
            const lamp = this._lampClass(s);
            const recent = s.restart && now - s.restart.at < 86400;
            const name = r.name || s.sys?.name || r.hash.slice(0, 12);
            return `
                <tr class="rr-ov__row${r.hash === selHash ? ' rr-ov__row--sel' : ''}" data-rr-select="${this._esc(r.hash)}"
                    title="${this._esc(s.error || r.hash)}">
                    <td><span class="rr-lamp ${lamp}"></span></td>
                    <td class="rr-ov__name">${this._esc(name)}</td>
                    <td>${a.voltage != null ? `${a.voltage.toFixed(2)} V · ${this._esc(a.percent)}%${a.charging ? ' ▲' : ''}` : '--'}</td>
                    <td>${ra.noise != null ? `${this._esc(ra.noise)} dBm` : '--'}</td>
                    <td>${a.neighbours != null ? this._esc(a.neighbours) : '--'}</td>
                    <td class="${recent ? 'rr-ov__restart' : ''}">${h.reboots != null ? this._esc(h.reboots) : '--'}${recent ? ' ⚠' : ''}</td>
                    <td>${s.polling ? 'Fetching…' : s.polled_at ? this._ago(s.polled_at) : '--'}</td>
                </tr>`;
        }).join('');
        return `<table class="rr-ov">
            <tr><th></th><th>Repeater</th><th>Battery</th><th>Noise</th><th>Neighbours</th><th>Restarts</th><th>Polled</th></tr>
            ${rows}</table>`;
    }

    /** One lamp for the overview row and the card: green = last poll OK,
     * red = it failed (the error is on the card / row tooltip), amber =
     * fetching or not polled yet. */
    _lampClass(s) {
        if (s.polling || !s.polled_at) return 'rr-lamp--busy';
        return s.ok ? 'rr-lamp--ok' : 'rr-lamp--bad';
    }

    _loadSelected() {
        try { return localStorage.getItem('meshpoint.rr.selected') || ''; } catch (_) { return ''; }
    }

    _select(hash) {
        this._selected = hash;
        try { localStorage.setItem('meshpoint.rr.selected', hash); } catch (_) {}
        this._renderGrid();
    }

    _card(r) {
        const s = r.snapshot || {};
        const a = s.all || {}, ra = s.radio || {}, h = s.health || {}, e = s.env, sy = s.sys || {};
        const stale = !s.ok;
        const title = r.name || sy.name || r.hash.slice(0, 12);
        const rows = (list) => list.filter(([, v]) => v != null && v !== '').map(([k, v]) =>
            `<div class="rp-row"><span class="rp-row__k">${this._esc(k)}</span><span class="rp-row__v">${this._esc(v)}</span></div>`).join('')
            || '<div class="rp-row"><span class="rp-row__k">No data yet</span><span class="rp-row__v">--</span></div>';
        const card = (name, meta, body, extra = '') => `
            <div class="rp-card ${extra} ${stale ? 'rp-card--stale' : ''}">
                <div class="rp-card__head"><span class="rp-card__name">${this._esc(name)}</span>
                <span class="rp-card__meta">${this._esc(meta || '')}</span></div>
                <div class="rp-card__rows">${body}</div>
            </div>`;

        const status = s.polling ? 'Fetching…' : s.polled_at ? `polled ${this._ago(s.polled_at)}` : 'not polled yet';
        const restart = s.restart && (Date.now() / 1000 - s.restart.at) < 86400
            ? `<div class="rr-alert">Restarted (#${this._esc(s.restart.to)}, ${this._esc(s.restart.reason || '?')}) ${this._ago(s.restart.at)}</div>` : '';
        const err = s.error ? `<div class="rr-error">${this._esc(s.error)}</div>` : '';
        const refresh = this._isAdmin
            ? `<button type="button" class="terminal-button rr-refresh" data-rr-refresh="${this._esc(r.hash)}" ${s.polling ? 'disabled' : ''}>${s.polling ? 'Fetching…' : 'Refresh now'}</button>` : '';

        const main = `
            <div class="rp-card ${stale ? 'rp-card--stale' : ''}">
                <div class="rp-card__head">
                    <span class="rp-card__name">${this._esc(title)}</span>
                    <span class="rr-lamp ${this._lampClass(s)}" title="${this._esc(s.ok ? 'Reporting' : (s.error || 'not reporting'))}"></span>
                </div>
                <div class="rp-card__id">${this._esc(r.hash)}</div>
                ${restart}
                <div class="rp-card__rows">${rows([
                    ['Battery', a.voltage != null ? `${a.voltage.toFixed(2)} V · ${a.percent}%${a.charging ? ' · charging' : ''}` : null],
                    ['Uptime', a.uptime != null ? this._dur(a.uptime) : null],
                    ['Last reset', a.reset || h.reset],
                    ['Restarts', h.reboots],
                    ['Packets rx / tx', a.rx != null ? `${this._num(a.rx)} / ${this._num(a.tx)}` : null],
                    ['Links', a.links],
                    ['Routes', a.routes],
                    ['Neighbours', a.neighbours],
                    ['Status', status],
                ])}</div>
                ${err}
                <div class="rr-actions">${refresh}</div>
            </div>`;

        const radio = card('Radio', ra.freq ? `${(ra.freq / 1e6).toFixed(3)} MHz` : '', rows([
            ['Bandwidth', ra.bw ? `${ra.bw / 1000} kHz` : null],
            ['SF / CR', ra.sf ? `SF${ra.sf} / CR${ra.cr}` : null],
            ['TX power', ra.txp != null ? `${ra.txp} dBm` : null],
            ['Noise floor', ra.noise != null ? `${ra.noise} dBm` : null],
            ['Last RSSI', ra.rssi != null ? `${ra.rssi} dBm` : null],
            ['Last SNR', ra.snr != null ? `${ra.snr} dB` : null],
            ['Airtime', ra.airtime != null ? `${ra.airtime}%` : null],
        ]));

        const pos = s.pos;
        const health = card('Health', [sy.board, sy.fw].filter(Boolean).join(' · '), rows([
            ['Free RAM', h.free_ram != null ? `${Math.round(h.free_ram / 1024)} kB` : null],
            ['Link pool free', h.free_pool != null ? `${Math.round(h.free_pool / 1024)} kB` : null],
            ['Pool fragmentation', h.pool_fragmented != null ? `${h.pool_fragmented}%` : null],
            ['Links closed (stuck / idle / mem)', h.links_closed_stuck != null ? `${h.links_closed_stuck} / ${h.links_closed_idle} / ${h.links_closed_mem}` : null],
            ['TX failures', h.tx_fail],
            ['Path table', h.paths != null ? `${h.paths} / ${h.paths_max}` : null],
            ['Location', pos ? `${pos.lat}, ${pos.lon}${pos.height ? ` · ${pos.height} m` : ''}` : 'not shared'],
        ]));

        let sensors = '';
        if (e) {
            const power = Array.isArray(e.power) ? e.power.map((p) => [p[0], p[1] != null ? `${p[1]} V · ${p[2]} A` : 'read failed']) : [];
            sensors = card('Sensors', e.sensor || '', rows([
                ['Temperature', e.temp != null ? `${e.temp} °C` : null],
                ['Humidity', e.humidity != null ? `${e.humidity} %` : null],
                ['Pressure', e.pressure != null ? `${e.pressure} hPa` : null],
                ['Altitude', e.altitude != null ? `${e.altitude} m` : null],
                ['MCU temperature', e.mcu_temp != null ? `${e.mcu_temp} °C` : null],
                ...power,
            ]), 'rp-card--sensors');
        }

        const lists = card('Neighbours & routes',
            s.at?.lists ? `updated ${this._ago(s.at.lists)}` : '',
            (() => {
                const names = this._nameMap(s);
                return this._listHtml('Neighbours', s.nbrs, names) + this._listHtml('Routes', s.paths, names)
                    + this._listHtml('Links', s.links, names);
            })(), 'rr-card--lists');

        const trends = `
            <div class="rp-card rp-card--trends">
                <div class="rp-card__head"><span class="rp-card__name">Trends</span>
                <span class="rp-card__meta">tap legend to toggle</span></div>
                <div class="rp-trends-wrap"><canvas data-rr-chart="${this._esc(r.hash)}"></canvas></div>
                <div class="rp-trends-wrap rr-trends2"><canvas data-rr-chart2="${this._esc(r.hash)}"></canvas></div>
                <div class="rp-trends-empty" data-rr-trends-empty="${this._esc(r.hash)}" style="display:none">No history yet.</div>
            </div>`;

        return `<div class="rp-repeater">${main}${radio}${health}${sensors}${lists}${trends}</div>`;
    }

    /** hash prefix -> name for the dest / via cells: the repeater's own
     * neighbour names first (a route's "via" is always one of its
     * neighbours), then the meshpoint's peer roster (the backend's
     * "names", announced names -- a local lookup, no airtime). */
    _nameMap(s) {
        const map = { ...(this._data.names || {}) };
        const n = s.nbrs;
        if (n && Array.isArray(n.cols) && Array.isArray(n.rows)) {
            const di = n.cols.indexOf('dest'), ni = n.cols.indexOf('name');
            if (di >= 0 && ni >= 0) {
                n.rows.forEach((r) => {
                    const name = r[ni];
                    if (name && name !== '--' && typeof r[di] === 'string') map[r[di].toLowerCase()] = name;
                });
            }
        }
        return map;
    }

    _listHtml(label, list, names = {}) {
        if (!list || !Array.isArray(list.rows)) return '';
        const cols = Array.isArray(list.cols) ? list.cols : [];
        const head = cols.length ? `<tr>${cols.map((c) => `<th>${this._esc(c)}</th>`).join('')}</tr>` : '';
        const hasNameCol = cols.includes('name');
        const cell = (c, v, row) => {
            if (v == null || v === '--') {
                // A neighbour the repeater has no name for: try the roster.
                if (c === 'name') {
                    const d = row[cols.indexOf('dest')];
                    const nm = typeof d === 'string' ? names[d.toLowerCase()] : null;
                    if (nm) return `<td class="rr-name">${this._esc(nm)}</td>`;
                }
                return '<td>--</td>';
            }
            if ((c === 'via' || (c === 'dest' && !hasNameCol)) && typeof v === 'string' && names[v.toLowerCase()]) {
                return `<td>${this._esc(v)} <span class="rr-name">${this._esc(names[v.toLowerCase()])}</span></td>`;
            }
            return `<td>${this._esc(v)}</td>`;
        };
        const body = list.rows.slice(0, 15).map((row) => {
            const r = Array.isArray(row) ? row : [row];
            return `<tr>${r.map((v, i) => cell(cols[i], v, r)).join('')}</tr>`;
        }).join('');
        const more = list.total > list.rows.length || list.rows.length > 15 ? ` (showing ${Math.min(15, list.rows.length)} of ${list.total})` : '';
        return `<div class="rr-list"><div class="rr-list__label">${this._esc(label)} · ${this._esc(list.total)}${this._esc(more)}</div>
            <table class="rr-table">${head}${body}</table></div>`;
    }

    async _renderTrends(hash, polledAt) {
        const canvas = this._root.querySelector(`canvas[data-rr-chart="${hash}"]`);
        const canvas2 = this._root.querySelector(`canvas[data-rr-chart2="${hash}"]`);
        if (!canvas) return;
        let samples = [];
        try {
            const r = await fetch(`/api/reticulum-repeaters/repeaters/${hash}/history?hours=720`, { credentials: 'same-origin' });
            if (r.ok) samples = (await r.json()).samples || [];
        } catch (_) {}
        const iso = (ts) => new Date(ts * 1000).toISOString();
        const telemetry = samples.map((s) => ({
            timestamp: iso(s.ts), battery_level: s.percent, voltage: s.voltage, temperature: s.temp,
            humidity: s.humidity, barometric_pressure: s.pressure,
        }));
        let drawn = false;
        if (window.NodeMetricsChart) {
            const chart = new window.NodeMetricsChart(canvas);
            drawn = chart.render({ telemetry, signal: [] });
            if (drawn) this._charts[hash] = chart;
        }
        const drawn2 = this._radioChart(hash, canvas2, samples);
        const empty = this._root.querySelector(`[data-rr-trends-empty="${hash}"]`);
        if (empty) empty.style.display = (drawn || drawn2) ? 'none' : '';
        if (!drawn2 && canvas2) canvas2.parentElement.style.display = 'none';
    }

    /** Noise floor, last RSSI (dBm) and link-pool fragmentation (%) -- not
     * fields NodeMetricsChart knows, so a small Chart.js chart of its own. */
    _radioChart(hash, canvas, samples) {
        if (!canvas || !window.Chart) return false;
        const pts = (k) => samples.filter((s) => s[k] != null).map((s) => ({ x: s.ts * 1000, y: s[k] }));
        const sets = [
            { label: 'Noise (dBm)', data: pts('noise'), yAxisID: 'db', borderColor: '#f59e0b' },
            { label: 'RSSI (dBm)', data: pts('rssi'), yAxisID: 'db', borderColor: '#06b6d4', hidden: true },
            { label: 'Fragmentation (%)', data: pts('frag'), yAxisID: 'pct', borderColor: '#a78bfa' },
        ].filter((d) => d.data.length >= 2);
        if (!sets.length) return false;
        sets.forEach((d) => Object.assign(d, { borderWidth: 1.5, pointRadius: 0, tension: 0.2 }));
        const ink = (window.ChartTheme && window.ChartTheme.ink()) || { fg: '#94a3b8', grid: 'rgba(51,65,85,0.3)' };
        const fmt = (v) => new Date(v).toLocaleString([], { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false });
        this._charts[`${hash}:2`] = new window.Chart(canvas, {
            type: 'line',
            data: { datasets: sets },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: 'nearest', axis: 'x', intersect: false },
                plugins: { legend: { labels: { color: ink.fg, boxWidth: 10 } },
                    tooltip: { callbacks: { title: (items) => items.length ? fmt(items[0].parsed.x) : '' } } },
                scales: {
                    x: { type: 'linear', ticks: { color: ink.fg, maxTicksLimit: 6, callback: fmt }, grid: { color: ink.grid } },
                    db: { position: 'left', ticks: { color: ink.fg }, grid: { color: ink.grid } },
                    pct: { position: 'right', min: 0, max: 100, ticks: { color: ink.fg }, grid: { display: false } },
                },
            },
        });
        return true;
    }

    // --- Settings tab ----------------------------------------------------------

    async _renderSettings(full) {
        const pane = this._root.querySelector('[data-rr-pane="settings"]');
        if (!this._isAdmin || !pane) return;
        const st = this._data.settings || {};
        if (full || !pane.dataset.built) {
            pane.dataset.built = '1';
            pane.innerHTML = `
                <fieldset class="cfg-fieldset">
                    <legend class="cfg-fieldset__legend">Repeaters</legend>
                    <div data-rr-list></div>
                    <p class="cfg-field__hint">Only the repeaters in this list are ever contacted -- nothing is
                        scanned or auto-detected. Adding one fetches everything once, including the 7 days of
                        history the repeater keeps itself.</p>
                    <div class="cfg-row">
                        <label class="cfg-field">
                            <span class="cfg-field__label">Heard NomadNet node</span>
                            <select class="cfg-field__input" data-rr-pick><option value="">Loading…</option></select>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">…or paste a NomadNet address</span>
                            <input class="cfg-field__input" type="text" maxlength="32" placeholder="32 hex characters" data-rr-paste>
                        </label>
                    </div>
                    <button type="button" class="terminal-button" data-rr-add>Add repeater</button>
                    <span class="cfg-field__hint" data-rr-add-msg></span>
                </fieldset>
                <fieldset class="cfg-fieldset">
                    <legend class="cfg-fieldset__legend">Polling</legend>
                    <label class="cfg-field cfg-field--toggle">
                        <input type="checkbox" data-rr-on ${st.polling_enabled !== false ? 'checked' : ''}>
                        <span class="cfg-field__label">Poll the repeaters</span>
                    </label>
                    <div class="cfg-row">
                        <label class="cfg-field cfg-field--narrow">
                            <span class="cfg-field__label">Stats every (min)</span>
                            <input class="cfg-field__input" type="number" min="5" max="1440" value="${this._esc(st.interval_min ?? 15)}" data-rr-int>
                        </label>
                        <label class="cfg-field cfg-field--narrow">
                            <span class="cfg-field__label">Neighbours/routes every (min)</span>
                            <input class="cfg-field__input" type="number" min="15" max="1440" value="${this._esc(st.lists_interval_min ?? 60)}" data-rr-lint>
                        </label>
                    </div>
                    <p class="cfg-field__hint">Stats (battery, radio, health, sensors) are 4 short requests; the
                        lists are longer. Firmware and location are fetched once a day. Every request is LoRa
                        airtime on a shared channel -- 15 / 60 min keeps it under 1 %.</p>
                    <button type="button" class="terminal-button" data-rr-save>Save</button>
                    <span class="cfg-field__hint" data-rr-save-msg></span>
                </fieldset>`;
            this._loadCandidates();
        }
        const list = pane.querySelector('[data-rr-list]');
        const reps = this._data.repeaters;
        list.innerHTML = reps.length ? reps.map((r) => `
            <div class="rp-row">
                <span class="rp-row__k">${this._esc(r.name || '(name after the first poll)')} <code>${this._esc(r.hash)}</code></span>
                <span class="rp-row__v"><button type="button" class="lw-link-btn" data-rr-remove="${this._esc(r.hash)}">Remove</button></span>
            </div>`).join('') : '<p class="lw-empty">No repeaters yet.</p>';
    }

    async _loadCandidates() {
        const sel = this._root.querySelector('[data-rr-pick]');
        if (!sel) return;
        try {
            const r = await fetch('/api/reticulum-repeaters/candidates', { credentials: 'same-origin' });
            const nodes = r.ok ? (await r.json()).nodes || [] : [];
            nodes.sort((x, y) => (x.name || x.hash).localeCompare(y.name || y.hash));
            sel.innerHTML = '<option value="">Choose…</option>' + nodes.map((n) =>
                `<option value="${this._esc(n.hash)}">${this._esc(n.name || n.hash.slice(0, 12))} · ${this._esc(n.hash.slice(0, 8))}</option>`).join('');
        } catch (_) {
            sel.innerHTML = '<option value="">(could not load)</option>';
        }
    }

    async _onClick(e) {
        const t = e.target;
        if (t.closest('[data-rr-goto-settings]')) { this._switch('settings'); return; }
        const pick = t.closest('[data-rr-select]');
        if (pick) { this._select(pick.dataset.rrSelect); return; }
        const refresh = t.closest('[data-rr-refresh]');
        if (refresh) {
            refresh.disabled = true;
            refresh.textContent = 'Fetching…';
            const r = await fetch(`/api/reticulum-repeaters/repeaters/${refresh.dataset.rrRefresh}/refresh`,
                { method: 'POST', credentials: 'same-origin' });
            if (!r.ok) {
                const d = await r.json().catch(() => ({}));
                refresh.textContent = d.detail || `Failed (HTTP ${r.status})`;
                setTimeout(() => this._load(), 4000);
            } else {
                await this._load();
                this._schedule();
            }
            return;
        }
        const remove = t.closest('[data-rr-remove]');
        if (remove) {
            const hash = remove.dataset.rrRemove;
            const rep = this._data.repeaters.find((r) => r.hash === hash) || {};
            const opts = {
                label: `Remove ${rep.name || 'repeater'}`,
                command: hash,
                description: 'Stops polling this repeater and deletes everything stored for it: '
                    + 'its latest data and its whole history (Trends). This cannot be undone; '
                    + 'adding it again starts over from the 7 days the repeater keeps itself.',
            };
            // meshpoint's shared in-app danger confirmation (as on the
            // Channels card); the browser's own dialog only if it's missing.
            const ok = window.confirmModal
                ? await window.confirmModal(opts)
                : confirm(`${opts.label}?\n\n${opts.description}`);
            if (!ok) return;
            await fetch(`/api/reticulum-repeaters/repeaters/${hash}`, { method: 'DELETE', credentials: 'same-origin' });
            await this._load();
            this._renderSettings(false);
            this._loadCandidates();
            return;
        }
        if (t.closest('[data-rr-add]')) {
            const msg = this._root.querySelector('[data-rr-add-msg]');
            const paste = this._root.querySelector('[data-rr-paste]').value.trim().toLowerCase();
            const pick = this._root.querySelector('[data-rr-pick]');
            const hash = paste || pick.value;
            const name = paste ? '' : (pick.selectedOptions[0]?.textContent.split(' · ')[0] || '');
            if (!/^[0-9a-f]{32}$/.test(hash)) { msg.textContent = 'Choose a node or paste a 32-character NomadNet address.'; return; }
            const r = await fetch('/api/reticulum-repeaters/repeaters', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ hash, name }),
            });
            const d = await r.json().catch(() => ({}));
            msg.textContent = r.ok ? 'Added -- fetching everything now (this takes a minute over LoRa).' : (d.detail || `Failed (HTTP ${r.status})`);
            if (r.ok) {
                this._root.querySelector('[data-rr-paste]').value = '';
                await this._load();
                this._renderSettings(false);
                this._loadCandidates();
                this._schedule();
            }
            return;
        }
        if (t.closest('[data-rr-save]')) {
            const msg = this._root.querySelector('[data-rr-save-msg]');
            const body = {
                polling_enabled: this._root.querySelector('[data-rr-on]').checked,
                interval_min: Number(this._root.querySelector('[data-rr-int]').value) || 15,
                lists_interval_min: Number(this._root.querySelector('[data-rr-lint]').value) || 60,
            };
            const r = await fetch('/api/reticulum-repeaters/settings', {
                method: 'PUT', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
            });
            const d = await r.json().catch(() => ({}));
            msg.textContent = r.ok ? 'Saved.' : (Array.isArray(d.detail) ? d.detail.map((x) => x.msg).join('; ') : (d.detail || `Failed (HTTP ${r.status})`));
            if (r.ok) this._load();
        }
    }

    // --- helpers -----------------------------------------------------------------

    _ago(unixS) {
        if (!unixS) return '--';
        const s = Math.max(0, Date.now() / 1000 - unixS);
        if (s < 90) return 'just now';
        if (s < 5400) return `${Math.round(s / 60)} min ago`;
        if (s < 129600) return `${Math.round(s / 3600)} h ago`;
        return `${Math.round(s / 86400)} d ago`;
    }

    _dur(sec) {
        const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
        return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
    }

    _num(n) { return n == null ? '--' : Number(n).toLocaleString(); }

    _esc(s) {
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}

window.registerSidebarPage({
    route: 'reticulum-repeaters',
    make: () => new ReticulumRepeatersPage(),
});
