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
 * messages,activity}.
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
                        </div>
                        <div class="panel__body lw-table-wrap">
                            <table class="lw-table lmd-ticker-table">
                                <colgroup>
                                    <col class="lmd-col-time"><col class="lmd-col-type"><col class="lmd-col-from">
                                    <col class="lmd-col-hops"><col class="lmd-col-signal"><col class="lmd-col-badge">
                                </colgroup>
                                <thead>
                                    <tr>
                                        <th>Time</th><th>Type</th><th>From</th>
                                        <th>Hops left</th><th>Signal</th><th>Decision</th>
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
        this._refreshTimer = setInterval(() => this._load(), 10_000);
    }

    hide() {
        clearInterval(this._refreshTimer);
        this._refreshTimer = null;
    }

    async _load() {
        await Promise.all([
            this._loadStatus(), this._loadMembers(), this._loadMessages24h(),
            this._loadActivity(), this._loadHomeLocation(),
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
    }

    _renderRepeatersList() {
        const list = this._q('#lmd-members-list');
        const empty = this._q('#lmd-members-empty');
        if (!list) return;
        const q = this._memberSearchQuery;
        const rows = q
            ? this._repeaters.filter((r) => (r.name || '').toLowerCase().includes(q) || String(r.node).includes(q))
            : this._repeaters;
        if (!rows.length) {
            list.innerHTML = '';
            if (empty) {
                empty.style.display = '';
                empty.textContent = q ? 'No repeaters match that search.' : 'No repeaters known yet.';
            }
            return;
        }
        if (empty) empty.style.display = 'none';
        const P = window.LocalMeshPanel;
        const esc = P ? P.esc : (s) => String(s ?? '');
        const ago = P ? P.ago : () => '--';
        list.innerHTML = rows.slice(0, LMD_MEMBER_LIST_LIMIT).map((r) => `
            <div class="lmd-member-row" data-node="${esc(r.node)}" title="Repeater ${esc(r.node)}${r.region != null ? `, region ${esc(r.region)}` : ''}">
                <span class="lmd-member-row__name">${esc(r.name || `Repeater ${r.node}`)}</span>
                ${r.revoked ? '<span class="lw-badge lw-badge--undecrypted">revoked</span>' : ''}
                ${r.direct ? '<span class="lw-badge lw-badge--join">direct</span>' : ''}
                <span class="lmd-member-row__time">${r.last_heard ? esc(ago(r.last_heard)) : '--'}</span>
            </div>
        `).join('');
    }

    _renderMembersList() {
        if (this._listView === 'repeaters') return this._renderRepeatersList();
        const list = this._q('#lmd-members-list');
        const empty = this._q('#lmd-members-empty');
        if (!list) return;
        if (!this._members.length) {
            list.innerHTML = '';
            if (empty) empty.style.display = '';
            return;
        }
        const q = this._memberSearchQuery;
        // Unfiltered this list is the *entire roster* (listed + ever
        // heard) -- fine at 42 members, not at 2000: most "listed"
        // members on a real network were never actually heard by this
        // specific receiver, and a glanceable live view has no business
        // showing them at all (same reasoning as reticulum-dashboard's
        // own Peers list, which is implicitly "peers heard/announced",
        // not "every theoretically possible peer"). Default view: only
        // members heard at least once or with a known name (the board
        // remembers names across restarts), newest first (already
        // the API's own sort). A search query reaches the full roster
        // though -- if you know a name/number to look for, you shouldn't
        // need it to have been heard recently to find it.
        const filtered = q
            ? this._members.filter((m) =>
                (m.name || '').toLowerCase().includes(q) || String(m.member).includes(q))
            // own's last_heard is never set (a receiver doesn't hear itself), so it needs its own clause here
            : this._members.filter((m) => m.last_heard != null || m.own || m.name);
        if (!filtered.length) {
            list.innerHTML = '';
            if (empty) {
                empty.style.display = '';
                empty.textContent = q ? 'No members match that search.' : 'No members heard yet.';
            }
            return;
        }
        if (empty) empty.style.display = 'none';
        const P = window.LocalMeshPanel;
        const esc = P ? P.esc : (s) => String(s ?? '');
        const ago = P ? P.ago : () => '--';
        // Already last_heard DESC from the API -- same assumption
        // localmesh_panel.js's own Members tab relies on.
        list.innerHTML = filtered.slice(0, LMD_MEMBER_LIST_LIMIT).map((m) => `
            <div class="lmd-member-row" data-member="${esc(m.member)}" title="Member ${esc(m.member)}">
                <span class="lmd-member-row__name">${esc(m.name || `Member ${m.member}`)}</span>
                ${m.blocked ? '<span class="lw-badge lw-badge--undecrypted">blocked</span>' : ''}
                ${m.own ? '<span class="lw-badge lw-badge--data">this Meshpoint</span>' : ''}
                <span class="lmd-member-row__time">${m.last_heard ? esc(ago(m.last_heard)) : '--'}</span>
            </div>
        `).join('');
    }

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
            requestAnimationFrame(() => this._initMap(el, located));
            return;
        }
        this._updateMemberMarkers(located);
        if (wasHidden) requestAnimationFrame(() => this._map && this._map.invalidateSize());
    }

    _initMap(el, located) {
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
        this._updateMemberMarkers(located);
        if (!located.length && this._homeLat != null) this._centerOnHome();
        requestAnimationFrame(() => this._map && this._map.invalidateSize());
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
        this._memberMarkers.clearLayers();
        located.forEach((m) => {
            // A click opens the member's side panel, as a click in the list does; the name shows on hover.
            L.marker([m.lat, m.lon])
                .bindTooltip(m.name || `Member ${m.member}`, { direction: 'top', offset: [0, -28] })
                .on('click', () => this._openMemberDrawer(m))
                .addTo(this._memberMarkers);
        });
        this._fitBounds(located);
    }

    _fitBounds(located) {
        if (!this._map) return;
        const bounds = located.map((m) => [m.lat, m.lon]);
        if (!bounds.length) return;
        if (bounds.length === 1) this._map.setView(bounds[0], 12);
        else this._map.fitBounds(bounds, { padding: [30, 30], maxZoom: 13 });
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
        setTimeout(() => this._map && this._map.invalidateSize(), 50);
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
            <td class="lw-r">${a.hops}</td>
            <td>${esc(signal(a.rssi, a.snr))}</td>
            <td><span class="lw-badge lw-badge--${dec.badge}">${esc(dec.label)}</span></td>
        `;
    }

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
        const path = (a.path || []).map((h) => `node ${h.node} (SNR ${h.snr} dB)`).join(' → ');
        const title = `${esc(a.type)} packet from ${esc(a.name || a.sender)}`;
        const body = layer('Decision', row('Decision', esc(dec.label), bad ? 'bad' : good ? 'good' : '')
                + row('Meaning', esc(dec.text)))
            + layer('Packet', row('Type', esc(a.type)) + row('Sender', esc(a.sender)) + row('Name', esc(a.name))
                + row('Counter', a.counter) + row('Heard', when(a.time)) + row('Receiver', esc(a.receiver)))
            + layer('Announce', (P.announceRows ? P.announceRows(a) : []).map(([k, v]) => row(k, esc(v))).join(''))
            + layer('Route', row('Routed', a.routed ? 'yes (follows a learned path)' : 'no (flood)')
                + row('Path', esc(path) || null)
                + row('Hops left', a.hops == null ? null : `${a.hops} (counts down by one at every repeater)`)
                + row('Previous hop', a.via ? `node ${a.via}` : null)
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
