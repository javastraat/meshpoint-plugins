/**
 * Bluetooth Scanner -- a generic nearby-BLE-device radar.
 *
 * Start/Stop a scan, watch a live table of every advertising BLE
 * device in range (address, name, RSSI, last seen), sortable by any
 * column. Polls `/api/bluetooth-scanner/status` every 2s while the
 * page is visible -- same shape as every other listener-family
 * plugin's panel (RTL433, ACARS, ...): no WebSocket, just a plain
 * poll loop that starts on show() and stops on hide().
 */
(function () {
    'use strict';

    const API = '/api/bluetooth-scanner';

    class BluetoothScannerPage {
        constructor() {
            this._root = null;
            this._timer = null;
            this._sortKey = 'last_seen';
            this._sortDir = 'desc';
        }

        mount(rootEl) {
            this._root = rootEl;
            rootEl.innerHTML = `
                <div class="plugin-page bts-page">
                    <header class="bts-head">
                        <h1>Bluetooth Scanner</h1>
                        <p class="bts-hint">
                            Generic nearby-BLE-device radar. Address, name, and
                            signal strength for every advertising device in range.
                        </p>
                    </header>
                    <div class="bts-controls">
                        <button class="terminal-button terminal-button--primary" data-start>Start scan</button>
                        <button class="terminal-button" data-stop disabled>Stop scan</button>
                        <button class="terminal-button" data-clear>Clear</button>
                        <span class="bts-status" data-status>Stopped</span>
                        <span class="bts-count" data-count></span>
                    </div>
                    <p class="bts-error" data-error hidden></p>
                    <div class="bts-table-wrap">
                        <table class="bts-table">
                            <thead>
                                <tr>
                                    <th data-sort="address">Address</th>
                                    <th data-sort="name">Name</th>
                                    <th data-sort="rssi">RSSI</th>
                                    <th data-sort="last_seen">Last seen</th>
                                </tr>
                            </thead>
                            <tbody data-rows></tbody>
                        </table>
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
            this._statusEl.textContent = body.running ? 'Scanning…' : 'Stopped';
            this._statusEl.classList.toggle('bts-status--live', !!body.running);
            this._startBtn.disabled = !!body.running;
            this._stopBtn.disabled = !body.running;

            const count = body.device_count || 0;
            this._countEl.textContent = `${count} device${count === 1 ? '' : 's'}`;

            if (body.last_error) {
                this._errorEl.hidden = false;
                this._errorEl.textContent = body.last_error;
            } else {
                this._errorEl.hidden = true;
            }

            const devices = (body.devices || []).slice();
            devices.sort((a, b) => this._compare(a, b));

            if (!devices.length) {
                this._rowsEl.innerHTML = '<tr><td colspan="4" class="bts-empty">No devices seen yet.</td></tr>';
                return;
            }

            this._rowsEl.innerHTML = devices.map((d) => `
                <tr>
                    <td>${this._esc(d.address)}</td>
                    <td>${this._esc(d.name || '—')}</td>
                    <td>${Number.isFinite(d.rssi) ? `${d.rssi} dBm` : '—'}</td>
                    <td>${this._formatAge(d.last_seen)}</td>
                </tr>
            `).join('');
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

        _formatAge(ts) {
            if (!Number.isFinite(ts)) return '—';
            const secs = Math.max(0, Math.round(Date.now() / 1000 - ts));
            if (secs < 5) return 'just now';
            if (secs < 60) return `${secs}s ago`;
            return `${Math.round(secs / 60)}m ago`;
        }

        _esc(value) {
            const div = document.createElement('div');
            div.textContent = value == null ? '' : String(value);
            return div.innerHTML;
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
