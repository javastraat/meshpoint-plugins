/**
 * LocalMesh Dashboard -- a standalone, live-only companion page to the
 * localmesh plugin's own Members/Messages/Activity/Send/Receiver/Settings
 * page.
 *
 * Same gap this closes as reticulum-dashboard's own (see that plugin's
 * header comment): a box with only LocalMesh configured shows a
 * completely empty core Dashboard -- the RF capture pipeline's packet
 * table does get LocalMesh's packets (its capture source IS on that
 * pipeline), but the stat-card/map/live-feel summary the core Dashboard
 * gives isn't shaped for "status/members/neighbours/messages/airtime" at
 * all. This page gives LocalMesh that same live, glanceable feel, reusing
 * the core Dashboard's own markup verbatim (main.dashboard,
 * .dashboard__stats/.dashboard__main/.dashboard__map/.dashboard__side/
 * .dashboard__feed, .panel* -- see reticulum_dashboard.js's own header
 * comment for why a hand-rolled approximation is the wrong call).
 *
 * Deliberately its own plugin (not a tab on the localmesh plugin's page):
 * the manifest only supports one [sidebar] entry per plugin, so a second
 * nav item needs a second plugin -- see plugins/apps/reticulum-dashboard
 * for the identical reasoning (and plugins/apps/hello-world for the
 * minimal "sidebar" pattern both follow). No new backend routes: reads
 * the localmesh plugin's already-public /api/localmesh/{status,members,
 * messages,activity,repeaters,packet-stats}.
 *
 * localmesh has no WebSocket broadcasts of its own (unlike reticulum's
 * reticulum_announce/_peer/_telemetry) -- its capture source only feeds
 * the generic RF packet pipeline, which this page doesn't use -- so
 * everything here polls every 10s instead, same cadence localmesh_panel.js's
 * own page already uses. The activity ticker still gets the same
 * "flowing" flash feel by diffing each poll against what it already
 * rendered rather than reacting to a push event.
 *
 * Deliberately smaller in scope than reticulum-dashboard: LocalMesh has
 * no NomadNet-style page browsing. It does now have a working "Send
 * message" action though, same as reticulum-dashboard's own peer drawer --
 * LocalMesh messages mirror into the same shared core message store
 * Reticulum/Meshtastic/MeshCore already write into (see the localmesh
 * plugin's own listener.py), so window.LocalMeshPanel.composeMessageTo()
 * can jump straight to a live thread on the core Messages page the same
 * way reticulum_panel.js's composeMessageTo() always could. Reuses
 * window.LocalMeshMemberDrawer and window.LocalMeshPanel's static
 * formatting/navigation helpers (esc/signal/decisionOf/clock/ago/
 * showModal/composeMessageTo) directly -- localmesh is a hard `requires`
 * of this plugin, so its scripts are always loaded
 * alongside this page's own, same reuse localmesh_panel.js's own code
 * already is.
 */

// Ticker ring-buffer cap -- same reasoning as reticulum-dashboard's
// RTD_TICKER_LIMIT: a glanceable live view, not a searchable log (the
// localmesh plugin's own Activity tab already is that, with its own
// 300-packet-per-receiver backend buffer).
const LMD_TICKER_LIMIT = 60;

// Members list (right column) cap -- same reasoning as reticulum-
// dashboard's RTD_PEER_LIST_LIMIT.
const LMD_MEMBER_LIST_LIMIT = 150;
const LMD_ONLINE_S = 2 * 3600;   // as the core's node cards
const LMD_LOW_MV = 3600;
const LMD_SORTS = { members: ['last_heard', 'signal', 'name'], repeaters: ['last_heard', 'signal', 'name', 'battery'] };
const LMD_SORT_TEXT = { last_heard: 'Last heard', signal: 'Signal', name: 'Name', battery: 'Battery' };
const LMD_FILTERS = ['all', 'direct', 'relayed'];

class LocalMeshDashboard {
    constructor() {
        this._root = null;
        this._refreshTimer = null;
        this._devices = [];
        this._members = [];
        this._repeaters = [];
        this._listView = 'members';       // the right-hand card shows either list
        try { if (localStorage.getItem('meshpoint.lmdListView') === 'repeaters') this._listView = 'repeaters'; } catch (_) {}
        this._activity = [];              // mirrors the ticker's DOM rows, newest first
        this._activitySeen = new Set();   // composite keys already rendered, for diff-driven flashing
        this._seeded = false;
        this._memberSearchQuery = '';
        this._sortBy = this._loadPref('lmdSort', Object.keys(LMD_SORT_TEXT), 'last_heard');
        this._filter = this._loadPref('lmdFilter', LMD_FILTERS, 'all');
        this._drawer = window.LocalMeshMemberDrawer ? new window.LocalMeshMemberDrawer() : null;
        this._homeLat = null;
        this._homeLon = null;
        this._homeMarker = null;
        // Same localStorage key node_map.js's own basemap toggle uses --
        // deliberately shared, not a separate preference, same reasoning
        // as reticulum_dashboard.js's own _loadBasemapPref().
        this._basemapLight = this._loadBasemapPref();
    }

    mount(rootEl) {
        this._root = rootEl;
        rootEl.innerHTML = `
            <main class="dashboard">
                <section class="dashboard__stats">
                    <div class="stat-card">
                        <div class="stat-card__label">Status</div>
                        <div class="stat-card__value" id="lmd-stat-status">--</div>
                        <div class="stat-card__sub" id="lmd-stat-status-sub"></div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Members</div>
                        <div class="stat-card__value" id="lmd-stat-members">--</div>
                        <div class="stat-card__sub">heard 24h / on the list</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Repeaters</div>
                        <div class="stat-card__value" id="lmd-stat-repeaters">--</div>
                        <div class="stat-card__sub">heard 24h / known</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Neighbours</div>
                        <div class="stat-card__value" id="lmd-stat-neighbours">--</div>
                        <div class="stat-card__sub">heard directly</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Messages (24h)</div>
                        <div class="stat-card__value" id="lmd-stat-messages">--</div>
                        <div class="stat-card__sub" id="lmd-stat-messages-sub"></div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Airtime (hour)</div>
                        <div class="stat-card__value" id="lmd-stat-airtime">--</div>
                        <div class="stat-card__sub" id="lmd-stat-airtime-sub"></div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Packets</div>
                        <div class="stat-card__value" id="lmd-stat-packets">--</div>
                        <div class="stat-card__sub">24h / total</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Packets / min</div>
                        <div class="stat-card__value" id="lmd-stat-ppm">--</div>
                        <div class="stat-card__sub">last 10 min</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Avg RSSI</div>
                        <div class="stat-card__value" id="lmd-stat-rssi">--</div>
                        <div class="stat-card__sub">24h</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Weakest battery</div>
                        <div class="stat-card__value" id="lmd-stat-battery">--</div>
                        <div class="stat-card__sub" id="lmd-stat-battery-sub">no status yet</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-card__label">Firmware</div>
                        <div class="stat-card__value" id="lmd-stat-firmware">--</div>
                        <div class="stat-card__sub" id="lmd-stat-firmware-sub">repeaters</div>
                    </div>
                </section>

                <div class="dashboard__main">
                    <section class="dashboard__map">
                        <div class="panel">
                            <div class="panel__header">
                                Map
                                <div class="panel__header-actions">
                                    <button id="lmd-map-basemap-btn" class="map-expand-btn" type="button" title="Darken the map"></button>
                                    <button id="lmd-map-home-btn" class="map-expand-btn" type="button" title="Center on home">
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14" aria-hidden="true">
                                            <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>
                                            <polyline points="9 22 9 12 15 12 15 22"/>
                                        </svg>
                                    </button>
                                    <button id="lmd-map-expand-btn" class="map-expand-btn" type="button" title="Expand map">⤢</button>
                                </div>
                            </div>
                            <div class="panel__body" style="position:relative">
                                <div id="lmd-member-map" class="lmd-member-map map-container" hidden></div>
                                <p class="lw-empty" id="lmd-map-empty">
                                    No located members yet -- most members
                                    don't report a position.
                                </p>
                            </div>
                        </div>
                    </section>

                    <section class="dashboard__side">
                        <div class="panel panel--nodes">
                            <div class="panel__header panel__header--tabs">
                                <div class="lw-tabs" role="tablist">
                                    <button class="lw-tab" type="button" role="tab" data-lmd-listview="members">Members</button>
                                    <button class="lw-tab" type="button" role="tab" data-lmd-listview="repeaters">Repeaters</button>
                                </div>
                                <div class="node-search-wrap">
                                    <input type="search" id="lmd-member-search" class="node-search"
                                           placeholder="Search..." autocomplete="off" spellcheck="false">
                                </div>
                            </div>
                            <div class="nc-controls" role="group" aria-label="Sort and filter">
                                <select id="lmd-sort" class="nc-controls__sort" aria-label="Sort by"></select>
                                <div class="nc-controls__filter" role="group" aria-label="Filter">
                                    <button type="button" class="nc-pill" data-lmd-filter="all">All</button>
                                    <button type="button" class="nc-pill" data-lmd-filter="direct">Direct</button>
                                    <button type="button" class="nc-pill" data-lmd-filter="relayed">Relayed</button>
                                </div>
                            </div>
                            <div class="panel__body" id="lmd-members-list"></div>
                            <p class="lw-empty" id="lmd-members-empty" style="display:none">
                                No LocalMesh members heard yet.
                            </p>
                        </div>
                    </section>
                </div>

                <section class="dashboard__feed">
                    <div class="panel">
                        <div class="panel__header">
                            <span>Live Activity</span>
                            <span class="packet-count" id="lmd-ticker-count">0</span>
                        </div>
                        <div class="panel__body lw-table-wrap">
                            <table class="lw-table lmd-ticker-table">
                                <colgroup>
                                    <col class="lmd-col-time"><col class="lmd-col-type"><col class="lmd-col-from">
                                    <col class="lmd-col-path"><col class="lmd-col-hops"><col class="lmd-col-signal"><col class="lmd-col-badge"><col>
                                </colgroup>
                                <thead>
                                    <tr>
                                        <th>Time</th><th>Type</th><th>From</th>
                                        <th>Path</th><th>Hops left</th><th>Signal</th><th>Decision</th><th>Details</th>
                                    </tr>
                                </thead>
                                <tbody id="lmd-ticker-tbody"></tbody>
                            </table>
                            <p class="lw-empty" id="lmd-ticker-empty">
                                Waiting for the first packet…
                            </p>
                        </div>
                    </div>
                </section>
            </main>
        `;

        this._q('#lmd-member-search')?.addEventListener('input', (e) => {
            this._memberSearchQuery = e.target.value.trim().toLowerCase();
            this._renderMembersList();
        });
        this._q('.panel--nodes .lw-tabs')?.addEventListener('click', (e) => {
            const btn = e.target.closest('[data-lmd-listview]');
            if (!btn || btn.dataset.lmdListview === this._listView) return;
            this._listView = btn.dataset.lmdListview;
            try { localStorage.setItem('meshpoint.lmdListView', this._listView); } catch (_) {}
            this._syncListTabs();
            this._renderMembersList();
        });
        this._syncListTabs();
        this._q('#lmd-sort')?.addEventListener('change', (e) => {
            this._sortBy = e.target.value;
            this._savePref('lmdSort', this._sortBy);
            this._renderMembersList();
        });
        this._root.querySelectorAll('[data-lmd-filter]').forEach((b) => b.addEventListener('click', () => {
            this._filter = b.dataset.lmdFilter;
            this._savePref('lmdFilter', this._filter);
            this._syncFilter();
            this._renderMembersList();
        }));
        this._syncFilter();

        this._syncBasemapBtn(this._basemapLight);
        this._q('#lmd-map-basemap-btn')?.addEventListener('click', () => this._toggleBasemap());
        this._q('#lmd-map-home-btn')?.addEventListener('click', () => this._centerOnHome());
        this._q('#lmd-map-expand-btn')?.addEventListener('click', () => this._toggleExpand());

        this._q('#lmd-members-list')?.addEventListener('click', (e) => {
            const node = e.target.closest('[data-node]');
            if (node && this._drawer) {
                const r = this._repeaters.find((x) => String(x.node) === node.dataset.node);
                if (r) this._drawer.openRepeater(r);
                return;
            }
            const row = e.target.closest('[data-member]');
            if (!row) return;
            const m = this._members.find((x) => String(x.member) === row.dataset.member);
            if (m) this._openMemberDrawer(m);
        });
        this._q('#lmd-ticker-tbody')?.addEventListener('click', (e) => {
            const tr = e.target.closest('tr[data-lmd-key]');
            if (!tr) return;
            const entry = this._activity.find((a) => this._activityKey(a) === tr.dataset.lmdKey);
            if (entry) this._openActivityDetail(entry);
        });
    }

    /** Called by the router (via registerSidebarPage) when the page becomes active. */
    show() {
        this._load();
        clearInterval(this._refreshTimer);
        this._refreshTimer = setInterval(() => this._load(), 10_000);
        requestAnimationFrame(() => this._mapResized());   // it may have been measured while the page was hidden
    }

    hide() {
        clearInterval(this._refreshTimer);
        this._refreshTimer = null;
    }

    async _load() {
        await Promise.all([
            this._loadStatus(), this._loadMessages24h(), this._loadHomeLocation(), this._loadPacketStats(),
            this._loadMembers().then(() => this._loadActivity()),   // the rows use the repeaters' names
        ]);
    }

    /** The device's own configured location (Configuration -> Identity),
     * same field node_map.js's centerOnHome() reads -- device-level, not
     * protocol-specific, same as reticulum_dashboard.js's own
     * _loadHomeLocation(). Loaded once; a mid-session location change
     * needs a page reload to pick up. */
    async _loadHomeLocation() {
        if (this._homeLat != null) return;
        try {
            const r = await fetch('/api/device', { credentials: 'same-origin' });
            if (!r.ok) return;
            const device = await r.json();
            if (device.latitude == null || device.longitude == null) return;
            this._homeLat = device.latitude;
            this._homeLon = device.longitude;
            this._renderHomeMarker();
            this._renderMemberMap();
        } catch (_) {}
    }

    // --- Stat cards ----------------------------------------------------
    // Same five cards, same computations, as localmesh_panel.js's own
    // _renderCards() -- duplicated rather than imported, same small-
    // duplication convention reticulum_dashboard.js's own RTD_ASPECT_BADGES
    // follows (this codebase's existing pattern of small self-contained
    // plugin frontend files).

    async _loadStatus() {
        try {
            const r = await fetch('/api/localmesh/status', { credentials: 'same-origin' });
            if (!r.ok) return;
            this._devices = await r.json();
            this._renderStatusCard();
            this._renderNeighboursCard();
            this._renderAirtimeCard();
        } catch (_) {}
    }

    _renderStatusCard() {
        const devs = this._devices;
        if (!devs.length) {
            this._setText('lmd-stat-status', 'Not configured');
            this._setText('lmd-stat-status-sub', '');
            return;
        }
        const online = devs.filter((d) => d.connected);
        const nodes = devs.map((d) => (d.info && d.info.node != null ? `node ${d.info.node}` : (d.label || 'receiver')));
        this._setText('lmd-stat-status',
            online.length === devs.length ? 'Running' : online.length ? `${online.length} of ${devs.length} online` : 'Offline');
        this._setText('lmd-stat-status-sub', nodes.join(' · '));
    }

    _renderNeighboursCard() {
        const neigh = this._devices.flatMap((d) => d.neighbors || []);
        const neighNodes = [...new Set(neigh.map((n) => n.node))];
        const el = this._q('#lmd-stat-neighbours');
        if (el) {
            el.textContent = String(neighNodes.length);
            el.title = neigh.map((n) => `node ${n.node}: ${n.rssi} dBm / ${n.snr} dB`).join('\n');
        }
    }

    _renderAirtimeCard() {
        const air = this._devices.filter((d) => d.status && d.status.budget_limit)
            .map((d) => ({ d, used: d.status.budget_ms, limit: d.status.budget_limit }))
            .sort((a, b) => b.used / b.limit - a.used / a.limit);
        if (!air.length) {
            this._setText('lmd-stat-airtime', '--');
            this._setText('lmd-stat-airtime-sub', 'no budget reported');
            return;
        }
        this._setText('lmd-stat-airtime', `${Math.round((100 * air[0].used) / air[0].limit)} %`);
        this._setText('lmd-stat-airtime-sub', `${(air[0].used / 1000).toFixed(1)} s of ${Math.round(air[0].limit / 1000)} s`);
    }

    async _loadPacketStats() {
        try {
            const r = await fetch('/api/localmesh/packet-stats', { credentials: 'same-origin' });
            if (!r.ok) return;   // an older localmesh plugin
            const s = await r.json();
            if (s.total == null) return;
            this._setText('lmd-stat-packets', `${s.day} / ${s.total}`);
            this._setText('lmd-stat-ppm', s.per_minute);
            this._setText('lmd-stat-rssi', s.avg_rssi == null ? '--' : `${s.avg_rssi} dBm`);
        } catch (_) {}
    }

    /** Weakest battery and firmware, from the repeaters' last status and announce. */
    _renderRepeaterCards() {
        const P = window.LocalMeshPanel;
        const weak = this._repeaters.filter((r) => r.status && r.status.battery_mv)
            .sort((a, b) => a.status.battery_mv - b.status.battery_mv)[0];
        this._setText('lmd-stat-battery', weak ? `${(weak.status.battery_mv / 1000).toFixed(2)} V` : '--');
        this._setText('lmd-stat-battery-sub', weak ? (weak.name || `repeater ${weak.node}`) : 'no status yet');
        const known = this._repeaters.map((r) => (P && P.repeaterHealth ? P.repeaterHealth(r).firmware : '')).filter(Boolean);
        const num = (v) => v.split('.').map(Number);
        const newest = [...known].sort((a, b) => {
            const x = num(a); const y = num(b);
            return y[0] - x[0] || y[1] - x[1] || y[2] - x[2];
        })[0];
        this._setText('lmd-stat-firmware', newest || '--');
        this._setText('lmd-stat-firmware-sub', newest
            ? `${known.filter((v) => v === newest).length} of ${known.length} repeaters` : 'repeaters');
    }

    async _loadMembers() {
        try {
            const [r, rr] = await Promise.all([
                fetch('/api/localmesh/members', { credentials: 'same-origin' }),
                fetch('/api/localmesh/repeaters', { credentials: 'same-origin' }),
            ]);
            if (rr.ok) {
                this._repeaters = await rr.json();
                const day = Date.now() / 1000 - 86400;
                this._setText('lmd-stat-repeaters',
                    `${this._repeaters.filter((x) => x.last_heard && x.last_heard >= day).length} / ${this._repeaters.length}`);
                this._renderRepeaterCards();
            }
            if (!r.ok) return;
            this._members = await r.json();
            this._renderMembersCard();
            this._renderMembersList();
            this._renderMemberMap();
        } catch (_) {}
    }

    _openMemberDrawer(m) {
        if (!this._drawer) return;
        // Now wired, since LocalMesh messages flow through the same
        // shared store Reticulum's do (see localmesh plugin's own
        // listener.py) -- window.LocalMeshPanel.composeMessageTo() is
        // the one canonical place that navigation lives, same helper
        // the main LocalMesh page's own drawer and messaging_chat.js's
        // drawer both call too.
        const onMessage = (window.LocalMeshPanel && !m.blocked)
            ? (member) => window.LocalMeshPanel.composeMessageTo(member, m.name)
            : undefined;
        this._drawer.open(m, onMessage ? { onMessage } : {});
    }

    _renderMembersCard() {
        const day = Date.now() / 1000 - 86400;
        const listed = this._members.filter((m) => m.listed).length;
        const active = this._members.filter((m) => m.listed && m.last_heard && m.last_heard >= day).length;
        this._setText('lmd-stat-members', `${active} / ${listed}`);
    }

    async _loadMessages24h() {
        try {
            const r = await fetch('/api/localmesh/messages', { credentials: 'same-origin' });
            if (!r.ok) return;
            const day = Date.now() / 1000 - 86400;
            const msgs = (await r.json()).filter((m) => (m.sort_time || m.time) >= day);
            const direct = msgs.filter((m) => m.direct).length;
            this._setText('lmd-stat-messages', msgs.length);
            this._setText('lmd-stat-messages-sub', `${msgs.length - direct} channel · ${direct} private`);
        } catch (_) {}
    }

    // --- Members list (right column) ------------------------------------

    _syncListTabs() {
        this._root?.querySelectorAll('[data-lmd-listview]').forEach((btn) => {
            const active = btn.dataset.lmdListview === this._listView;
            btn.classList.toggle('lw-tab--active', active);
            btn.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        this._syncSort();
    }

    _loadPref(key, allowed, fallback) {
        try {
            const v = localStorage.getItem(`meshpoint.${key}`);
            return allowed.includes(v) ? v : fallback;
        } catch (_) { return fallback; }
    }

    _savePref(key, value) {
        try { localStorage.setItem(`meshpoint.${key}`, value); } catch (_) { /* private mode */ }
    }

    _syncFilter() {
        this._root?.querySelectorAll('[data-lmd-filter]').forEach((b) => {
            const on = b.dataset.lmdFilter === this._filter;
            b.classList.toggle('nc-pill--active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    /** The sort choices of the list that shows (only repeaters have a battery). */
    _syncSort() {
        const sel = this._q('#lmd-sort');
        if (!sel) return;
        const keys = LMD_SORTS[this._listView];
        sel.innerHTML = keys.map((k) => `<option value="${k}">Sort: ${LMD_SORT_TEXT[k]}</option>`).join('');
        sel.value = this._sort();
    }

    _sort() { return LMD_SORTS[this._listView].includes(this._sortBy) ? this._sortBy : 'last_heard'; }

    /** Filter and sort; a list that does not say whether something was heard directly leaves it out of both. */
    _arrange(rows, direct, name) {
        const kept = rows.filter((r) => this._filter === 'all' || direct(r) === (this._filter === 'direct'));
        const by = {
            last_heard: (a, b) => (b.last_heard || 0) - (a.last_heard || 0),
            signal: (a, b) => (b.rssi ?? -999) - (a.rssi ?? -999),
            name: (a, b) => name(a).localeCompare(name(b), undefined, { numeric: true, sensitivity: 'base' }),
            battery: (a, b) => (a.status?.battery_mv || 99999) - (b.status?.battery_mv || 99999),   // weakest first
        }[this._sort()];
        return [...kept].sort(by);
    }

    _showList(rows, html, emptyText) {
        const list = this._q('#lmd-members-list');
        const empty = this._q('#lmd-members-empty');
        if (!list) return;
        list.innerHTML = rows.slice(0, LMD_MEMBER_LIST_LIMIT).map(html).join('');
        if (empty) {
            empty.style.display = rows.length ? 'none' : '';
            empty.textContent = emptyText;
        }
    }

    _renderRepeatersList() {
        const q = this._memberSearchQuery;
        const found = this._repeaters.filter((r) => !q || (r.name || '').toLowerCase().includes(q) || String(r.node).includes(q));
        const rows = this._arrange(found, (r) => (r.last_heard ? !!r.direct : null), (r) => r.name || `Repeater ${r.node}`);
        this._showList(rows, (r) => this._repeaterCard(r),
            q || this._filter !== 'all' ? 'No repeaters match.' : 'No repeaters known yet.');
    }

    _renderMembersList() {
        if (this._listView === 'repeaters') { this._renderRepeatersList(); return; }
        const q = this._memberSearchQuery;
        // By default only members heard at least once or with a known name, not the whole roster; a search reaches all
        // of it. The receiver's own last_heard is never set (it does not hear itself).
        const found = this._members.filter((m) => (q
            ? (m.name || '').toLowerCase().includes(q) || String(m.member).includes(q)
            : m.last_heard != null || m.own || m.name));
        const rows = this._arrange(found, (m) => m.heard_directly ?? null, (m) => m.name || `Member ${m.member}`);
        this._showList(rows, (m) => this._memberCard(m),
            q || this._filter !== 'all' ? 'No members match.' : 'No members heard yet.');
    }

    // --- cards, with the core's own classes (node_cards.css, nc-*) ---

    _chip(text, cls = '', title = '') {
        return `<span class="nc-chip${cls ? ` nc-chip--${cls}` : ''}"${title ? ` title="${this._attr(title)}"` : ''}>${text}</span>`;
    }

    _chipRow(chips, cls = '') {
        const kept = chips.filter(Boolean);
        return kept.length ? `<div class="nc-card__row${cls}">${kept.join('')}</div>` : '';
    }

    _signalChips(x) {
        if (x.rssi == null) return [];
        const q = x.rssi > -80 ? ['Excellent', 'excellent'] : x.rssi >= -100 ? ['Good', 'good']
            : x.rssi >= -115 ? ['Fair', 'fair'] : ['Poor', 'poor'];   // same limits as the core's node cards
        const level = x.rssi > -80 ? 5 : x.rssi >= -100 ? 4 : x.rssi >= -115 ? 3 : x.rssi >= -125 ? 2 : 1;
        const bars = [1, 2, 3, 4, 5].map((i) => `<span class="nc-bar nc-bar--h${i}${i <= level ? ' active' : ''}"></span>`).join('');
        return [this._chip(`<span class="nc-bars">${bars}</span> ${Math.round(x.rssi)} dBm`, `signal nc-chip--${q[1]}`, 'of the last hop'),
            x.snr == null ? '' : this._chip(`SNR ${Number(x.snr).toFixed(1)} dB`),
            this._chip(q[0], `quality nc-chip--${q[1]}`)];
    }

    _card({ attr, label, color, name, heard, badges, rows }) {
        const ago = window.LocalMeshPanel ? window.LocalMeshPanel.ago : () => '--';
        const online = heard && Date.now() / 1000 - heard < LMD_ONLINE_S;
        return `<div class="nc-card" ${attr}>
            <div class="nc-card__top">
                <div class="nc-avatar" style="background:${color}">${this._esc(label)}</div>
                <div class="nc-card__identity">
                    <div class="nc-card__name"><span class="nc-online nc-online--${online ? 'on' : 'off'}" title="${online ? 'Heard' : 'Not heard'} within 2 hours"></span> ${this._esc(name)}</div>
                    <div class="nc-card__heard">${heard ? this._esc(ago(heard)) : '--'}</div>
                </div>
                ${badges}<span class="nc-proto lmd-proto">LM</span>
            </div>${rows}</div>`;
    }

    _memberCard(m) {
        const name = m.name || `Member ${m.member}`;
        const badges = (m.blocked ? '<span class="lw-badge lw-badge--undecrypted">blocked</span>' : '')
            + (m.own ? '<span class="lw-badge lw-badge--data">this Meshpoint</span>' : '');
        const how = m.heard_directly === true ? 'direct' : m.heard_directly === false ? `via ${this._esc(this._nodeName(m.via))}` : '';
        const km = this._distance(m);
        return this._card({
            attr: `data-member="${Number(m.member)}"`, label: this._initials(name), color: this._color(`m${m.member}`), name,
            heard: m.last_heard, badges,
            rows: this._chipRow([...this._signalChips(m), how && this._chip(how)])
                + this._chipRow([m.count ? this._chip(`${Number(m.count)} packet${m.count === 1 ? '' : 's'}`, 'telem') : '',
                    m.type ? this._chip(`last: ${this._esc(m.type.replace(/_/g, ' '))}`, 'telem') : '',
                    km == null ? '' : this._chip(`&#x25CE; ${km}`, 'telem')])
                + this._chipRow([m.national ? this._chip('NATIONAL', 'band') : '', this._chip(`member ${Number(m.member)}`, 'id')],
                    ' nc-card__row--meta'),
        });
    }

    _repeaterCard(r) {
        const P = window.LocalMeshPanel;
        const name = r.name || `Repeater ${r.node}`;
        const s = r.status;
        const h = P && P.repeaterHealth ? P.repeaterHealth(r) : { firmware: '', battery: '', uptime: '', title: '' };
        const badges = r.revoked ? '<span class="lw-badge lw-badge--undecrypted">revoked</span>' : '';
        const how = !r.last_heard ? '' : r.direct ? 'direct' : r.via ? `via ${this._esc(this._nodeName(r.via))}` : 'relayed';
        return this._card({
            attr: `data-node="${Number(r.node)}"`, label: this._initials(name), color: this._color(`r${r.node}`), name,
            heard: r.last_heard, badges,
            rows: this._chipRow([...this._signalChips(r), how && this._chip(how)])
                + this._chipRow(!s ? [] : [
                    h.battery && this._chip(`&#9889; ${this._esc(h.battery)}`, s.battery_mv < LMD_LOW_MV ? 'poor' : 'telem', h.title),
                    this._chip(`up ${this._esc(h.uptime)}`, 'telem', h.title),
                    this._chip(`noise ${Number(s.noise_floor)} dBm`, 'telem', h.title),
                    this._chip(`air ${Number(s.airtime_pct)}%`, 'telem', h.title),
                    this._chip(`${(s.neighbors || []).length} neighbours`, 'telem', 'heard by it in the last 25 hours (10 at most)')])
                + this._chipRow([h.firmware ? this._chip(this._esc(h.firmware), 'meta') : '',
                    r.region == null ? '' : this._chip(`REGION ${Number(r.region)}`, 'band'),
                    this._chip(`node ${Number(r.node)}`, 'id')], ' nc-card__row--meta'),
        });
    }

    /** "NL-AMS-1057" -> "1057", "Einstein Tag" -> "ET", "Albert" -> "ALBE". */
    _initials(name) {
        const parts = name.split(/[\s\-_.]+/).filter(Boolean);
        const number = [...parts].reverse().find((x) => /^\d{2,}$/.test(x));
        if (number) return number.slice(-5);
        return (parts.length > 1 ? parts.map((x) => [...x][0]).join('') : name).slice(0, 4).toUpperCase();
    }

    _color(key) {
        let hash = 0;
        for (let i = 0; i < key.length; i++) hash = key.charCodeAt(i) * 31 + ((hash << 5) - hash);
        return `hsl(${Math.abs(hash) % 360}, 55%, 45%)`;
    }

    _nodeName(node) {
        const r = this._repeaters.find((x) => x.node === node);
        return (r && r.name) || `node ${node}`;
    }

    _distance(m) {
        if (m.lat == null || m.lon == null || this._homeLat == null || this._homeLon == null) return null;
        const rad = Math.PI / 180;
        const a = Math.sin((m.lat - this._homeLat) * rad / 2) ** 2 + Math.cos(this._homeLat * rad) * Math.cos(m.lat * rad)
            * Math.sin((m.lon - this._homeLon) * rad / 2) ** 2;
        const km = 12742 * Math.asin(Math.sqrt(Math.min(1, a)));
        if (km < 0.05) return null;
        return window.MeshpointDisplayUnits ? window.MeshpointDisplayUnits.formatDistanceKm(km) : `${km.toFixed(1)} km`;
    }

    _attr(s) { return this._esc(s).replace(/"/g, '&quot;'); }

    // --- Located-members map ---------------------------------------------
    // Ported from reticulum_dashboard.js's own telemetry map (same
    // Leaflet-instance/home-marker/basemap-toggle shape), fed from
    // LocalMesh members with a position instead of Reticulum telemetry
    // peers -- same reasoning, a different data source. No "discovered
    // interfaces" layer: LocalMesh has no equivalent announce-based
    // interface-discovery concept.

    _renderMemberMap() {
        const el = this._q('#lmd-member-map');
        const empty = this._q('#lmd-map-empty');
        if (!el || typeof L === 'undefined') return;
        const located = this._members.filter((m) => m.lat != null && m.lon != null);
        const hasHome = this._homeLat != null && this._homeLon != null;
        if (!located.length && !hasHome) {
            el.hidden = true;
            if (empty) empty.style.display = '';
            return;
        }
        const wasHidden = el.hidden;
        el.hidden = false;
        if (empty) empty.style.display = 'none';

        if (!this._map) {
            // Same deferred-measurement reasoning as reticulum_dashboard.js's
            // own _renderTelemetryMap(): Leaflet reads the container's real
            // size when constructed, and measuring in the same synchronous
            // tick as unhiding it can catch a stale (zero-size) layout.
            requestAnimationFrame(() => this._initMap(el));
            return;
        }
        this._updateMemberMarkers(located);
        if (wasHidden) requestAnimationFrame(() => this._map && this._map.invalidateSize());
    }

    _initMap(el) {
        if (this._map || el.hidden) return;
        this._map = L.map(el, { scrollWheelZoom: true });
        const tileOpts = {
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
            maxZoom: 19,
        };
        let tileLayer = L.tileLayer(window.MAP_TILE_URL_FALLBACK, tileOpts).addTo(this._map);
        window.getMapTileUrl().then((url) => {
            if (url && url !== window.MAP_TILE_URL_FALLBACK && this._map) {
                this._map.removeLayer(tileLayer);
                tileLayer = L.tileLayer(url, tileOpts).addTo(this._map);
            }
        });
        this._memberMarkers = L.layerGroup().addTo(this._map);
        this._renderHomeMarker();
        // now, not when this was scheduled: the members may have come in since
        this._updateMemberMarkers(this._members.filter((m) => m.lat != null && m.lon != null));
        // The box gets its real size only after the layout (and 0 while the page is hidden): measure again whenever it
        // changes, or Leaflet draws half a map.
        if (window.ResizeObserver) {
            this._mapObserver = new ResizeObserver(() => this._mapResized());
            this._mapObserver.observe(el);
        }
        requestAnimationFrame(() => this._mapResized());
    }

    _renderHomeMarker() {
        if (!this._map || this._homeLat == null || this._homeLon == null || this._homeMarker) return;
        const icon = L.divIcon({
            className: 'lmd-home-marker',
            html: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                + 'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                + '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
                + '<polyline points="9 22 9 12 15 12 15 22"/></svg>',
            iconSize: [26, 26],
            iconAnchor: [13, 13],
        });
        this._homeMarker = L.marker([this._homeLat, this._homeLon], { icon, zIndexOffset: 1000 })
            .bindTooltip('Home', { direction: 'top', offset: [0, -12] })
            .addTo(this._map);
    }

    /** Map-header home button -- same "Center on home" the core
     * Dashboard's NODE MAP offers, zoom level 14 to match. */
    _centerOnHome() {
        if (!this._map || this._homeLat == null || this._homeLon == null) return;
        this._map.setView([this._homeLat, this._homeLon], 14);
    }

    _updateMemberMarkers(located) {
        this._located = located;
        this._memberMarkers.clearLayers();
        const now = Date.now() / 1000;
        located.forEach((m) => {
            // A click opens the member's side panel, as a click in the list does; the name shows on hover.
            const recent = m.last_heard && now - m.last_heard < LMD_ONLINE_S;
            L.circleMarker([m.lat, m.lon], { radius: 6, fillColor: '#ff6b35', fillOpacity: 0.85,
                color: recent ? '#00e5a0' : '#ffffff', weight: recent ? 2 : 1 })
                .bindTooltip(this._esc(m.name || `Member ${m.member}`), { direction: 'top', offset: [0, -6] })
                .on('click', () => this._openMemberDrawer(m))
                .addTo(this._memberMarkers);
        });
        this._fit();
    }

    _mapResized() {
        const el = this._q('#lmd-member-map');
        if (!this._map || !el || !el.clientWidth || !el.clientHeight) return;
        this._map.invalidateSize();
        this._fit();
    }

    /** Show everything, once per set of members on the map: not again at every poll, which would undo the user's zoom. */
    _fit() {
        const el = this._q('#lmd-member-map');
        const located = this._located || [];
        const key = located.length ? located.map((m) => m.member).sort().join() : `home ${this._homeLat}`;
        if (!this._map) return;
        if (!el.clientWidth || !el.clientHeight) {   // no size yet: Leaflet needs some view, _mapResized() does it right
            if (!this._map._loaded) this._map.setView([this._homeLat ?? 52.2, this._homeLon ?? 5.3], 10);
            return;
        }
        if (key === this._fittedFor) return;
        this._fittedFor = key;
        const points = located.map((m) => [m.lat, m.lon]);
        if (!points.length) this._centerOnHome();
        else if (points.length === 1) this._map.setView(points[0], 12);
        else this._map.fitBounds(points, { padding: [30, 30], maxZoom: 13 });
    }

    // --- Map header actions (basemap / home / expand) ---------------------
    // Same behaviours/reasoning as reticulum_dashboard.js's own equivalent
    // section -- reimplemented here rather than reused for the same
    // "hardwired to a different map's id/an unscoped .dashboard query"
    // reasons that file's own header comment gives.

    _loadBasemapPref() {
        try {
            const v = localStorage.getItem('meshpoint.nodeMap.basemap');
            if (v === 'light') return true;
            if (v === 'dark') return false;
        } catch (_) {}
        return document.documentElement.getAttribute('data-theme') === 'light';
    }

    _applyBasemap() {
        const el = this._q('#lmd-member-map');
        if (!el) return;
        el.classList.toggle('map--basemap-light', this._basemapLight);
        el.classList.toggle('map--basemap-dark', !this._basemapLight);
    }

    _toggleBasemap() {
        this._basemapLight = !this._basemapLight;
        try {
            localStorage.setItem('meshpoint.nodeMap.basemap', this._basemapLight ? 'light' : 'dark');
        } catch (_) {}
        this._syncBasemapBtn(this._basemapLight);
    }

    _syncBasemapBtn(light) {
        this._applyBasemap();
        const btn = this._q('#lmd-map-basemap-btn');
        if (!btn) return;
        btn.innerHTML = window.themeGlyph ? window.themeGlyph(light ? 'moon' : 'sun', 14) : '';
        btn.title = light ? 'Darken the map' : 'Lighten the map';
    }

    _toggleExpand() {
        const dash = this._q('.dashboard');
        const btn = this._q('#lmd-map-expand-btn');
        if (!dash) return;
        const expanded = dash.classList.toggle('dashboard--map-expanded');
        if (btn) {
            btn.textContent = expanded ? '⤡' : '⤢';
            btn.title = expanded ? 'Collapse map' : 'Expand map';
        }
        setTimeout(() => this._mapResized(), 50);
    }

    // --- Live activity ticker ---------------------------------------------
    // No WebSocket to push new rows (localmesh has none -- see this file's
    // header comment), so this polls /api/localmesh/activity every 10s
    // (_load()'s own cadence) and diffs against what it already rendered,
    // prepending only the genuinely-new rows with the same flash animation
    // reticulum_dashboard.js's own WS-pushed rows get
    // (.packet-row--new / @keyframes packetFlash, core dashboard.css,
    // loaded globally).

    _activityKey(a) {
        // receiver+member+counter+time: unique per packet per receiver --
        // two receivers legitimately produce two distinct rows for the
        // same over-the-air packet (views.py's activity() keeps one row
        // per receiver that heard it), so receiver has to be part of the
        // key, not just member+counter.
        return `${a.receiver}|${a.member}|${a.counter}|${a.time}`;
    }

    async _loadActivity() {
        try {
            const r = await fetch(`/api/localmesh/activity?limit=${LMD_TICKER_LIMIT}`, { credentials: 'same-origin' });
            if (!r.ok) return;
            const rows = await r.json();   // newest first
            if (!this._seeded) {
                this._seeded = true;
                this._activity = rows;
                rows.forEach((a) => this._activitySeen.add(this._activityKey(a)));
                this._renderTickerSeed(rows);
                return;
            }
            // New rows are the ones not already in _activitySeen -- walk
            // oldest-to-newest within this batch so they prepend in the
            // right order (each insertBefore puts the newest on top).
            const fresh = rows.filter((a) => !this._activitySeen.has(this._activityKey(a))).reverse();
            fresh.forEach((a) => this._prependTickerRow(a));
        } catch (_) {}
    }

    _renderTickerSeed(rows) {
        const tbody = this._q('#lmd-ticker-tbody');
        const empty = this._q('#lmd-ticker-empty');
        if (!tbody) return;
        if (!rows.length) {
            if (empty) empty.style.display = '';
            return;
        }
        if (empty) empty.style.display = 'none';
        tbody.innerHTML = rows.map((a) => this._rowHtml(a)).join('');
        this._syncCount();
    }

    _prependTickerRow(a) {
        const tbody = this._q('#lmd-ticker-tbody');
        if (!tbody) return;
        const empty = this._q('#lmd-ticker-empty');
        if (empty) empty.style.display = 'none';

        this._activitySeen.add(this._activityKey(a));
        this._activity.unshift(a);
        if (this._activity.length > LMD_TICKER_LIMIT) {
            const dropped = this._activity.splice(LMD_TICKER_LIMIT);
            dropped.forEach((d) => this._activitySeen.delete(this._activityKey(d)));
        }

        const tr = document.createElement('tr');
        tr.className = 'lw-pkt-row packet-row--new';
        tr.dataset.lmdKey = this._activityKey(a);
        tr.title = 'Click for details';
        tr.innerHTML = this._rowInner(a);
        tr.addEventListener('animationend', () => tr.classList.remove('packet-row--new'));
        tbody.insertBefore(tr, tbody.firstChild);

        while (tbody.children.length > LMD_TICKER_LIMIT) {
            tbody.removeChild(tbody.lastChild);
        }
        this._syncCount();
    }

    _rowHtml(a) {
        return `<tr class="lw-pkt-row" data-lmd-key="${this._esc(this._activityKey(a))}" title="Click for details">${this._rowInner(a)}</tr>`;
    }

    _rowInner(a) {
        const P = window.LocalMeshPanel;
        const esc = P ? P.esc : (s) => String(s ?? '');
        const clock = P ? P.clock : (t) => (t ? new Date(t * 1000).toLocaleTimeString() : '--');
        const signal = P ? P.signal : (rssi) => (rssi == null ? '--' : `${rssi} dBm`);
        const dec = P ? P.decisionOf(a) : { label: a.decision, badge: 'rejoin' };
        const from = a.name ? `${esc(a.name)} (${esc(a.sender)})` : esc(a.sender);
        return `
            <td class="lw-time">${esc(clock(a.time))}</td>
            <td>${esc(a.type)}</td>
            <td>${from}</td>
            <td>${esc(this._pathText(a))}</td>
            <td class="lw-r">${a.hops}</td>
            <td>${esc(signal(a.rssi, a.snr))}</td>
            <td><span class="lw-badge lw-badge--${dec.badge}">${esc(dec.label)}</span></td>
            <td class="lmd-details">${esc(this._detailText(a))}</td>
        `;
    }

    _pathText(a) {
        const nodes = (a.path || []).map((h) => this._nodeName(h.node));
        if (nodes.length) return nodes.join(' → ');
        return a.member >= 0xFF0000 && a.via && a.via !== a.member - 0xFF0000 ? `via ${this._nodeName(a.via)}` : 'direct';
    }

    /** What we know of the contents: a repeater's announce or opened status; nothing for the rest (encrypted). */
    _detailText(a) {
        const s = a.repeater_status;
        if (s) {
            const up = s.uptime_h >= 48 ? `${Math.floor(s.uptime_h / 24)} d` : `${s.uptime_h} h`;
            return [s.battery_mv ? `${(s.battery_mv / 1000).toFixed(2)} V` : '', `up ${up}`, s.version,
                `${(s.neighbors || []).length} neighbours`].filter(Boolean).join(', ');
        }
        if (a.announce) return `${a.announce.version}, region ${a.announce.region}`;
        return '--';
    }

    _syncCount() { this._setText('lmd-ticker-count', this._activity.length); }

    /** Same detail popup styling (pdm-*) the localmesh plugin's own
     * Activity tab row click opens (localmesh_panel.js's own
     * _openDetail('activity', r)) -- rebuilt here rather than called into,
     * since that method is private to that page's own component instance.
     * window.LocalMeshPanel.showModal() is the one piece of chrome that
     * *is* public, so this reuses that instead of duplicating the overlay/
     * modal DOM too. */
    _openActivityDetail(a) {
        const P = window.LocalMeshPanel;
        if (!P || !P.showModal) return;
        const esc = P.esc;
        const dec = P.decisionOf(a);
        const bad = dec.badge === 'undecrypted';
        const good = dec.badge === 'join';
        const row = (k, v, cls = '') => (v == null || v === '' ? '' : `
            <div class="pdm-row"><span class="pdm-row__key">${k}</span>
            <span class="pdm-row__val${cls ? ` pdm-row__val--${cls}` : ''}">${v}</span></div>`);
        const layer = (label, rows) => (rows.trim() ? `
            <section class="pdm-layer"><div class="pdm-layer__label">${label}</div>
            <div class="pdm-layer__rows">${rows}</div></section>` : '');
        const when = (t) => (t ? esc(new Date(t * 1000).toLocaleString()) : null);
        const path = P.pathText ? P.pathText(a.path)
            : (a.path || []).map((h) => `node ${h.node} (SNR ${h.snr} dB)`).join(' → ');
        const title = `${esc(a.type)} packet from ${esc(a.name || a.sender)}`;
        const body = layer('Decision', row('Decision', esc(dec.label), bad ? 'bad' : good ? 'good' : '')
                + row('Meaning', esc(dec.text)))
            + layer('Packet', row('Type', esc(a.type)) + row('Sender', esc(a.sender)) + row('Name', esc(a.name))
                + row('Counter', a.counter) + row('Heard', when(a.time)) + row('Receiver', esc(a.receiver)))
            + layer('Status', (P.statusRows ? P.statusRows(a.repeater_status) : []).map(([k, v]) => row(k, v && esc(v))).join(''))
            + layer('Announce', (P.announceRows ? P.announceRows(a) : []).map(([k, v]) => row(k, esc(v))).join(''))
            + layer('Route', row('Routed', a.routed ? 'yes (follows a learned path)' : 'no (flood)')
                + row('Path', esc(path) || null)
                + row('Hops left', a.hops == null ? null : `${a.hops} (counts down by one at every repeater)`)
                + row('Previous hop', a.via ? esc(P.nodeLabel ? P.nodeLabel(a.via, a.via_name) : `node ${a.via}`) : null)
                + row('Signal', esc(P.signal(a.rssi, a.snr))));
        P.showModal(title, body);
    }

    _esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    }

    _q(sel) { return this._root ? this._root.querySelector(sel) : null; }

    _setText(id, val) {
        const el = this._q(`#${id}`);
        if (el) el.textContent = val;
    }
}

window.registerSidebarPage({
    route: 'localmesh-dashboard',
    make: () => new LocalMeshDashboard(),
});
