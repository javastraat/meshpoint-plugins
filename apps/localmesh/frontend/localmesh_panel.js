/**
 * LocalMesh page (sidebar, Networks).
 *
 * Same layout as the DAPNET and Reticulum pages and the same core CSS (lw-* from lorawan.css, cfg-* from
 * configuration.css): a summary card per receiver at the top, then tabs (LM_TABS). Viewers see the read-only tabs;
 * Settings (and later Send) are admin-only, like on the other pages. The open tab is remembered per browser.
 *
 *  - Members:  everyone a receiver knows of (member list, adverts, packets heard)   GET /api/localmesh/members
 *  - Messages: channel messages and direct messages to a receiver                 GET /api/localmesh/messages
 *  - Activity: every packet heard, with the receiver's decision                    GET /api/localmesh/activity
 *  - Receiver: per receiver its board, radio, battery, counters, channels and neighbours  GET /api/localmesh/status
 *  - Settings: the receiver list (localmesh_settings_tab.js)
 *
 * Everything refreshes every 10 s while the page is open; only the open tab's list is fetched.
 */

const LM_TAB_STORE_KEY = 'meshpoint.lmTab';
const LM_TABS = [
    { id: 'members', label: 'Members', admin: false },
    { id: 'messages', label: 'Messages', admin: false },
    { id: 'activity', label: 'Activity', admin: false },
    { id: 'receiver', label: 'Receiver', admin: false },
    { id: 'settings', label: 'Settings', admin: true },
];
const LM_SEARCH_TABS = ['members', 'messages', 'activity'];

// decision -> lw-badge colour (core lorawan.css): delivered/passed on green, own/admin cyan, dropped as normal
// mesh traffic amber, refused for a security reason red
const LM_DECISION_BADGE = {
    forward: 'join', deliver: 'join', member_added: 'data', admin_applied: 'data',
    duplicate: 'rejoin', not_next_hop: 'rejoin', hops_exhausted: 'rejoin', budget_exceeded: 'rejoin',
    path_full: 'rejoin', looped: 'rejoin', out_of_region: 'rejoin', neighbor_rejected: 'rejoin',
    wrong_net: 'rejoin', unknown_type: 'rejoin',
    not_member: 'undecrypted', blocked: 'undecrypted', replay: 'undecrypted', bad_signature: 'undecrypted',
    member_blocked: 'undecrypted',
};

class LocalMeshPanel {
    constructor() {
        this._root = null;
        this._timer = null;
        this._devices = [];
        this._lists = { members: [], messages: [], activity: [] };
        this._search = '';
        this._isAdmin = window.meshpointIdentity?.role !== 'viewer';
        let stored = null;
        try { stored = localStorage.getItem(LM_TAB_STORE_KEY); } catch (_) {}
        this._tab = this._allowed(stored) ? stored : 'members';
        this._settingsTab = null;
    }

    _allowed(id) {
        const tab = LM_TABS.find((t) => t.id === id);
        return Boolean(tab) && (!tab.admin || this._isAdmin);
    }

    mount(rootEl) {
        this._root = rootEl;
        const tabs = LM_TABS.map((t) => `
            <button class="lw-tab" type="button" role="tab" data-lm-tab="${t.id}"
                    ${this._allowed(t.id) ? '' : 'hidden'}>${t.label}</button>`).join('');
        const table = (id, heads) => `
            <div data-lm-view="${id}" hidden>
                <div class="panel__body lw-table-wrap">
                    <table class="lw-table">
                        <thead><tr>${heads.map((h) => `<th${h.startsWith('>') ? ' class="lw-r"' : ''}>${h.replace('>', '')}</th>`).join('')}</tr></thead>
                        <tbody data-lm-body="${id}"></tbody>
                    </table>
                    <p class="lw-empty" data-lm-empty="${id}" style="display:none"></p>
                </div>
            </div>`;
        rootEl.innerHTML = `
            <header class="lw-panel__head">
                <h2 class="lw-panel__title">LocalMesh</h2>
                <div class="lw-panel__actions">
                    <button class="terminal-button" type="button" data-lm-refresh>Refresh</button>
                </div>
            </header>

            <section class="lw-stats" data-lm-cards></section>

            <section class="lw-section">
                <div class="panel">
                    <div class="panel__header panel__header--tabs">
                        <div class="lw-tabs" role="tablist">${tabs}</div>
                        <div class="lw-search-wrap" data-lm-search-wrap hidden>
                            <span class="lw-panel__limit" data-lm-count></span>
                            <input type="text" class="lw-search" data-lm-search placeholder="Search..."
                                   autocomplete="off" spellcheck="false" />
                            <button class="lw-search-clear" data-lm-search-clear title="Clear search" hidden>&times;</button>
                        </div>
                    </div>
                    ${table('members', ['Last heard', 'Name', 'Member', '>Packets', '>Hops', 'Signal', 'Position', 'Status'])}
                    ${table('messages', ['Time', 'Channel', 'From', 'Text', '>Hops', 'Signal'])}
                    ${table('activity', ['Time', 'Type', 'From', '>Counter', '>Hops', 'Path', 'Signal', 'Decision'])}
                    <div data-lm-view="receiver" hidden>
                        <div class="panel__body" data-lm-receivers></div>
                    </div>
                    <div data-lm-view="settings" hidden>
                        <div class="panel__body" data-lm-settings-body></div>
                    </div>
                </div>
            </section>
        `;
        if (window.LocalMeshSettingsTab) {
            this._settingsTab = new window.LocalMeshSettingsTab(rootEl.querySelector('[data-lm-settings-body]'));
        }
        rootEl.querySelector('[data-lm-refresh]').addEventListener('click', () => this._refresh());
        rootEl.querySelectorAll('[data-lm-tab]').forEach((btn) => {
            btn.addEventListener('click', () => this._setTab(btn.dataset.lmTab));
        });
        const search = rootEl.querySelector('[data-lm-search]');
        const clear = rootEl.querySelector('[data-lm-search-clear]');
        search.addEventListener('input', () => {
            this._search = search.value.trim().toLowerCase();
            clear.hidden = !search.value;
            this._renderList();
        });
        clear.addEventListener('click', () => {
            search.value = '';
            this._search = '';
            clear.hidden = true;
            search.focus();
            this._renderList();
        });
        this._applyTab();
    }

    /** Called by the router (via registerSidebarPage) when the page becomes active. */
    show() {
        this._refresh();
        this._timer = setInterval(() => this._refresh(), 10_000);
        if (this._tab === 'settings' && this._settingsTab) this._settingsTab.show();
    }

    /** Called by the router when the page is hidden. */
    hide() {
        clearInterval(this._timer);
        this._timer = null;
    }

    _setTab(tab) {
        if (!this._allowed(tab) || tab === this._tab) return;
        this._tab = tab;
        try { localStorage.setItem(LM_TAB_STORE_KEY, tab); } catch (_) {}
        this._applyTab();
        if (tab === 'settings' && this._settingsTab) this._settingsTab.show();
        if (LM_SEARCH_TABS.includes(tab)) this._loadList(tab);
    }

    _applyTab() {
        this._root.querySelectorAll('[data-lm-tab]').forEach((btn) => {
            const active = btn.dataset.lmTab === this._tab;
            btn.classList.toggle('lw-tab--active', active);
            btn.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        this._root.querySelectorAll('[data-lm-view]').forEach((el) => {
            el.hidden = el.dataset.lmView !== this._tab;
        });
        this._root.querySelector('[data-lm-search-wrap]').hidden = !LM_SEARCH_TABS.includes(this._tab);
        this._renderList();
    }

    async _get(url) {
        try {
            const res = await fetch(url, { credentials: 'same-origin' });
            if (res.ok) return await res.json();
        } catch (_e) { /* keep the last render */ }
        return null;
    }

    async _refresh() {
        const devices = await this._get('/api/localmesh/status');
        if (Array.isArray(devices)) this._devices = devices;
        this._renderCards();
        this._renderReceivers();
        if (LM_SEARCH_TABS.includes(this._tab)) await this._loadList(this._tab);
    }

    async _loadList(tab) {
        const list = await this._get(`/api/localmesh/${tab}`);
        if (Array.isArray(list)) this._lists[tab] = list;
        if (tab === this._tab) this._renderList();
    }

    // --- Members / Messages / Activity ---

    _renderList() {
        const tab = this._tab;
        if (!LM_SEARCH_TABS.includes(tab) || !this._root) return;
        const all = this._lists[tab];
        const rows = this._search
            ? all.filter((r) => LocalMeshPanel.searchText(tab, r).includes(this._search)) : all;
        const row = { members: LocalMeshPanel.memberRow, messages: LocalMeshPanel.messageRow,
            activity: LocalMeshPanel.activityRow }[tab];
        this._root.querySelector(`[data-lm-body="${tab}"]`).innerHTML = rows.map(row).join('');
        const empty = this._root.querySelector(`[data-lm-empty="${tab}"]`);
        empty.style.display = rows.length ? 'none' : '';
        empty.textContent = all.length ? 'Nothing matches the search.' : {
            members: 'No members heard yet.',
            messages: 'No messages yet. The receiver shows channel messages it has the key for, and direct messages to itself.',
            activity: 'No packets heard yet.',
        }[tab];
        this._root.querySelector('[data-lm-count]').textContent = this._search
            ? `${rows.length} of ${all.length}` : `${all.length}`;
    }

    static searchText(tab, r) {
        const parts = tab === 'members' ? [r.name, r.member]
            : tab === 'messages' ? [r.sender, r.member, r.channel_name, r.text]
                : [r.name, r.sender, r.type, r.decision, r.counter];
        return parts.join(' ').toLowerCase();
    }

    static memberRow(m) {
        const esc = LocalMeshPanel.esc;
        const pos = m.lat != null
            ? `<a href="https://www.openstreetmap.org/?mlat=${m.lat}&mlon=${m.lon}#map=15/${m.lat}/${m.lon}"
                  target="_blank" rel="noopener">${m.lat.toFixed(4)}, ${m.lon.toFixed(4)}</a>` : '--';
        const status = m.blocked ? '<span class="lw-badge lw-badge--undecrypted">blocked</span>'
            : m.national ? '<span class="lw-badge lw-badge--data">national</span>' : '';
        return `<tr>
            <td>${LocalMeshPanel.ago(m.last_heard)}</td>
            <td>${esc(m.name) || '--'}</td>
            <td>${m.member}</td>
            <td class="lw-r">${m.count || '--'}</td>
            <td class="lw-r">${m.hops ?? '--'}</td>
            <td>${LocalMeshPanel.signal(m.rssi, m.snr)}</td>
            <td>${pos}</td>
            <td>${status}</td>
        </tr>`;
    }

    static messageRow(m) {
        const esc = LocalMeshPanel.esc;
        const from = m.sender ? `${esc(m.sender)} (${m.member})` : String(m.member);
        const channel = m.direct ? `<span class="lw-badge lw-badge--data">${esc(m.channel_name)}</span>`
            : esc(m.channel_name);
        return `<tr>
            <td title="${m.time ? '' : 'time received by Meshpoint (the board clock is not set)'}">${LocalMeshPanel.clock(m.sort_time)}</td>
            <td>${channel}</td>
            <td>${from}</td>
            <td style="white-space: pre-wrap; overflow-wrap: anywhere">${esc(m.text)}</td>
            <td class="lw-r">${m.hops}</td>
            <td>${LocalMeshPanel.signal(m.rssi, m.snr)}</td>
        </tr>`;
    }

    static activityRow(a) {
        const esc = LocalMeshPanel.esc;
        const from = a.name ? `${esc(a.name)} (${esc(a.sender)})` : esc(a.sender);
        const path = (a.path || []).map((h) => h.node).join(' → ') || (a.via ? `via ${a.via}` : 'direct');
        const badge = LM_DECISION_BADGE[a.decision] || 'rejoin';
        return `<tr>
            <td>${LocalMeshPanel.clock(a.time)}</td>
            <td>${esc(a.type)}</td>
            <td>${from}</td>
            <td class="lw-r">${a.counter}</td>
            <td class="lw-r">${a.hops}</td>
            <td>${esc(path)}</td>
            <td>${LocalMeshPanel.signal(a.rssi, a.snr)}</td>
            <td><span class="lw-badge lw-badge--${badge}">${esc(a.decision.replace(/_/g, ' '))}</span></td>
        </tr>`;
    }

    // --- cards and the Receiver tab ---

    _renderCards() {
        const el = this._root.querySelector('[data-lm-cards]');
        if (!this._devices.length) {
            el.innerHTML = `
                <div class="stat-card">
                    <div class="stat-card__label">Receiver</div>
                    <div class="stat-card__value">Not configured</div>
                </div>`;
            return;
        }
        el.innerHTML = this._devices.map((d) => {
            const i = d.info || {}; const s = d.status || {};
            const bits = [];
            if (i.node != null) bits.push(`node ${i.node}`);
            if (s.battery != null) bits.push(`${s.battery} %`);
            if (s.rx_ok != null) bits.push(`${s.rx_ok} heard`);
            return `
                <div class="stat-card">
                    <div class="stat-card__label">${d.connected ? '🟢' : '⚪'} ${LocalMeshPanel.esc(d.label || 'receiver')}</div>
                    <div class="stat-card__value" style="font-size: 0.95rem">
                        ${bits.length ? bits.join(' · ') : (d.connected ? 'Connected' : 'Not connected')}
                    </div>
                </div>`;
        }).join('');
    }

    _renderReceivers() {
        const el = this._root.querySelector('[data-lm-receivers]');
        if (!this._devices.length) {
            el.innerHTML = `<p class="lw-empty">No receiver configured.${this._isAdmin ? ' Add one on the Settings tab.' : ''}</p>`;
            return;
        }
        el.innerHTML = `<div class="cfg-section">${this._devices.map((d) => this._receiverHtml(d)).join('')}</div>`;
    }

    _receiverHtml(d) {
        const esc = LocalMeshPanel.esc;
        const head = `
            <header class="cfg-card__head">
                <h3 class="cfg-card__title">${d.connected ? '🟢' : '⚪'} ${esc(d.label || 'receiver')}</h3>
                <p class="cfg-card__hint">${esc(d.serial_port || 'no serial port')}</p>
            </header>`;
        if (!d.connected) {
            return `<article class="cfg-card">${head}
                <p class="cfg-companion__offline-hint">Not connected. Is the board plugged in, and is the port right?</p>
            </article>`;
        }
        const channels = Object.entries(d.channels || {});
        const neighbors = d.neighbors || [];
        return `<article class="cfg-card">${head}
            <div class="cfg-mc-readouts">${LocalMeshPanel.readouts(d).map(([label, value]) => `
                <div class="cfg-mc-readout">
                    <span class="cfg-mc-readout__label">${label}</span>
                    <span class="cfg-mc-readout__value">${esc(value)}</span>
                </div>`).join('')}
            </div>
            <h4 class="cfg-card__title" style="margin-top: 16px">Channels</h4>
            <p class="cfg-card__hint">${channels.length
                ? channels.map(([id, name]) => `${esc(name)} (${id})`).join(' · ')
                : 'None: this receiver has no channel key, so it shows only direct messages to itself.'}</p>
            <h4 class="cfg-card__title" style="margin-top: 16px">Neighbours (repeaters heard directly)</h4>
            ${neighbors.length ? `
                <table class="lw-table">
                    <thead><tr><th>Node</th><th>Signal</th><th>Last heard</th><th class="lw-r">Packets</th></tr></thead>
                    <tbody>${neighbors.map((n) => `<tr>
                        <td>${n.node}</td>
                        <td>${LocalMeshPanel.signal(n.rssi, n.snr)}</td>
                        <td>${LocalMeshPanel.duration(n.ago_s)} ago</td>
                        <td class="lw-r">${n.count}</td>
                    </tr>`).join('')}</tbody>
                </table>` : '<p class="cfg-card__hint">None heard yet.</p>'}
        </article>`;
    }

    /** [label, text] pairs for one receiver's readout tiles; '--' where the board has not told us yet. */
    static readouts(d) {
        const i = d.info || {}; const s = d.status || {};
        const v = (x, f = (y) => String(y)) => (x == null ? '--' : f(x));
        const radio = i.freq_mhz != null
            ? `${i.freq_mhz.toFixed(3)} MHz · SF${i.sf} · ${i.bw_khz} kHz · CR 4/${i.cr}` : '--';
        let battery = v(s.battery, (b) => `${b} %`);
        if (s.battery_mv) battery += ` (${(s.battery_mv / 1000).toFixed(2)} V)`;
        return [
            ['Node', v(i.node)],
            ['Member', v(i.member)],
            ['Role', v(i.role)],
            ['Firmware', v(i.firmware)],
            ['Board', v(i.board)],
            ['Radio', radio],
            ['Transmit power', v(i.power_dbm, (p) => `${p} dBm`)],
            ['Battery', battery],
            ['Uptime', v(s.uptime_s, LocalMeshPanel.duration)],
            ['Heard / rejected', s.rx_ok == null ? '--' : `${s.rx_ok} / ${s.rx_rejected}`],
            ['Sent', v(s.tx)],
            ['Last signal', s.rssi == null ? '--' : `${s.rssi} dBm · SNR ${s.snr} dB`],
            ['Noise floor', v(s.noise_floor, (n) => `${n} dBm`)],
            ['Airtime used', s.budget_limit ? `${Math.round((100 * s.budget_ms) / s.budget_limit)} %` : '--'],
            ['Last packet', v(d.last_heard, (t) => `${LocalMeshPanel.duration(Math.max(0, Date.now() / 1000 - t))} ago`)],
        ];
    }

    // --- formatting ---

    static signal(rssi, snr) {
        if (rssi == null) return '--';
        return snr == null ? `${rssi} dBm` : `${rssi} dBm · ${snr} dB`;
    }

    /** Today: 14:03:22; earlier: 10-11 14:03. */
    static clock(t) {
        if (!t) return '--';
        const d = new Date(t * 1000);
        const p2 = (n) => String(n).padStart(2, '0');
        const time = `${p2(d.getHours())}:${p2(d.getMinutes())}`;
        return d.toDateString() === new Date().toDateString()
            ? `${time}:${p2(d.getSeconds())}` : `${p2(d.getDate())}-${p2(d.getMonth() + 1)} ${time}`;
    }

    static ago(t) {
        return t ? `${LocalMeshPanel.duration(Math.max(0, Date.now() / 1000 - t))} ago` : '--';
    }

    static duration(sec) {
        sec = Math.floor(sec);
        if (sec < 60) return `${sec} s`;
        if (sec < 3600) return `${Math.floor(sec / 60)} min`;
        if (sec < 86400) return `${Math.floor(sec / 3600)} h ${Math.floor((sec % 3600) / 60)} min`;
        return `${Math.floor(sec / 86400)} d ${Math.floor((sec % 86400) / 3600)} h`;
    }

    static esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}

window.LocalMeshPanel = LocalMeshPanel;
window.registerSidebarPage({
    route: 'localmesh',
    make: () => new LocalMeshPanel(),
});
