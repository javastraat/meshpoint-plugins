/**
 * Offline Maps -- sidebar page for the offline-map plugin.
 *
 * Settings form (mirrors offline-map-tile-downloader's CLI flags) + a
 * Start/Stop control for the subprocess. Once running, its own web UI
 * (a separate app, not embedded here) is where you actually pick an area
 * and download tiles -- this page only manages the process + its config.
 *
 * `route` below MUST match plugin.toml's [sidebar].route -- see
 * hello-world-github/frontend/hello_world.js for why.
 */
window.registerSidebarPage({
    route: 'offline-map',
    make: () => new OfflineMapPage(),
});

class OfflineMapPage {
    constructor() {
        this.root = null;
        this._pollTimer = null;
        this._modal = new window.DangerousModal();
    }

    mount(rootEl) {
        this.root = rootEl;
        rootEl.innerHTML = `
            <div class="plugin-page om-page">
                <h2>Offline Maps</h2>
                <p>Downloads OpenStreetMap tiles for offline use. Builds and runs
                    <code>offline-map-tile-downloader</code> as a background process;
                    once it's running, use its own web UI to pick an area and
                    download tiles.</p>

                <article class="om-card">
                    <h3 class="om-card__title">Downloader</h3>
                    <p class="auth-status" data-om-status aria-live="polite">Loading…</p>
                    <div class="om-card__actions">
                        <button class="terminal-button" type="button" data-om-toggle disabled>Start</button>
                        <a class="terminal-button" data-om-open href="#" target="_blank" rel="noopener" hidden>Open downloader UI</a>
                    </div>
                    <pre class="om-log" data-om-log hidden></pre>
                </article>

                <article class="om-card">
                    <h3 class="om-card__title">Dashboard map source</h3>
                    <p class="cfg-card__hint">Switches the Dashboard + Topology maps
                        between the public OSM tile server and tiles already
                        downloaded here -- takes effect immediately, no restart.</p>
                    <p class="auth-status" data-om-source-current aria-live="polite">Loading…</p>
                    <div data-om-source-list></div>
                    <p class="auth-status" data-om-source-status aria-live="polite"></p>
                </article>

                <article class="om-card">
                    <h3 class="om-card__title">Settings</h3>
                    <p class="cfg-card__hint">Applies on the next Start. Stop the downloader first to change these.</p>
                    <form class="om-form" data-om-form>
                        <label class="cfg-field cfg-field--narrow">
                            <span class="cfg-field__label">Port</span>
                            <input class="cfg-field__input" type="number" name="port" min="1" max="65535" required>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Maps directory</span>
                            <input class="cfg-field__input" type="text" name="maps_directory" required>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Presets directory</span>
                            <input class="cfg-field__input" type="text" name="presets_directory" required>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Log file</span>
                            <input class="cfg-field__input" type="text" name="log_file" required>
                        </label>
                        <label class="cfg-field cfg-field--narrow">
                            <span class="cfg-field__label">Max workers</span>
                            <input class="cfg-field__input" type="number" name="max_workers" min="1" max="500" required>
                        </label>
                        <label class="cfg-field cfg-field--narrow">
                            <span class="cfg-field__label">Rate limit (tiles/s)</span>
                            <input class="cfg-field__input" type="number" name="rate_limit" min="1" max="1000" required>
                        </label>
                        <label class="cfg-field cfg-field--narrow">
                            <span class="cfg-field__label">Max retries</span>
                            <input class="cfg-field__input" type="number" name="max_retries" min="1" max="50" required>
                        </label>
                        <label class="cfg-field cfg-field--toggle">
                            <input type="checkbox" name="quiet">
                            <span class="cfg-field__label">Quiet (suppress log output)</span>
                        </label>
                        <div class="cfg-card__actions">
                            <button class="terminal-button" type="submit">Save settings</button>
                        </div>
                    </form>
                    <p class="auth-status" data-om-settings-status aria-live="polite"></p>
                </article>
            </div>
        `;

        this.statusEl = rootEl.querySelector('[data-om-status]');
        this.toggleBtn = rootEl.querySelector('[data-om-toggle]');
        this.openLink = rootEl.querySelector('[data-om-open]');
        this.logEl = rootEl.querySelector('[data-om-log]');
        this.formEl = rootEl.querySelector('[data-om-form]');
        this.settingsStatusEl = rootEl.querySelector('[data-om-settings-status]');
        this.sourceCurrentEl = rootEl.querySelector('[data-om-source-current]');
        this.sourceListEl = rootEl.querySelector('[data-om-source-list]');
        this.sourceStatusEl = rootEl.querySelector('[data-om-source-status]');

        this.toggleBtn.addEventListener('click', () => this._onToggle());
        this.formEl.addEventListener('submit', (e) => { e.preventDefault(); this._saveSettings(); });

        this._loadSettings();
        this._refreshStatus();
        this._loadTileSource();
    }

    // -- "Dashboard map source" -- switching node_map.js/topology_tab.js
    // between the public OSM server and tiles already downloaded here --

    _localTileUrl(collection, style) {
        // Relative, not `${window.location.origin}/...` -- Meshpoint is
        // typically reachable at several valid names (LAN IP, .local
        // mDNS, a VPN address...) and baking in whichever one happened
        // to be active when this button was clicked breaks the map the
        // moment someone visits through a different one: even with a
        // cert that covers both, the browser has never been asked to
        // trust it for that *other* origin specifically, so the tile
        // requests fail silently in the background (no click-through
        // prompt for subresource loads). A relative path always resolves
        // against whatever origin the dashboard is currently being
        // viewed from.
        return `/api/offline-map/tiles/${collection}/${style}/{z}/{x}/{y}.png`;
    }

    async _loadTileSource() {
        let currentUrl = '';
        let collections = [];
        try {
            const [cfgRes, colRes] = await Promise.all([
                fetch('/api/config', { credentials: 'same-origin' }),
                fetch('/api/offline-map/collections', { credentials: 'same-origin' }),
            ]);
            if (cfgRes.ok) currentUrl = ((await cfgRes.json()).dashboard || {}).map_tile_url || '';
            if (colRes.ok) collections = (await colRes.json()).collections || [];
        } catch (_e) { /* falls through to the empty-state render below */ }
        this._renderTileSource(currentUrl, collections);
    }

    _renderTileSource(currentUrl, collections) {
        const usingLocal = currentUrl.includes('/api/offline-map/tiles/');
        this.sourceCurrentEl.textContent = usingLocal
            ? `Currently using local tiles (${currentUrl}).`
            : `Currently using the public OSM server (${currentUrl || 'default'}).`;

        if (!collections.length) {
            this.sourceListEl.innerHTML = '<p class="cfg-card__hint">No downloaded tiles found yet -- download some first (Start the downloader above), then come back here.</p>';
            return;
        }
        this.sourceListEl.innerHTML = `
            <div class="om-card__actions">
                ${collections.map((c) => `
                    <button type="button" class="terminal-button" data-om-use-source
                        data-collection="${this._escape(c.collection)}" data-style="${this._escape(c.style)}">
                        Use ${this._escape(c.collection)} / ${this._escape(c.style)}
                    </button>
                `).join('')}
                <button type="button" class="terminal-button" data-om-use-osm>Use OSM (online)</button>
            </div>
        `;
        this.sourceListEl.querySelectorAll('[data-om-use-source]').forEach((btn) => {
            btn.addEventListener('click', () => this._useTileSource(
                this._localTileUrl(btn.dataset.collection, btn.dataset.style),
            ));
        });
        this.sourceListEl.querySelector('[data-om-use-osm]').addEventListener('click', () => this._useTileSource(''));
    }

    async _useTileSource(url) {
        // '' (from the "Use OSM" button) means "reset to the default" --
        // send the real OSM URL explicitly rather than a blank string,
        // since update_dashboard() rejects a blank map_tile_url outright.
        const body = { map_tile_url: url || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png' };
        this.sourceStatusEl.dataset.kind = 'pending';
        this.sourceStatusEl.textContent = 'Saving…';
        try {
            const r = await fetch('/api/config/dashboard', {
                method: 'PUT', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const respBody = await r.json().catch(() => ({}));
            if (!r.ok) {
                this.sourceStatusEl.dataset.kind = 'error';
                this.sourceStatusEl.textContent = respBody.detail || `Failed (HTTP ${r.status}).`;
                return;
            }
            this.sourceStatusEl.dataset.kind = 'success';
            this.sourceStatusEl.textContent = 'Saved -- reload any open map to see it.';
            this._loadTileSource();
        } catch (e) {
            this.sourceStatusEl.dataset.kind = 'error';
            this.sourceStatusEl.textContent = e.message || 'Failed.';
        }
    }

    _escape(s) {
        const div = document.createElement('div');
        div.textContent = String(s == null ? '' : s);
        return div.innerHTML;
    }

    show() {
        if (!this._pollTimer) this._pollTimer = setInterval(() => this._refreshStatus(), 3000);
    }

    hide() {
        if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
    }

    async _loadSettings() {
        try {
            const r = await fetch('/api/offline-map/settings', { credentials: 'same-origin' });
            if (!r.ok) return;
            const cfg = await r.json();
            for (const [key, value] of Object.entries(cfg)) {
                const el = this.formEl.elements.namedItem(key);
                if (!el) continue;
                if (el.type === 'checkbox') el.checked = !!value;
                else el.value = value;
            }
        } catch (_e) { /* settings form just stays empty */ }
    }

    async _saveSettings() {
        const fd = new FormData(this.formEl);
        const body = {
            port: Number(fd.get('port')),
            maps_directory: fd.get('maps_directory'),
            presets_directory: fd.get('presets_directory'),
            log_file: fd.get('log_file'),
            max_workers: Number(fd.get('max_workers')),
            rate_limit: Number(fd.get('rate_limit')),
            max_retries: Number(fd.get('max_retries')),
            quiet: fd.get('quiet') === 'on',
        };
        this._setSettingsStatus('pending', 'Saving…');
        try {
            const r = await fetch('/api/offline-map/settings', {
                method: 'PUT', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const respBody = await r.json().catch(() => ({}));
            if (!r.ok) { this._setSettingsStatus('error', respBody.detail || `Failed (HTTP ${r.status}).`); return; }
            this._setSettingsStatus('success', 'Saved. Applies on the next Start.');
        } catch (e) {
            this._setSettingsStatus('error', e.message || 'Failed.');
        }
    }

    async _refreshStatus() {
        try {
            const r = await fetch('/api/offline-map/status', { credentials: 'same-origin' });
            if (!r.ok) return;
            const s = await r.json();
            this._render(s);
        } catch (_e) { /* keep showing the last known state */ }
    }

    _render(s) {
        this.toggleBtn.disabled = !s.binary_present;
        this.toggleBtn.textContent = s.running ? 'Stop' : 'Start';
        this.toggleBtn.classList.toggle('terminal-button--danger', s.running);

        if (!s.binary_present) {
            this.statusEl.textContent = 'Not built yet -- run setup from Settings → Plugins first.';
            this.statusEl.dataset.kind = 'error';
        } else if (s.running) {
            const host = window.location.hostname;
            this.statusEl.textContent = `Running on port ${s.port}.`;
            this.statusEl.dataset.kind = 'success';
            this.openLink.href = `http://${host}:${s.port}`;
            this.openLink.hidden = false;
        } else {
            this.statusEl.textContent = s.last_error ? `Stopped -- ${s.last_error}` : 'Stopped.';
            this.statusEl.dataset.kind = s.last_error ? 'error' : '';
            this.openLink.hidden = true;
        }

        if (s.log_lines && s.log_lines.length) {
            this.logEl.hidden = false;
            this.logEl.textContent = s.log_lines.join('\n');
            this.logEl.scrollTop = this.logEl.scrollHeight;
        } else {
            this.logEl.hidden = true;
        }
    }

    async _onToggle() {
        const starting = this.toggleBtn.textContent === 'Start';
        if (starting) {
            const ok = await this._modal.confirm({
                label: 'Start the offline-map downloader?',
                command: 'Start offline-map downloader',
                description:
                    'This starts a separate program with its own web interface on ' +
                    'the port set below. That interface has no login of its own and ' +
                    '(today) listens on every network interface, not just this device ' +
                    '-- anyone on the same network can reach it while it\'s running. ' +
                    'Stop it when you\'re done downloading.',
            });
            if (!ok) return;
        }
        this.toggleBtn.disabled = true;
        try {
            const r = await fetch(`/api/offline-map/${starting ? 'start' : 'stop'}`, {
                method: 'POST', credentials: 'same-origin',
            });
            const body = await r.json().catch(() => ({}));
            if (!r.ok) {
                this.statusEl.textContent = body.detail || `Failed (HTTP ${r.status}).`;
                this.statusEl.dataset.kind = 'error';
            } else {
                this._render(body);
            }
        } catch (e) {
            this.statusEl.textContent = e.message || 'Failed.';
            this.statusEl.dataset.kind = 'error';
        } finally {
            this.toggleBtn.disabled = false;
        }
    }

    _setSettingsStatus(kind, message) {
        this.settingsStatusEl.dataset.kind = kind;
        this.settingsStatusEl.textContent = message;
    }
}
