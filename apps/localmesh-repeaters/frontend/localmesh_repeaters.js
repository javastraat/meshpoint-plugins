/**
 * LocalMesh Repeaters -- sidebar page (Radio section), laid out like the Reticulum Repeaters page: tiles, one row per
 * repeater, and the cards of the selected one. Settings tab (admins): how long to keep the history, hidden repeaters.
 *
 * Data: GET /api/localmesh-repeaters/repeaters; live through the "localmesh_repeater" WebSocket event, plus every 60 s.
 * Nothing is polled over the air: a repeater sends its status every hour by itself.
 */

const LMR_LATE_S = 2.5 * 3600;   // two statuses missed
const LMR_SILENT_S = 6 * 3600;
const LMR_LOW_MV = 3600;

class LocalMeshRepeatersPage {
    constructor() {
        this._root = null;
        this._timer = null;
        this._tab = 'repeaters';
        this._isAdmin = window.meshpointIdentity?.role !== 'viewer';
        this._data = { repeaters: [], settings: {} };
        this._failed = false;
        this._selected = null;
        try { this._selected = Number(localStorage.getItem('meshpoint.lmr.selected')) || null; } catch (_) { /* private mode */ }
        this._onWs = () => this._load();
    }

    mount(rootEl) {
        this._root = rootEl;
        rootEl.innerHTML = `
            <header class="lw-panel__head"><h2 class="lw-panel__title">LocalMesh Repeaters</h2></header>
            <section class="lw-stats" data-lmr-tiles></section>
            <section class="lw-section"><div class="panel">
                <div class="panel__header panel__header--tabs"><div class="lw-tabs" role="tablist">
                    <button class="lw-tab lw-tab--active" type="button" role="tab" data-lmr-tab="repeaters">Repeaters</button>
                    <button class="lw-tab" type="button" role="tab" data-lmr-tab="settings" ${this._isAdmin ? '' : 'hidden'}>Settings</button>
                </div></div>
                <div class="panel__body">
                    <div data-lmr-pane="repeaters">
                        <p class="lw-empty" data-lmr-empty style="display:none"></p>
                        <div class="lmr-wrap" data-lmr-overview></div>
                        <div class="rp-grid" data-lmr-cards></div>
                        <p class="lmr-note" data-lmr-note></p>
                    </div>
                    <div data-lmr-pane="settings" hidden></div>
                </div>
            </div></section>`;
        rootEl.addEventListener('click', (e) => this._onClick(e));
    }

    show() {
        this._load();
        clearInterval(this._timer);
        this._timer = setInterval(() => this._load(), 60000);
        if (window.concentratorWS && !this._wsOn) {
            window.concentratorWS.on('localmesh_repeater', this._onWs);
            this._wsOn = true;
        }
    }

    hide() {
        clearInterval(this._timer);
        this._timer = null;
    }

    async _load() {
        try {
            const r = await fetch('/api/localmesh-repeaters/repeaters', { credentials: 'same-origin' });
            if (!r.ok) throw new Error(String(r.status));
            this._data = await r.json();
            this._failed = false;
        } catch (_) { this._failed = true; }
        if (this._root) this._render();
    }

    _onClick(e) {
        const tab = e.target.closest('[data-lmr-tab]');
        if (tab) { this._switch(tab.dataset.lmrTab); return; }
        const row = e.target.closest('[data-lmr-node]');
        if (row) {
            this._selected = Number(row.dataset.lmrNode);
            try { localStorage.setItem('meshpoint.lmr.selected', String(this._selected)); } catch (_) { /* private mode */ }
            this._render();
            return;
        }
        if (e.target.closest('[data-lmr-save]')) this._save();
    }

    _switch(tab) {
        this._tab = tab;
        this._root.querySelectorAll('[data-lmr-tab]').forEach((b) => b.classList.toggle('lw-tab--active', b.dataset.lmrTab === tab));
        this._root.querySelectorAll('[data-lmr-pane]').forEach((p) => { p.hidden = p.dataset.lmrPane !== tab; });
        if (tab === 'settings') this._renderSettings();   // only when opened, so a reload does not undo what is being typed
    }

    _visible() { return this._data.repeaters.filter((r) => !r.hidden); }

    _render() {
        const reps = this._visible();
        const now = Date.now() / 1000;
        const heard = reps.filter((r) => r.status);
        const fresh = heard.filter((r) => now - r.status.time < LMR_LATE_S);
        const weakest = heard.filter((r) => r.status.battery_mv).sort((a, b) => a.status.battery_mv - b.status.battery_mv)[0];
        const oldest = heard.length ? this._ago(Math.min(...heard.map((r) => r.status.time))) : '--';
        const tile = (label, value, sub = '') => `<div class="stat-card"><div class="stat-card__label">${this._esc(label)}</div>
            <div class="stat-card__value">${this._esc(value)}</div>${sub ? `<div class="stat-card__sub">${this._esc(sub)}</div>` : ''}</div>`;
        this._q('[data-lmr-tiles]').innerHTML = tile('Repeaters', reps.length) + tile('Reporting', `${fresh.length} / ${reps.length}`)
            + tile('Weakest battery', weakest ? this._volt(weakest.status.battery_mv) : '--', weakest ? this._name(weakest) : '')
            + tile('Oldest status', oldest);

        const empty = this._q('[data-lmr-empty]');
        const text = this._failed ? 'Could not load the repeaters.'
            : this._data.localmesh_too_old ? 'The localmesh plugin is too old to pass on what it receives: update it and restart Meshpoint.'
            : this._data.status_key_set === false ? 'No status key: enter one on the LocalMesh page (Settings, Repeater status) to see what the repeaters report.'
            : !heard.length ? 'No status received yet: battery, uptime and so on fill in as they come (one per repeater per hour).' : '';
        empty.textContent = text;
        empty.style.display = text ? '' : 'none';

        const sel = reps.find((r) => r.node === this._selected) || heard[0] || reps.find((r) => r.heard) || reps[0];
        this._q('[data-lmr-overview]').innerHTML = reps.length ? this._overview(reps, sel, now) : '';
        this._q('[data-lmr-cards]').innerHTML = sel ? this._cards(sel, now) : '';
        this._q('[data-lmr-note]').textContent = heard.length
            ? 'As the status packets say; Meshpoint cannot check that they are genuine.' : '';
    }

    _lamp(r, now) {
        const t = Math.max(r.status ? r.status.time : 0, r.heard ? r.heard.last_heard : 0);
        if (!t) return ['', 'not heard'];
        const age = now - t;
        return age < LMR_LATE_S ? ['lmr-lamp--ok', r.status && now - r.status.time < LMR_LATE_S ? 'reporting' : 'heard']
            : age < LMR_SILENT_S ? ['lmr-lamp--late', 'late'] : ['lmr-lamp--bad', 'silent'];
    }

    _restarted(s, now) { return s.restart_at && now - s.restart_at < 86400; }

    _overview(reps, sel, now) {
        const rows = reps.map((r) => {
            const s = r.status;
            const [lamp, word] = this._lamp(r, now);
            const h = r.heard;
            const firmware = this._firmware(r);
            const first = `
                <td>${h ? this._ago(h.last_heard) : '--'}</td>
                <td>${h && h.rssi != null ? `${this._esc(Math.round(h.rssi))} dBm` : '--'}</td>
                <td>${this._esc(this._route(r))}</td>`;
            const cells = first + (s ? `
                <td class="${s.battery_mv && s.battery_mv < LMR_LOW_MV ? 'lmr-warn' : ''}">${this._volt(s.battery_mv)}</td>
                <td>${this._esc(s.noise_floor)} dBm</td>
                <td>${this._esc(s.airtime_pct)} %</td>
                <td>${this._esc((s.neighbors || []).length)} / ${this._esc(s.neighbor_count)}</td>
                <td class="${this._restarted(s, now) ? 'lmr-warn' : ''}">${this._dur(s.uptime_h)}${this._restarted(s, now) ? ' ⚠' : ''}</td>
                <td>${this._esc(firmware || '--')}</td>
                <td>${this._ago(s.time)}</td>` : `${'<td>--</td>'.repeat(5)}<td>${this._esc(firmware || '--')}</td><td>--</td>`);
            return `<tr class="lmr-ov__row${r === sel ? ' lmr-ov__row--sel' : ''}" data-lmr-node="${r.node}">
                <td><span class="lmr-lamp ${lamp}" title="${word}"></span></td>
                <td class="lmr-name">${this._esc(this._name(r))}${r.revoked ? ' <span class="lw-badge lw-badge--undecrypted">revoked</span>' : ''}</td>
                ${cells}</tr>`;
        }).join('');
        return `<table class="lmr-ov"><tr><th></th><th>Repeater</th><th>Last heard</th><th title="of the last hop">Signal</th><th>Route</th><th>Battery</th><th>Noise</th><th>Airtime</th>
            <th title="heard in the last 25 h (10 at most) / known">Neighbours</th><th>Uptime</th><th>Firmware</th><th>Last status</th></tr>${rows}</table>`;
    }

    _cards(r, now) {
        const s = r.status;
        const [lamp, word] = this._lamp(r, now);
        const rows = (list) => list.filter(([, v]) => v != null && v !== '').map(([k, v]) =>
            `<div class="rp-row"><span class="rp-row__k">${this._esc(k)}</span><span class="rp-row__v">${this._esc(v)}</span></div>`).join('')
            || '<div class="rp-row"><span class="rp-row__k">Nothing to show</span></div>';
        const stale = lamp !== 'lmr-lamp--ok' ? ' rp-card--stale' : '';
        const card = (title, body, meta = '') => `<div class="rp-card${stale}">
            <div class="rp-card__head"><span class="rp-card__name">${this._esc(title)}</span><span class="rp-card__meta">${this._esc(meta)}</span></div>
            <div class="rp-card__rows">${body}</div></div>`;
        const main = `<div class="rp-card${stale}">
            <div class="rp-card__head"><span class="rp-card__name">${this._esc(this._name(r))}</span>
                <span class="lmr-lamp ${lamp}" title="${word}"></span></div>
            <div class="rp-card__id">node ${r.node}</div>
            ${s && this._restarted(s, now) ? `<div class="lmr-alert">Restarted about ${this._ago(s.restart_at)}</div>` : ''}
            <div class="rp-card__rows">${rows([
                ['Battery', s && s.battery_mv ? this._volt(s.battery_mv) : null],
                ['Uptime', s ? this._dur(s.uptime_h) : null],
                ['Restarts seen', s ? s.restarts : null],
                ['Firmware', this._firmware(r)],
                ['Last heard', r.heard ? `${this._ago(r.heard.last_heard)}${r.heard.type ? ` (${r.heard.type.replace(/_/g, ' ')})` : ''}` : null],
                ['Packets heard', r.heard && r.heard.count ? this._num(r.heard.count) : null],
                ['Announced', r.heard && r.heard.announced ? this._ago(r.heard.announced) : null],
                ['Region', r.region],
                ['Certificate', r.revoked ? 'revoked' : r.listed ? 'in the server certificates' : 'not in the certificates'],
                ['Status', s ? `received ${this._ago(s.time)}` : 'none received yet'],
            ])}</div></div>`;
        const packets = card('Recent packets', rows((r.recent || []).map((x) => [this._clock(x.time),
            `${x.type.replace(/_/g, ' ')} · ${x.rssi} dBm${x.via && x.via !== r.node ? ` · via ${this._nodeName(x.via)}` : ''}`])));
        if (!s) {
            const hd = r.heard;
            return `<div class="rp-repeater">${main}${hd ? card('Radio', rows([['Reached us', this._route(r)],
                ['Signal of the last hop', hd.rssi != null ? `${hd.rssi} dBm · ${hd.snr} dB` : null]])) : ''}${packets}</div>`;
        }
        const reasons = Object.entries(s.reasons || {}).map(([k, v]) => [k.replace(/_/g, ' '), v >= 255 ? '255 or more' : v]);
        const direct = s.via === r.node;
        const near = [...(s.neighbors || [])].sort((a, b) => b.snr - a.snr)
            .map((n) => [n.name || `node ${n.node}`, `${n.snr} dB`]);
        return `<div class="rp-repeater">${main}
            ${card('Radio', rows([
                ['Noise floor', `${s.noise_floor} dBm`], ['Airtime', `${s.airtime_pct} %`], ['Send queue', s.queue],
                ['Reached us', direct ? 'directly' : s.via != null ? `through ${this._nodeName(s.via)}` : null],
                ['Signal of the last hop', s.rssi != null ? `${s.rssi} dBm · ${s.snr} dB` : null]]))}
            ${card('Traffic', rows([['Packets accepted', this._num(s.rx_ok)], ['Packets refused', this._num(s.rx_rejected)],
                ...reasons.map(([k, v]) => [`Refused: ${k}`, v])]), reasons.length ? 'reasons since its previous status' : '')}
            ${card('Neighbours', rows(near), `${near.length} in the last 25 h · ${s.neighbor_count} known`)}
            ${packets}
        </div>`;
    }

    /** From its last status or announce, whichever is newer. */
    _firmware(r) {
        const h = r.heard || {};
        return r.status && r.status.time >= (h.announced || 0) ? r.status.version : h.version || '';
    }

    _route(r) {
        const h = r.heard;
        return !h ? '--' : h.direct ? 'direct' : h.via && h.via !== r.node ? `via ${this._nodeName(h.via)}` : 'relayed';
    }

    _clock(t) {
        const d = new Date(t * 1000);
        const time = d.toLocaleTimeString();
        return d.toDateString() === new Date().toDateString() ? time : `${d.toLocaleDateString()} ${time}`;
    }

    _nodeName(node) {
        const r = this._data.repeaters.find((x) => x.node === node);
        return r && r.name ? r.name : `node ${node}`;
    }

    _renderSettings() {
        const pane = this._q('[data-lmr-pane="settings"]');
        pane.innerHTML = `<div class="lmr-settings">
            <label class="cfg-field"><span class="cfg-field__label">Keep the history (days)</span>
                <input class="cfg-field__input" type="number" min="1" max="365" value="${this._esc(this._data.settings.keep_days ?? 30)}" data-lmr-keep></label>
            <div><span class="cfg-field__label">Hide from the page</span>
                ${this._data.repeaters.map((r) => `<label class="lmr-hide"><input type="checkbox" value="${r.node}" data-lmr-hide ${r.hidden ? 'checked' : ''}>
                    ${this._esc(this._name(r))}</label>`).join('') || '<p class="lw-empty">No repeaters known yet.</p>'}</div>
            <div><button class="terminal-button terminal-button--primary" type="button" data-lmr-save>Save</button></div>
            <p class="cfg-status" data-lmr-status aria-live="polite"></p></div>`;
    }

    async _save() {
        const status = this._q('[data-lmr-status]');
        const keep = Number(this._q('[data-lmr-keep]').value);
        if (!Number.isInteger(keep) || keep < 1 || keep > 365) {
            status.dataset.kind = 'error';
            status.textContent = 'Days must be 1 to 365.';
            return;
        }
        const shown = new Set(this._data.repeaters.map((r) => r.node));   // a hidden one not in the list stays hidden
        const hidden = [...(this._data.settings.hidden || []).filter((n) => !shown.has(n)),
            ...[...this._root.querySelectorAll('[data-lmr-hide]:checked')].map((b) => Number(b.value))];
        status.dataset.kind = 'pending';
        status.textContent = 'Saving…';
        let ok = false;
        try {
            const r = await fetch('/api/localmesh-repeaters/settings', { method: 'PUT', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keep_days: keep, hidden }) });
            ok = r.ok;
        } catch (_) { /* shown below */ }
        status.dataset.kind = ok ? 'success' : 'error';
        status.textContent = ok ? 'Saved.' : 'Save failed.';
        if (ok) await this._load();
    }

    _name(r) { return r.name || `Repeater ${r.node}`; }
    _volt(mv) { return mv ? `${(mv / 1000).toFixed(2)} V` : '--'; }
    _dur(h) { return h >= 48 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h`; }
    _num(n) { return n == null ? null : Number(n).toLocaleString(); }
    _ago(t) {
        const s = Math.max(0, Math.round(Date.now() / 1000 - t));
        return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.floor(s / 60)} min ago`
            : s < 172800 ? `${Math.floor(s / 3600)} h ago` : `${Math.floor(s / 86400)} days ago`;
    }
    _q(sel) { return this._root.querySelector(sel); }
    _esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}

window.registerSidebarPage({ route: 'localmesh-repeaters', make: () => new LocalMeshRepeatersPage() });
