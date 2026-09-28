/**
 * WiFi & Network -- scan for WiFi networks and switch the Pi's WiFi
 * connection from the dashboard, no shell needed. A **Network** tab
 * shows the wired (eth0) connection's own status alongside it.
 *
 * Two tabs, one plugin -- NOT the RTL-SDR host/hook pattern (separate
 * plugins hooking into a shared host page via window.registerPageHook,
 * see frontend/sidebar/page_hook_registry.js in core). That mechanism
 * exists so independently-authored/installable plugins can share one
 * page; WiFi and Ethernet are two views of the same "network settings"
 * concern, always shipped together, nothing gained by splitting into
 * two plugin folders just to get a tabbar. Instead this reuses the
 * Reticulum plugin's own in-page tab pattern verbatim: `.lw-tabs`/
 * `.lw-tab` (lorawan.css) for the tab buttons, `data-rn-tab`/
 * `data-rn-view` for wiring instead of Reticulum's own `data-rt-*` --
 * same mechanism, same look, no cross-plugin registration involved.
 *
 * Same visual system as the LoRaWAN/Meshtastic/MeshCore/Bluetooth Scanner
 * pages otherwise too -- this renders the literal core CSS class names
 * (`lw-panel__head`/`lw-table`/`stat-card` from lorawan.css) rather than
 * a hand-copied approximation.
 *
 * No polling here, unlike most plugin pages -- a wifi scan is something
 * the user deliberately triggers (it briefly disrupts the radio), not a
 * continuously-refreshing feed. WiFi status is fetched once on show()
 * and again after a connect attempt; Ethernet status is fetched lazily,
 * the first time the Network tab is actually opened (and again on every
 * later visit to it) -- no reason to query a device the user isn't
 * looking at.
 *
 * The connect flow deliberately never claims success until the backend
 * says so: `POST /connect` blocks until nmcli itself reports the real
 * outcome (src/api/nmcli.py's wifi_connect(), core) -- a failure leaves
 * whatever was connected before untouched, and this page shows nmcli's
 * real error text rather than inventing a friendlier one that could
 * hide what actually went wrong.
 */
(function () {
    'use strict';

    const API = '/api/raspberry-network';

    function esc(value) {
        const el = document.createElement('span');
        el.textContent = value == null ? '' : String(value);
        return el.innerHTML;
    }

    function signalBars(signal) {
        if (!Number.isFinite(signal)) return '--';
        if (signal >= 75) return '▂▄▆█';
        if (signal >= 50) return '▂▄▆_';
        if (signal >= 25) return '▂▄__';
        return '▂___';
    }

    class RaspberryNetworkPage {
        constructor() {
            this._root = null;
            this._networks = [];
            this._selectedSsid = null;
            this._tab = 'wifi';
            this._ethernetLoaded = false;
        }

        mount(rootEl) {
            this._root = rootEl;
            rootEl.innerHTML = `
                <div class="plugin-page rn-page">
                    <div class="lw-panel__head">
                        <h1 class="lw-panel__title">WiFi &amp; Network</h1>
                        <div class="lw-panel__actions">
                            <button class="terminal-button terminal-button--primary" data-scan data-rn-wifi-only>Scan for networks</button>
                        </div>
                    </div>

                    <div data-rn-view="wifi">
                        <p class="rn-warning">
                            ⚠️ Connecting to a network here can disconnect this dashboard
                            if you're currently reached over WiFi and the new network
                            doesn't work -- have Ethernet or physical access available
                            as a fallback before trying an unfamiliar network.
                        </p>
                        <div class="lw-stats">
                            <div class="stat-card">
                                <div class="stat-card__value" data-current-ssid>--</div>
                                <div class="stat-card__label">Connected to</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-current-state>--</div>
                                <div class="stat-card__label">State</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-current-ip>--</div>
                                <div class="stat-card__label">IP address</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-current-gateway>--</div>
                                <div class="stat-card__label">Gateway</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-current-dns>--</div>
                                <div class="stat-card__label">DNS</div>
                            </div>
                        </div>
                        <p class="rn-error" data-error hidden></p>
                        <p class="rn-success" data-success hidden></p>
                    </div>

                    <div data-rn-view="network" hidden>
                        <div class="lw-stats">
                            <div class="stat-card">
                                <div class="stat-card__value" data-eth-connection>--</div>
                                <div class="stat-card__label">Connection</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-eth-state>--</div>
                                <div class="stat-card__label">State</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-eth-ip>--</div>
                                <div class="stat-card__label">IP address</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-eth-gateway>--</div>
                                <div class="stat-card__label">Gateway</div>
                            </div>
                            <div class="stat-card">
                                <div class="stat-card__value" data-eth-dns>--</div>
                                <div class="stat-card__label">DNS</div>
                            </div>
                        </div>
                        <p class="lw-empty" data-eth-empty hidden>No ethernet device found on this board.</p>
                    </div>

                    <div class="lw-section">
                        <div class="panel">
                            <div class="panel__header panel__header--tabs">
                                <div class="lw-tabs" role="tablist">
                                    <button class="lw-tab" type="button" role="tab" data-rn-tab="wifi">WiFi</button>
                                    <button class="lw-tab" type="button" role="tab" data-rn-tab="network">Network</button>
                                </div>
                            </div>

                            <div data-rn-view="wifi">
                                <div class="panel__body lw-table-wrap">
                                    <table class="lw-table">
                                        <thead>
                                            <tr>
                                                <th>Network</th>
                                                <th>Signal</th>
                                                <th>Security</th>
                                                <th></th>
                                            </tr>
                                        </thead>
                                        <tbody data-rows></tbody>
                                    </table>
                                    <p class="lw-empty" data-empty hidden>No networks found yet -- click Scan.</p>
                                </div>

                                <div class="rn-connect-form panel__body" data-connect-form hidden>
                                    <div class="rn-connect-form__title">Connect to <span data-connect-ssid></span></div>
                                    <label class="rn-field">
                                        <span>Password</span>
                                        <input type="password" data-password autocomplete="off">
                                        <small class="rn-field__hint">Leave blank to reconnect to an already-known network with its saved password, or to join an open network.</small>
                                    </label>
                                    <div class="rn-connect-actions">
                                        <button class="terminal-button terminal-button--primary" data-connect-submit>Connect</button>
                                        <button class="terminal-button" data-connect-cancel>Cancel</button>
                                    </div>
                                </div>
                            </div>

                            <div data-rn-view="network" hidden>
                                <div class="panel__body">
                                    <p class="rn-note">No additional Ethernet settings yet -- status only, above.</p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            `;

            this._scanBtn = rootEl.querySelector('[data-scan]');
            this._currentSsidEl = rootEl.querySelector('[data-current-ssid]');
            this._currentStateEl = rootEl.querySelector('[data-current-state]');
            this._currentIpEl = rootEl.querySelector('[data-current-ip]');
            this._currentGatewayEl = rootEl.querySelector('[data-current-gateway]');
            this._currentDnsEl = rootEl.querySelector('[data-current-dns]');
            this._errorEl = rootEl.querySelector('[data-error]');
            this._successEl = rootEl.querySelector('[data-success]');
            this._rowsEl = rootEl.querySelector('[data-rows]');
            this._emptyEl = rootEl.querySelector('[data-empty]');
            this._formEl = rootEl.querySelector('[data-connect-form]');
            this._formSsidEl = rootEl.querySelector('[data-connect-ssid]');
            this._passwordEl = rootEl.querySelector('[data-password]');

            this._ethConnectionEl = rootEl.querySelector('[data-eth-connection]');
            this._ethStateEl = rootEl.querySelector('[data-eth-state]');
            this._ethIpEl = rootEl.querySelector('[data-eth-ip]');
            this._ethGatewayEl = rootEl.querySelector('[data-eth-gateway]');
            this._ethDnsEl = rootEl.querySelector('[data-eth-dns]');
            this._ethEmptyEl = rootEl.querySelector('[data-eth-empty]');
            this._ethStatsEl = rootEl.querySelector('[data-rn-view="network"] .lw-stats');

            this._scanBtn.addEventListener('click', () => this._scan());
            rootEl.querySelector('[data-connect-submit]').addEventListener('click', () => this._connect());
            rootEl.querySelector('[data-connect-cancel]').addEventListener('click', () => this._closeForm());
            rootEl.querySelectorAll('[data-rn-tab]').forEach((btn) => {
                btn.addEventListener('click', () => this._setTab(btn.dataset.rnTab));
            });
            this._applyTab();
        }

        show() {
            this._refreshStatus();
            if (this._tab === 'network') this._refreshEthernetStatus();
        }

        hide() {
            this._closeForm();
        }

        _setTab(tab) {
            if (tab === this._tab) return;
            this._tab = tab;
            this._applyTab();
            if (tab === 'network') this._refreshEthernetStatus();
        }

        _applyTab() {
            if (!this._root) return;
            this._root.querySelectorAll('[data-rn-tab]').forEach((btn) => {
                const active = btn.dataset.rnTab === this._tab;
                btn.classList.toggle('lw-tab--active', active);
                btn.setAttribute('aria-selected', active ? 'true' : 'false');
            });
            this._root.querySelectorAll('[data-rn-view]').forEach((el) => {
                el.hidden = el.dataset.rnView !== this._tab;
            });
            this._root.querySelectorAll('[data-rn-wifi-only]').forEach((el) => {
                el.hidden = this._tab !== 'wifi';
            });
        }

        async _refreshStatus() {
            try {
                const res = await fetch(`${API}/status`, { credentials: 'same-origin' });
                if (!res.ok) return;
                const body = await res.json();
                const status = body.status;
                this._currentSsidEl.textContent = status && status.connection ? status.connection : '(not connected)';
                this._currentStateEl.textContent = status ? status.state : 'no wifi device';
                // IPv4 address comes as CIDR ("192.168.4.50/24") -- the
                // prefix length is useful detail, not clutter, so it stays.
                this._currentIpEl.textContent = (status && status.address) || '--';
                this._currentGatewayEl.textContent = (status && status.gateway) || '--';
                this._currentDnsEl.textContent = status && status.dns && status.dns.length
                    ? status.dns.join(', ') : '--';
            } catch (_e) {
                // Network blip -- next action (scan/connect) will surface anything real.
            }
        }

        async _refreshEthernetStatus() {
            try {
                const res = await fetch(`${API}/status/ethernet`, { credentials: 'same-origin' });
                if (!res.ok) return;
                const body = await res.json();
                const status = body.status;
                this._ethernetLoaded = true;
                if (!status) {
                    this._ethStatsEl.hidden = true;
                    this._ethEmptyEl.hidden = false;
                    return;
                }
                this._ethStatsEl.hidden = false;
                this._ethEmptyEl.hidden = true;
                this._ethConnectionEl.textContent = status.connection || '(not connected)';
                this._ethStateEl.textContent = status.state;
                this._ethIpEl.textContent = status.address || '--';
                this._ethGatewayEl.textContent = status.gateway || '--';
                this._ethDnsEl.textContent = status.dns && status.dns.length ? status.dns.join(', ') : '--';
            } catch (_e) {
                // Network blip -- next tab visit recovers.
            }
        }

        async _scan() {
            this._scanBtn.disabled = true;
            this._scanBtn.textContent = 'Scanning…';
            this._hideMessages();
            try {
                const res = await fetch(`${API}/scan`, { method: 'POST', credentials: 'same-origin' });
                if (!res.ok) {
                    this._showError(`Scan failed (${res.status})`);
                    return;
                }
                const body = await res.json();
                this._networks = body.networks || [];
                this._renderNetworks();
            } catch (_e) {
                this._showError('Scan failed -- network error talking to the dashboard itself.');
            } finally {
                this._scanBtn.disabled = false;
                this._scanBtn.textContent = 'Scan for networks';
            }
        }

        _renderNetworks() {
            if (!this._networks.length) {
                this._rowsEl.innerHTML = '';
                this._emptyEl.hidden = false;
                return;
            }
            this._emptyEl.hidden = true;

            const sorted = this._networks.slice().sort((a, b) => (b.signal || 0) - (a.signal || 0));
            this._rowsEl.innerHTML = sorted.map((n) => `
                <tr class="lw-pkt-row" data-ssid="${esc(n.ssid)}">
                    <td class="mt-name">${esc(n.ssid)}${n.in_use ? ' <span class="rn-in-use">(connected)</span>' : ''}</td>
                    <td class="lw-num">${signalBars(n.signal)} ${Number.isFinite(n.signal) ? esc(n.signal) : '--'}%</td>
                    <td>${n.security ? esc(n.security) : 'Open'}</td>
                    <td><button class="terminal-button" data-connect-row>${n.in_use ? 'Reconnect' : 'Connect'}</button></td>
                </tr>
            `).join('');

            this._rowsEl.querySelectorAll('[data-connect-row]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const tr = btn.closest('tr[data-ssid]');
                    this._openForm(tr.dataset.ssid);
                });
            });
        }

        _openForm(ssid) {
            this._selectedSsid = ssid;
            this._formSsidEl.textContent = ssid;
            this._passwordEl.value = '';
            this._formEl.hidden = false;
            this._passwordEl.focus();
        }

        _closeForm() {
            this._formEl.hidden = true;
            this._selectedSsid = null;
        }

        async _connect() {
            if (!this._selectedSsid) return;
            const submitBtn = this._root.querySelector('[data-connect-submit]');
            submitBtn.disabled = true;
            submitBtn.textContent = 'Connecting…';
            this._hideMessages();
            try {
                const res = await fetch(`${API}/connect`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ssid: this._selectedSsid, password: this._passwordEl.value }),
                });
                const body = await res.json().catch(() => ({}));
                if (!res.ok) {
                    // The backend already carries nmcli's own real error text
                    // (src/api/nmcli.py) -- show that, not a generic message.
                    this._showError(body.detail || `Connect failed (${res.status})`);
                    return;
                }
                this._showSuccess(`Connected to ${this._selectedSsid}.`);
                this._closeForm();
                this._refreshStatus();
            } catch (_e) {
                this._showError('Connect failed -- network error talking to the dashboard itself.');
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Connect';
            }
        }

        _showError(message) {
            this._errorEl.textContent = message;
            this._errorEl.hidden = false;
            this._successEl.hidden = true;
        }

        _showSuccess(message) {
            this._successEl.textContent = message;
            this._successEl.hidden = false;
            this._errorEl.hidden = true;
        }

        _hideMessages() {
            this._errorEl.hidden = true;
            this._successEl.hidden = true;
        }
    }

    window.registerSidebarPage({
        route: 'raspberry-network',
        make: () => new RaspberryNetworkPage(),
    });
}());
