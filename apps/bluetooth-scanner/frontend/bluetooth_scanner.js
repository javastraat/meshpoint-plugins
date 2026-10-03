/**
 * Bluetooth Scanner -- a generic nearby-BLE-device radar.
 *
 * Start/Stop a scan, watch a live table of every advertising BLE
 * device in range (address, name, RSSI, last seen), sortable by any
 * column, click a row for a right-side detail drawer.
 *
 * Same visual system as the LoRaWAN/Meshtastic/MeshCore/Reticulum
 * protocol pages -- this renders the literal core CSS class names
 * (`lw-panel__head`/`lw-stats`/`lw-table` from lorawan.css,
 * `nd-drawer`/`nd-header`/`nd-section`/`nd-row` from node_drawer.css)
 * rather than a hand-copied approximation, the same way the Reticulum
 * plugin's own Peers drawer does (see reticulum_detail_panels.js's
 * own docstring for the reasoning). Those class names are just
 * layout/color rules with no protocol-specific behaviour baked in.
 * What stays plugin-owned is the markup construction and data --
 * this never touches NodeDrawer's own JS class or its singleton
 * `#node-drawer` element; Bluetooth devices don't have Meshtastic's
 * node_id/telemetry shape, so building a small drawer of our own is
 * the right call, not a special case of the real one.
 *
 * Vendor info (2026-10): a Vendor column (MAC vendor for public
 * addresses, else the advertisement's Bluetooth company) and drawer
 * sections for address type, manufacturer data, services, TX power and
 * appearance -- all resolved server-side from an offline database
 * (vendor_db.py), refreshable from the page. A "Hide devices not seen
 * for 2 min" checkbox (default on, remembered per browser) only filters
 * while scanning: after Stop the table keeps its last state until Clear.
 *
 * Polling, not WebSocket, same as every listener-family plugin panel
 * (RTL433, ACARS, ...): a plain 2s poll loop that starts on show()
 * and stops on hide().
 */
(function () {
    'use strict';

    const API = '/api/bluetooth-scanner';

    function esc(value) {
        const el = document.createElement('span');
        el.textContent = value == null ? '' : String(value);
        return el.innerHTML;
    }

    function hashColor(str) {
        let hash = 0;
        for (let i = 0; i < str.length; i++) {
            hash = str.charCodeAt(i) + ((hash << 5) - hash);
        }
        return `hsl(${Math.abs(hash) % 360}, 55%, 45%)`;
    }

    function fullTime(ts) {
        if (!Number.isFinite(ts)) return '--';
        return new Date(ts * 1000).toLocaleString([], { hour12: false });
    }

    // Exact same logic as reticulum_panel.js's own _fmtTime -- same-day
    // shows just a clock time, anything older shows month/day too. Not
    // relative ("Xs ago"): every other protocol table in this app
    // (LoRaWAN, Meshtastic, MeshCore, Reticulum's own Peers) uses this
    // same absolute-time convention for its time columns.
    function smartTime(ts) {
        if (!Number.isFinite(ts)) return '--';
        const d = new Date(ts * 1000);
        const now = new Date();
        const sameDay = d.getFullYear() === now.getFullYear()
            && d.getMonth() === now.getMonth()
            && d.getDate() === now.getDate();
        if (sameDay) {
            return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        }
        return d.toLocaleString([], {
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit', hour12: false,
        });
    }

    // Same RSSI tier breaks as node_drawer.js's _signalQuality -- same
    // physical quantity (a receive signal strength in dBm), same
    // meaning, so the same thresholds and the same visual language.
    function signalQuality(rssi) {
        if (!Number.isFinite(rssi)) return null;
        if (rssi > -60) return 'Excellent';
        if (rssi >= -75) return 'Good';
        if (rssi >= -90) return 'Fair';
        return 'Poor';
    }

    /** Right-side slide-in detail panel for a single device row -- same
     * nd-drawer/nd-header/nd-section chrome as every other protocol's
     * node/peer drawer, own small markup for our own small data shape. */
    class BluetoothDeviceDrawer {
        open(device) {
            this.close();

            const backdrop = document.createElement('div');
            backdrop.className = 'nd-backdrop';
            backdrop.addEventListener('click', () => this.close());

            const drawer = document.createElement('div');
            drawer.className = 'nd-drawer';
            drawer.addEventListener('click', (e) => e.stopPropagation());

            const name = esc(device.name || device.address);
            const shortLabel = esc((device.address || '').replace(/[^0-9A-F]/gi, '').slice(0, 2)).toUpperCase();
            const color = hashColor(device.address || '');

            drawer.innerHTML = `
                <div class="nd-header">
                    <div class="nd-header__left">
                        <div class="nd-avatar" style="background:${color}">${shortLabel || '??'}</div>
                        <div class="nd-header__info">
                            <div class="nd-header__name">${name}</div>
                            <div class="nd-header__id">${esc(device.address)}</div>
                        </div>
                    </div>
                    <button class="nd-close" title="Close">&times;</button>
                </div>
                <div class="nd-body"></div>
            `;
            drawer.querySelector('.nd-close').addEventListener('click', () => this.close());

            document.body.appendChild(backdrop);
            document.body.appendChild(drawer);
            this._backdrop = backdrop;
            this._drawer = drawer;
            requestAnimationFrame(() => {
                backdrop.classList.add('nd-backdrop--visible');
                drawer.classList.add('nd-drawer--open');
            });

            this._renderSections(device);
        }

        close() {
            if (this._drawer) this._drawer.remove();
            if (this._backdrop) this._backdrop.remove();
            this._drawer = null;
            this._backdrop = null;
        }

        _renderSections(device) {
            const body = this._drawer.querySelector('.nd-body');
            const kind = device.address_kind || 'unknown';
            const kindNote = kind === 'public'
                ? 'public (vendor-assigned MAC)'
                : `${kind} — no real MAC, so no vendor from the address`;
            body.appendChild(this._buildSection('Device Info', [
                ['Address', esc(device.address)],
                ['Address type', esc(kindNote)],
                ['Name', device.name ? esc(device.name) : '(none advertised)'],
                ['First seen', esc(fullTime(device.first_seen))],
                ['Last seen', esc(fullTime(device.last_seen))],
            ]));

            const ident = [];
            if (device.mac_vendor) ident.push(['Vendor (MAC)', esc(device.mac_vendor)]);
            (device.companies || []).forEach((c) => {
                const label = c.name || 'Unknown company';
                const extra = c.apple_type ? ` · ${c.apple_type}` : '';
                ident.push(['Manufacturer', `${esc(label + extra)} <span class="bts-muted">${esc(c.id_hex)}</span>`]);
            });
            if (device.appearance_name) ident.push(['Appearance', esc(device.appearance_name)]);
            const named = (device.services || []).filter((x) => x.name);
            if (named.length) ident.push(['Services', named.map((x) => esc(x.name)).join('<br>')]);
            body.appendChild(this._buildSection('Identification', ident));

            const adv = [];
            if (Number.isFinite(device.tx_power)) adv.push(['TX power', `${device.tx_power} dBm`]);
            (device.companies || []).forEach((c) => {
                adv.push([`Mfr data ${c.id_hex}`, `<code class="bts-hex">${esc(c.data)}</code>`]);
            });
            (device.services || []).forEach((x) => {
                const data = (device.service_data || {})[x.uuid];
                adv.push([x.name ? `UUID (${x.name})` : 'UUID',
                    `<code class="bts-hex">${esc(x.uuid)}</code>${data ? `<br><code class="bts-hex">${esc(data)}</code>` : ''}`]);
            });
            body.appendChild(this._buildSection('Advertisement', adv));

            const quality = signalQuality(device.rssi);
            const signalRows = [];
            if (Number.isFinite(device.rssi)) signalRows.push(['RSSI', `${device.rssi} dBm`]);
            if (quality) signalRows.push(['Quality', quality]);
            body.appendChild(this._buildSection('Signal', signalRows));
        }

        _buildSection(title, rows) {
            const section = document.createElement('div');
            section.className = 'nd-section';

            const header = document.createElement('div');
            header.className = 'nd-section__header';
            header.innerHTML = `<span class="nd-section__title">${esc(title)}</span>
                <span class="nd-section__arrow">▼</span>`;

            const content = document.createElement('div');
            content.className = 'nd-section__content';

            if (!rows.length) {
                content.innerHTML = '<div class="nd-section__empty">No data available</div>';
            } else {
                rows.forEach(([label, value]) => {
                    const row = document.createElement('div');
                    row.className = 'nd-row';
                    row.innerHTML = `<span class="nd-row__label">${esc(label)}</span>
                        <span class="nd-row__value">${value}</span>`;
                    content.appendChild(row);
                });
            }

            header.addEventListener('click', () => {
                const visible = content.style.display !== 'none';
                content.style.display = visible ? 'none' : '';
                header.querySelector('.nd-section__arrow').textContent = visible ? '▶' : '▼';
            });

            section.appendChild(header);
            section.appendChild(content);
            return section;
        }
    }

    class BluetoothScannerPage {
        constructor() {
            this._root = null;
            this._timer = null;
            this._sortKey = 'last_seen';
            this._sortDir = 'desc';
            this._devicesByAddress = new Map();
            this._drawer = new BluetoothDeviceDrawer();
            this._hideStale = true;
            try {
                const stored = localStorage.getItem('meshpoint.btsHideStale');
                if (stored !== null) this._hideStale = stored === '1';
            } catch (_e) { /* private mode etc. -- keep the default */ }
            this._lastBody = null;
        }

        mount(rootEl) {
            this._root = rootEl;
            rootEl.innerHTML = `
                <div class="bts-page">
                    <div class="lw-panel__head">
                        <h1 class="lw-panel__title">Bluetooth Scanner</h1>
                        <div class="lw-panel__actions">
                            <button class="terminal-button terminal-button--primary" data-start>Start scan</button>
                            <button class="terminal-button" data-stop disabled>Stop scan</button>
                            <button class="terminal-button" data-clear>Clear</button>
                        </div>
                    </div>
                    </div>
                    <div class="bts-stats">
                        <div class="stat-card">
                            <div class="stat-card__value" data-status>Stopped</div>
                            <div class="stat-card__label">Status</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card__value" data-count>0</div>
                            <div class="stat-card__label" data-count-label>Devices in range</div>
                        </div>
                        <div class="stat-card" title="Offline MAC vendor + Bluetooth SIG lists, used for the Vendor column and the device panel">
                            <div class="stat-card__value bts-db-value" data-db-value>—</div>
                            <div class="stat-card__label bts-db" data-db>Vendor database</div>
                        </div>
                    </div>
                    <p class="bts-error" data-error hidden></p>
                    <div class="lw-section">
                        <div class="panel">
                            <div class="panel__header">
                                <span>Devices</span>
                                <label class="bts-toggle" title="While scanning, hide devices that haven't advertised for 2 minutes. After Stop the table always keeps its last state until Clear.">
                                    <input type="checkbox" data-hide-stale ${this._hideStale ? 'checked' : ''}>
                                    Hide devices not seen for 2 min
                                </label>
                            </div>
                            <div class="panel__body lw-table-wrap">
                                <table class="lw-table lw-table--bluetooth">
                                    <colgroup>
                                        <col class="col-time">
                                        <col class="col-name">
                                        <col class="col-vendor">
                                        <col class="col-id">
                                        <col class="col-rssi">
                                        <col class="col-time">
                                    </colgroup>
                                    <thead>
                                        <tr>
                                            <th data-sort="last_seen">Last seen</th>
                                            <th data-sort="name">Name</th>
                                            <th data-sort="vendor">Vendor</th>
                                            <th data-sort="address">Address</th>
                                            <th class="lw-r" data-sort="rssi">RSSI</th>
                                            <th data-sort="first_seen">First seen</th>
                                        </tr>
                                    </thead>
                                    <tbody data-rows></tbody>
                                </table>
                                <p class="lw-empty" data-empty hidden>No devices seen yet.</p>
                            </div>
                        </div>
                    </div>
                </div>
            `;

            this._startBtn = rootEl.querySelector('[data-start]');
            this._stopBtn = rootEl.querySelector('[data-stop]');
            this._clearBtn = rootEl.querySelector('[data-clear]');
            this._statusEl = rootEl.querySelector('[data-status]');
            this._countEl = rootEl.querySelector('[data-count]');
            this._errorEl = rootEl.querySelector('[data-error]');
            this._rowsEl = rootEl.querySelector('[data-rows]');
            this._emptyEl = rootEl.querySelector('[data-empty]');
            this._countLabelEl = rootEl.querySelector('[data-count-label]');
            this._dbEl = rootEl.querySelector('[data-db]');
            this._dbValueEl = rootEl.querySelector('[data-db-value]');
            rootEl.querySelector('[data-hide-stale]').addEventListener('change', (e) => {
                this._hideStale = e.target.checked;
                try { localStorage.setItem('meshpoint.btsHideStale', this._hideStale ? '1' : '0'); } catch (_e) { /* ignore */ }
                if (this._lastBody) this._render(this._lastBody);
            });
            this._dbEl.addEventListener('click', (e) => {
                if (e.target.closest('[data-db-refresh]')) this._refreshDb(e.target.closest('[data-db-refresh]'));
            });

            this._startBtn.addEventListener('click', () => this._start());
            this._stopBtn.addEventListener('click', () => this._stop());
            this._clearBtn.addEventListener('click', () => this._clear());
            rootEl.querySelectorAll('th[data-sort]').forEach((th) => {
                th.addEventListener('click', () => this._onSortClick(th.dataset.sort));
                th.classList.add('bts-sortable');
            });
            this._updateSortIndicators();
        }

        show() {
            this._refresh();
            this._timer = window.setInterval(() => this._refresh(), 2000);
        }

        hide() {
            if (this._timer) {
                window.clearInterval(this._timer);
                this._timer = null;
            }
            this._drawer.close();
        }

        async _refresh() {
            try {
                const res = await fetch(`${API}/status`, { credentials: 'same-origin' });
                if (!res.ok) return;
                const body = await res.json();
                this._render(body);
            } catch (_e) {
                // Network blip -- next tick recovers.
            }
        }

        _render(body) {
            this._statusEl.textContent = body.running ? 'Scanning' : 'Stopped';
            this._statusEl.classList.toggle('bts-status--live', !!body.running);
            this._startBtn.disabled = !!body.running;
            this._stopBtn.disabled = !body.running;
            this._lastBody = body;

            if (body.last_error) {
                this._errorEl.hidden = false;
                this._errorEl.textContent = body.last_error;
            } else {
                this._errorEl.hidden = true;
            }

            this._renderDb(body.vendor_db);
            const now = Date.now() / 1000;
            const staleAfter = body.stale_after_seconds || 120;
            const filtering = this._hideStale && body.running;
            const devices = (body.devices || []).filter((d) => !filtering || now - d.last_seen <= staleAfter);
            this._countEl.textContent = devices.length;
            this._countLabelEl.textContent = filtering ? 'Devices in range' : 'Devices seen';
            this._devicesByAddress = new Map(devices.map((d) => [d.address, d]));
            devices.sort((a, b) => this._compare(a, b));

            if (!devices.length) {
                this._rowsEl.innerHTML = '';
                if (this._emptyEl) this._emptyEl.hidden = false;
                return;
            }
            if (this._emptyEl) this._emptyEl.hidden = true;

            this._rowsEl.innerHTML = devices.map((d) => `
                <tr class="lw-pkt-row" data-address="${esc(d.address)}" title="Click for details">
                    <td class="lw-time">${esc(smartTime(d.last_seen))}</td>
                    <td class="mt-name">${esc(d.name || '—')}</td>
                    <td class="bts-vendor" title="${esc(d.vendor_source ? `from ${d.vendor_source}` : (d.address_kind && d.address_kind !== 'public' ? 'random address, no vendor advertised' : ''))}">${esc(d.vendor || '—')}</td>
                    <td class="lw-id">${esc(d.address)}</td>
                    <td class="lw-num">${Number.isFinite(d.rssi) ? `${d.rssi} dBm` : '—'}</td>
                    <td class="lw-time">${esc(smartTime(d.first_seen))}</td>
                </tr>
            `).join('');

            this._rowsEl.querySelectorAll('tr[data-address]').forEach((tr) => {
                tr.addEventListener('click', () => {
                    const device = this._devicesByAddress.get(tr.dataset.address);
                    if (device) this._drawer.open(device);
                });
            });
        }

        _renderDb(db) {
            if (!this._dbEl || !db) return;
            if (this._dbBusy) return;
            if (db.present) {
                const when = db.updated_at ? new Date(db.updated_at * 1000).toLocaleDateString() : '?';
                const n = (db.counts && db.counts.mac_prefixes) || 0;
                this._dbValueEl.textContent = when;
                this._dbValueEl.title = `${n.toLocaleString()} MAC prefixes`;
                this._dbEl.innerHTML = 'Vendor database · <button type="button" class="bts-link" data-db-refresh>Refresh</button>';
            } else {
                this._dbValueEl.textContent = 'Built-in only';
                this._dbValueEl.title = 'No MAC vendors yet; common company and service names still work';
                this._dbEl.innerHTML = 'Vendor database · <button type="button" class="bts-link" data-db-refresh>Download</button>';
            }
        }

        async _refreshDb(btn) {
            this._dbBusy = true;
            btn.disabled = true;
            this._dbValueEl.textContent = 'Downloading…';
            try {
                const res = await fetch(`${API}/vendor-db/refresh`, { method: 'POST', credentials: 'same-origin' });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    this._dbValueEl.textContent = 'Failed';
                    this._dbValueEl.title = res.status === 403 ? 'Admin role required to refresh.' : (err.detail || `HTTP ${res.status}`);
                    window.setTimeout(() => { this._dbBusy = false; }, 6000);
                    return;
                }
            } catch (e) {
                this._dbValueEl.textContent = 'Failed';
                this._dbValueEl.title = e.message || 'Network error.';
                window.setTimeout(() => { this._dbBusy = false; }, 6000);
                return;
            }
            this._dbBusy = false;
            this._refresh();
        }

        _compare(a, b) {
            const key = this._sortKey;
            const dir = this._sortDir === 'asc' ? 1 : -1;
            const av = a[key];
            const bv = b[key];
            if (av == null && bv == null) return 0;
            if (av == null) return 1;
            if (bv == null) return -1;
            if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
            return String(av).localeCompare(String(bv)) * dir;
        }

        _onSortClick(key) {
            if (this._sortKey === key) {
                this._sortDir = this._sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                this._sortKey = key;
                this._sortDir = 'asc';
            }
            this._updateSortIndicators();
            this._refresh();
        }

        _updateSortIndicators() {
            if (!this._root) return;
            this._root.querySelectorAll('th[data-sort]').forEach((th) => {
                th.classList.remove('bts-sort--asc', 'bts-sort--desc');
                if (th.dataset.sort === this._sortKey) {
                    th.classList.add(this._sortDir === 'asc' ? 'bts-sort--asc' : 'bts-sort--desc');
                }
            });
        }

        async _start() {
            this._startBtn.disabled = true;
            try {
                await fetch(`${API}/start`, { method: 'POST', credentials: 'same-origin' });
            } finally {
                this._refresh();
            }
        }

        async _stop() {
            this._stopBtn.disabled = true;
            try {
                await fetch(`${API}/stop`, { method: 'POST', credentials: 'same-origin' });
            } finally {
                this._refresh();
            }
        }

        async _clear() {
            await fetch(`${API}/clear`, { method: 'POST', credentials: 'same-origin' });
            this._refresh();
        }
    }

    window.registerSidebarPage({
        route: 'bluetooth-scanner',
        make: () => new BluetoothScannerPage(),
    });
}());
