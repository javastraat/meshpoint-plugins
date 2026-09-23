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
            body.appendChild(this._buildSection('Device Info', [
                ['Address', esc(device.address)],
                ['Name', device.name ? esc(device.name) : '(none advertised)'],
                ['First seen', esc(fullTime(device.first_seen))],
                ['Last seen', esc(fullTime(device.last_seen))],
            ]));

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
        }

        mount(rootEl) {
            this._root = rootEl;
            rootEl.innerHTML = `
                <div class="plugin-page bts-page">
                    <div class="lw-panel__head">
                        <h1 class="lw-panel__title">Bluetooth Scanner</h1>
                        <div class="lw-panel__actions">
                            <button class="terminal-button terminal-button--primary" data-start>Start scan</button>
                            <button class="terminal-button" data-stop disabled>Stop scan</button>
                            <button class="terminal-button" data-clear>Clear</button>
                        </div>
                    </div>
                    <div class="bts-stats">
                        <div class="stat-card">
                            <div class="stat-card__value" data-status>Stopped</div>
                            <div class="stat-card__label">Status</div>
                        </div>
                        <div class="stat-card">
                            <div class="stat-card__value" data-count>0</div>
                            <div class="stat-card__label">Devices in range</div>
                        </div>
                    </div>
                    <p class="bts-error" data-error hidden></p>
                    <div class="lw-section">
                        <div class="panel">
                            <div class="panel__header">Devices</div>
                            <div class="panel__body lw-table-wrap">
                                <table class="lw-table lw-table--bluetooth">
                                    <colgroup>
                                        <col class="col-time">
                                        <col class="col-name">
                                        <col class="col-id">
                                        <col class="col-rssi">
                                        <col class="col-time">
                                    </colgroup>
                                    <thead>
                                        <tr>
                                            <th data-sort="last_seen">Last seen</th>
                                            <th data-sort="name">Name</th>
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
            this._countEl.textContent = body.device_count || 0;

            if (body.last_error) {
                this._errorEl.hidden = false;
                this._errorEl.textContent = body.last_error;
            } else {
                this._errorEl.hidden = true;
            }

            const devices = (body.devices || []).slice();
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
