/**
 * LocalMesh page (sidebar, Networks).
 *
 * Same layout as the DAPNET and Reticulum pages and the same core CSS (lw-* from lorawan.css, cfg-* from
 * configuration.css): a summary card per receiver at the top, then tabs. The tab list is LM_TABS below; Members,
 * Messages, Activity and Send follow in later steps. Viewers see the read-only tabs; Settings (and later Send) are
 * admin-only, like on the other pages. The open tab is remembered per browser.
 *
 * Receiver data comes from GET /api/localmesh/status (routes.py): per receiver whether it is connected, its INFO
 * (firmware, board, node, radio) and its last STATUS (battery, counters, noise floor), every 10 s.
 */

const LM_TAB_STORE_KEY = 'meshpoint.lmTab';
const LM_TABS = [
    { id: 'receiver', label: 'Receiver', admin: false },
    { id: 'settings', label: 'Settings', admin: true },
];

class LocalMeshPanel {
    constructor() {
        this._root = null;
        this._timer = null;
        this._devices = [];
        this._isAdmin = window.meshpointIdentity?.role !== 'viewer';
        let stored = null;
        try { stored = localStorage.getItem(LM_TAB_STORE_KEY); } catch (_) {}
        this._tab = this._allowed(stored) ? stored : 'receiver';
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
                    </div>
                    <div data-lm-view="receiver">
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
    }

    async _refresh() {
        try {
            const res = await fetch('/api/localmesh/status', { credentials: 'same-origin' });
            if (res.ok) this._devices = await res.json();
        } catch (_e) { /* keep the last render */ }
        if (!Array.isArray(this._devices)) this._devices = [];
        this._renderCards();
        this._renderReceivers();
    }

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
        return `<article class="cfg-card">${head}
            <div class="cfg-mc-readouts">${LocalMeshPanel.readouts(d).map(([label, value]) => `
                <div class="cfg-mc-readout">
                    <span class="cfg-mc-readout__label">${label}</span>
                    <span class="cfg-mc-readout__value">${esc(value)}</span>
                </div>`).join('')}
            </div>
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
