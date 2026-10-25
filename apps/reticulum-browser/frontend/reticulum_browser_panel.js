/**
 * Reticulum Browser -- a full multi-tab NomadNet browser for Reticulum.
 *
 * Tabs, address bar, back/forward/reload, favourites, a raw/rendered
 * view toggle, and keyboard shortcuts -- built fresh against Meshpoint's
 * own patterns, the same ones the reticulum plugin's own Browse tab and
 * the reticulum-dashboard plugin's quick-browse modal already use
 * (_splitAddr/_followLink shape, the shared favourites list,
 * window.MicronParser rendering).
 *
 * Form-field submission, `/file/...` downloads, and identity fingerprinting
 * (ported from the reticulum plugin's own reticulum_nomad.js, plus a new
 * `/api/reticulum/nomad/fingerprint` route -- see nomad.py/nomad_routes.py
 * for the fingerprint mechanism itself) are all supported here too.
 *
 * Deliberately NOT included (see README.md "What's not here"):
 *   - a local NomadNet search engine + page cache (a background crawler
 *     + index, not a browser-tab feature -- its own service-seam plugin
 *     if built at all, not bundled into this one)
 *
 * Requires the reticulum plugin enabled (plugin.toml `requires =
 * "reticulum"`) -- window.MicronParser and the /api/reticulum/nomad/*
 * routes both come from there.
 */

// Same localStorage key + {hash, name} shape the reticulum plugin's own
// Browse tab / Peers drawer / and the reticulum-dashboard plugin's
// quick-browse modal all use -- duplicated here on purpose, same small-
// duplication convention all of those already follow, so a node
// favourited anywhere shows favourited everywhere.
const _RB_FAV_KEY = 'meshpoint.rtNomadFavourites';

function _rbFavourites() {
    try {
        const v = JSON.parse(localStorage.getItem(_RB_FAV_KEY) || '[]');
        return Array.isArray(v) ? v.filter((f) => f && f.hash) : [];
    } catch (_) { return []; }
}

function _rbIsFavourite(hash) {
    return _rbFavourites().some((f) => f.hash === hash);
}

// Lenient Micron alignment (see MicronParser's lenientAlign), per browser.
const _RB_LENIENT_KEY = 'meshpoint.rbLenientMicron';
function _rbLenient() { try { return localStorage.getItem(_RB_LENIENT_KEY) === '1'; } catch (_) { return false; } }
function _rbSetLenient(on) { try { localStorage.setItem(_RB_LENIENT_KEY, on ? '1' : '0'); } catch (_) {} }

// Auto-identify on every node you visit, per browser -- off by default
// (identifying reveals your LXMF address to that node). Was a one-shot
// "ID" button you had to remember to click each time a node needed it;
// now a persistent toggle, same idiom as the lenient-centring one above.
const _RB_AUTO_ID_KEY = 'meshpoint.rbAutoIdentify';
function _rbAutoIdentify() { try { return localStorage.getItem(_RB_AUTO_ID_KEY) === '1'; } catch (_) { return false; } }
function _rbSetAutoIdentify(on) { try { localStorage.setItem(_RB_AUTO_ID_KEY, on ? '1' : '0'); } catch (_) {} }

function _rbToggleFavourite(hash, name) {
    const list = _rbFavourites();
    const idx = list.findIndex((f) => f.hash === hash);
    if (idx >= 0) list.splice(idx, 1);
    else list.push({ hash, name: name || `${hash.slice(0, 12)}…` });
    try { localStorage.setItem(_RB_FAV_KEY, JSON.stringify(list)); } catch (_) {}
    return idx < 0; // true if it's now favourited
}

let _rbTabSeq = 0;

class ReticulumBrowserPanel {
    constructor() {
        this._root = null;
        this._tabs = [];
        this._activeTabId = null;
        this._nodes = [];
        this._nodeSearchQuery = '';
        this._loading = false;
        this._fetchController = null;
        this._onKeyDown = this._onKeyDown.bind(this);
    }

    mount(rootEl) {
        this._root = rootEl;
        rootEl.innerHTML = `
            <header class="lw-panel__head">
                <h2 class="lw-panel__title">Reticulum Browser</h2>
                <div class="lw-panel__actions">
                    <span class="lw-panel__limit">A multi-tab NomadNet browser</span>
                </div>
            </header>

            <div class="rb-bar">
                <div class="rb-bar-row rb-bar-row--find">
                    <input type="search" class="cfg-field__input rb-search" data-rb-search
                           placeholder="Filter nodes…" autocomplete="off" spellcheck="false"
                           aria-label="Filter NomadNet nodes">
                    <select class="cfg-field__input rb-nodes" data-rb-nodes>
                        <option value="">— pick a node —</option>
                    </select>
                    <select class="cfg-field__input rb-favs" data-rb-favs aria-label="Jump to a favourite node">
                        <option value="">★ Favourites</option>
                    </select>
                    <button class="terminal-button rt-nomad__fav" type="button" data-rb-fav
                            title="Favourite this node" disabled>&#9734;</button>
                </div>
                <div class="rb-bar-row rb-bar-row--nav">
                    <button class="terminal-button" type="button" data-rb-back title="Back" disabled>&larr;</button>
                    <button class="terminal-button" type="button" data-rb-forward title="Forward" disabled>&rarr;</button>
                    <button class="terminal-button" type="button" data-rb-reload title="Reload" disabled>&#x21bb;</button>
                    <input type="text" class="cfg-field__input rb-addr" data-rb-addr
                           placeholder="&lt;hash&gt;:/page/x.mu  —  or  :/page/info.mu on the current node"
                           autocomplete="off" spellcheck="false" aria-label="Node address">
                    <button class="terminal-button" type="button" data-rb-go>Go</button>
                    <button class="terminal-button rb-meshpoint-badge" type="button" data-rb-meshpoint
                            title="This node also runs Meshpoint — click to view its info.mu" hidden>&#9432;</button>
                    <button class="terminal-button" type="button" data-rb-raw
                            title="Toggle raw/rendered view">{ }</button>
                    <button class="terminal-button" type="button" data-rb-lenient
                            title="Lenient centring (MeshChatX-compatible): a \`c at the start of a line always centres that line. Off = standard NomadNet Micron, where \`c toggles">\`c</button>
                    <button class="terminal-button" type="button" data-rb-auto-id
                            title="Auto-identify: automatically identify yourself to every node you visit — your LXMF address will be included in its form submissions. Off by default.">ID</button>
                </div>
            </div>

            <div class="rb-tabstrip" data-rb-tabstrip></div>
            <p class="cfg-status" data-rb-status aria-live="polite"></p>

            <div class="rb-page" data-rb-page>
                <p class="lw-empty">Pick a node above, or type an address
                (<code>&lt;hash&gt;:/page/x.mu</code>). Once you're on a node, the bar
                holds its full address — edit the path and press Go, or open a new tab
                (<code>Ctrl/Cmd+T</code>) to browse a second node alongside this one.</p>
            </div>
        `;

        this._searchEl = this._q('[data-rb-search]');
        this._nodesEl = this._q('[data-rb-nodes]');
        this._favsEl = this._q('[data-rb-favs]');
        this._favBtn = this._q('[data-rb-fav]');
        this._addrEl = this._q('[data-rb-addr]');
        this._backBtn = this._q('[data-rb-back]');
        this._fwdBtn = this._q('[data-rb-forward]');
        this._reloadBtn = this._q('[data-rb-reload]');
        this._rawBtn = this._q('[data-rb-raw]');
        this._lenientBtn = this._q('[data-rb-lenient]');
        this._lenientBtn.classList.toggle('is-active', _rbLenient());
        this._autoIdBtn = this._q('[data-rb-auto-id]');
        // Read from storage once, at mount -- the *in-memory* flag is what
        // every later check/toggle actually uses, not a fresh storage
        // read each time. A storage write that silently fails (private
        // browsing, a blocked/third-party-cookie-style restriction, quota)
        // would otherwise make every click re-derive "on" from whatever
        // was last actually persisted and get stuck unable to turn off.
        this._autoIdOn = _rbAutoIdentify();
        this._autoIdBtn.classList.toggle('is-active', this._autoIdOn);
        this._meshpointBtn = this._q('[data-rb-meshpoint]');
        this._tabstripEl = this._q('[data-rb-tabstrip]');
        this._statusEl = this._q('[data-rb-status]');
        this._pageEl = this._q('[data-rb-page]');

        this._searchEl.addEventListener('input', (e) => {
            this._nodeSearchQuery = e.target.value.trim().toLowerCase();
            this._renderNodeOptions();
        });
        this._nodesEl.addEventListener('change', () => {
            const hash = this._nodesEl.value;
            if (hash) this._go(hash, '/page/index.mu');
        });
        this._favsEl.addEventListener('change', () => {
            const chosen = this._favsEl.value;
            this._favsEl.value = '';
            if (chosen) this._go(chosen, '/page/index.mu');
        });
        this._favBtn.addEventListener('click', () => {
            const tab = this._activeTab();
            if (!tab || !tab.hash) return;
            const nowOn = _rbToggleFavourite(tab.hash, tab.title);
            this._syncFavBtn(nowOn);
            this._renderFavsSelect();
        });
        this._q('[data-rb-go]').addEventListener('click', () => this._goFromAddr());
        this._meshpointBtn.addEventListener('click', () => {
            const tab = this._activeTab();
            if (tab && tab.hash) this._go(tab.hash, '/page/info.mu');
        });
        this._addrEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); this._goFromAddr(); }
        });
        this._backBtn.addEventListener('click', () => this._historyGo(-1));
        this._fwdBtn.addEventListener('click', () => this._historyGo(1));
        this._reloadBtn.addEventListener('click', () => {
            if (this._loading && this._fetchController) this._fetchController.abort();
            else this._reload();
        });
        this._rawBtn.addEventListener('click', () => this._toggleRaw());
        this._lenientBtn.addEventListener('click', () => {
            const on = !_rbLenient();
            _rbSetLenient(on);
            this._lenientBtn.classList.toggle('is-active', on);
            const tab = this._activeTab();
            if (tab) this._renderPage(tab);
        });
        this._autoIdBtn.addEventListener('click', () => this._toggleAutoIdentify());
        this._pageEl.addEventListener('click', (e) => {
            const a = e.target.closest('a[data-nomad-url]');
            if (!a) return;
            e.preventDefault();
            this._followLink(a);
        });
        this._tabstripEl.addEventListener('click', (e) => {
            const closeBtn = e.target.closest('[data-rb-tab-close]');
            if (closeBtn) { this._closeTab(closeBtn.dataset.rbTabClose); return; }
            const newBtn = e.target.closest('[data-rb-tab-new]');
            if (newBtn) { this._newTab(); return; }
            const tabEl = e.target.closest('[data-rb-tab-id]');
            if (tabEl) this._switchTab(tabEl.dataset.rbTabId);
        });

        this._renderFavsSelect();
        this._newTab();
    }

    show() {
        this._loadNodes();
        document.addEventListener('keydown', this._onKeyDown);
    }

    hide() {
        document.removeEventListener('keydown', this._onKeyDown);
    }

    // --- Tabs ---------------------------------------------------------------

    _activeTab() {
        return this._tabs.find((t) => t.id === this._activeTabId) || null;
    }

    _newTab() {
        const tab = {
            id: `rb${++_rbTabSeq}`, hash: null, path: null, title: 'New tab',
            content: null, rawMode: false, history: [], historyIdx: -1,
            fingerprinted: false, meshpointDetected: null,
        };
        this._tabs.push(tab);
        this._activeTabId = tab.id;
        this._renderTabStrip();
        this._renderActiveTab();
        this._addrEl.focus();
    }

    _closeTab(tabId) {
        const idx = this._tabs.findIndex((t) => t.id === tabId);
        if (idx === -1) return;
        this._tabs.splice(idx, 1);
        if (!this._tabs.length) {
            // Never zero tabs -- a fresh blank one takes its place.
            this._newTab();
            return;
        }
        if (this._activeTabId === tabId) {
            const next = this._tabs[Math.min(idx, this._tabs.length - 1)];
            this._activeTabId = next.id;
        }
        this._renderTabStrip();
        this._renderActiveTab();
    }

    _switchTab(tabId) {
        if (!this._tabs.some((t) => t.id === tabId)) return;
        this._activeTabId = tabId;
        this._renderTabStrip();
        this._renderActiveTab();
    }

    _renderTabStrip() {
        const html = this._tabs.map((t) => `
            <div class="rb-tab${t.id === this._activeTabId ? ' rb-tab--active' : ''}"
                 data-rb-tab-id="${t.id}" title="${this._esc(t.hash || 'New tab')}">
                <span class="rb-tab__title">${this._esc(t.title)}</span>
                <button type="button" class="rb-tab__close" data-rb-tab-close="${t.id}"
                        title="Close tab (Ctrl/Cmd+W)" aria-label="Close tab">&times;</button>
            </div>
        `).join('');
        this._tabstripEl.innerHTML = html
            + `<button type="button" class="rb-tab__new" data-rb-tab-new title="New tab (Ctrl/Cmd+T)">+</button>`;
    }

    /** Re-renders the page body + syncs the toolbar (address/back/forward/
     * reload/favourite/raw) from the active tab's own cached state --
     * switching tabs never re-fetches, it just re-displays what that tab
     * already loaded. */
    _renderActiveTab() {
        const tab = this._activeTab();
        if (!tab) return;
        this._addrEl.value = tab.hash ? `${tab.hash}:${tab.path}` : '';
        this._backBtn.disabled = tab.historyIdx <= 0;
        this._fwdBtn.disabled = tab.historyIdx >= tab.history.length - 1;
        // While loading, the reload button is showing Stop (always
        // clickable) -- _setLoading() owns .disabled in that state, don't
        // stomp on it with the Reload-specific history check here.
        if (!this._loading) this._reloadBtn.disabled = tab.historyIdx < 0;
        this._rawBtn.classList.toggle('is-active', tab.rawMode);
        this._syncFavBtn(tab.hash ? _rbIsFavourite(tab.hash) : false);
        this._syncMeshpointBtn(tab);
        this._renderPage(tab);
    }

    _renderPage(tab) {
        if (tab.content == null) {
            this._pageEl.innerHTML = `<p class="lw-empty">Pick a node above, or type an address
                (<code>&lt;hash&gt;:/page/x.mu</code>).</p>`;
            return;
        }
        if (tab.rawMode) {
            this._pageEl.textContent = tab.content;
            return;
        }
        this._pageEl.textContent = '';
        if (window.MicronParser) {
            this._pageEl.appendChild(new window.MicronParser(true, { lenientAlign: _rbLenient() }).parseToHtml(tab.content));
        } else {
            this._pageEl.textContent = tab.content;
        }
    }

    _toggleRaw() {
        const tab = this._activeTab();
        if (!tab) return;
        tab.rawMode = !tab.rawMode;
        this._rawBtn.classList.toggle('is-active', tab.rawMode);
        this._renderPage(tab);
    }

    // --- Navigation (per active tab) -----------------------------------------

    _goFromAddr() {
        const tab = this._activeTab();
        const raw = (this._addrEl.value || '').trim();
        if (!tab || !raw) return;
        const { hash, path } = this._splitAddr(raw, tab.hash);
        if (!hash) return;
        this._go(hash, path);
    }

    _go(hash, path, fieldData) {
        const tab = this._activeTab();
        if (tab) this._fetch(tab, hash, path, true, fieldData);
    }

    /** Returns the underlying _fetch() promise (callers that just want
     * "reload, fire-and-forget" -- the button, link-following -- can
     * ignore it same as always; _toggleAutoIdentify() awaits it so it can
     * show a final status once the reconnect actually finishes). */
    _reload() {
        const tab = this._activeTab();
        if (!tab) return undefined;
        const e = tab.history[tab.historyIdx];
        return e ? this._fetch(tab, e.hash, e.path, false, e.field_data) : undefined;
    }

    _historyGo(delta) {
        const tab = this._activeTab();
        if (!tab) return;
        const next = tab.historyIdx + delta;
        if (next < 0 || next >= tab.history.length) return;
        tab.historyIdx = next;
        const e = tab.history[next];
        this._fetch(tab, e.hash, e.path, false, e.field_data);
    }

    async _fetch(tab, hash, path, pushHistory, fieldData) {
        // Supersede, don't race: a second navigation (new link clicked,
        // Go pressed again) while one's still in flight aborts the first
        // instead of letting whichever response lands last silently win.
        if (this._fetchController) this._fetchController.abort();
        const controller = new AbortController();
        this._fetchController = controller;
        this._setLoading(true);

        this._pageEl.innerHTML = '<p class="lw-empty">Loading…</p>';
        const isNewNode = tab.hash !== hash;
        // Identify BEFORE requesting the page, not after, when auto-
        // identify is on and this is a new node -- some pages render
        // their own "are you identified?" status server-side (this is
        // what sent us here in the first place), and that status was
        // otherwise always stale on the very first load: identifying only
        // *after* the page already rendered meant it never reflected
        // reality until a second, manual reload.
        let justIdentified = false;
        try {
            if (isNewNode && this._autoIdOn) {
                justIdentified = await this._identifyHash(hash, controller.signal);
            }
            const r = await fetch('/api/reticulum/nomad/page', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ destination_hash: hash, path, field_data: fieldData || null }),
                signal: controller.signal,
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok || !data.ok) {
                this._pageEl.textContent = data.error || `Failed (HTTP ${r.status})`;
                return;
            }
            tab.hash = hash;
            tab.path = path;
            tab.content = data.content || '';
            if (isNewNode) {
                tab.fingerprinted = justIdentified;
                tab.meshpointDetected = null;
                this._checkMeshpointInfo(tab, hash);
            }
            const known = this._nodes.find((n) => n.destination_hash === hash);
            const fav = _rbFavourites().find((f) => f.hash === hash);
            tab.title = fav?.name || known?.display_name || `${hash.slice(0, 8)}…`;
            if (pushHistory) {
                tab.history = tab.history.slice(0, tab.historyIdx + 1);
                tab.history.push({ hash, path, field_data: fieldData || null });
                tab.historyIdx = tab.history.length - 1;
            }
            if (tab.id === this._activeTabId) this._renderActiveTab();
            this._renderTabStrip();
        } catch (e) {
            if (e.name === 'AbortError') {
                this._pageEl.textContent = 'Stopped.';
            } else {
                this._pageEl.textContent = `Network error: ${e.message}`;
            }
        } finally {
            // Only this call's own controller gets to clear the loading
            // state -- if a newer _fetch() has already superseded this
            // one (see the abort() above), that newer call owns it now.
            if (this._fetchController === controller) {
                this._fetchController = null;
                this._setLoading(false);
            }
        }
    }

    /** Toggles the reload button into a Stop button (same spot, same
     * idiom every browser uses) while a page is loading. Stop is always
     * clickable regardless of history state -- unlike Reload, which stays
     * disabled with no history to reload from. */
    _setLoading(on) {
        this._loading = on;
        if (!this._reloadBtn) return;
        this._reloadBtn.innerHTML = on ? '&times;' : '&#x21bb;';
        this._reloadBtn.title = on ? 'Stop' : 'Reload';
        if (on) {
            this._reloadBtn.disabled = false;
        } else {
            const tab = this._activeTab();
            this._reloadBtn.disabled = !tab || tab.historyIdx < 0;
        }
    }

    /** Ported from the reticulum plugin's own reticulum_nomad.js: gathers
     * form fields a Micron link asks to submit (`data-nomad-fields`, set
     * by window.MicronParser), routes `/file/...` links to a download
     * instead of a page render, and opens external http(s) links in a
     * normal browser tab. */
    _followLink(a) {
        const tab = this._activeTab();
        if (!tab) return;
        const rawUrl = a.dataset.nomadUrl || '';
        let addrPart = rawUrl;
        const varData = {};
        const btick = rawUrl.indexOf('`');
        if (btick !== -1) {
            addrPart = rawUrl.slice(0, btick);
            for (const pair of rawUrl.slice(btick + 1).split('|')) {
                const eq = pair.indexOf('=');
                if (eq !== -1) varData[`var_${pair.slice(0, eq)}`] = pair.slice(eq + 1);
            }
        }
        addrPart = addrPart.replace(/^nomadnetwork:\/\//, '');
        if (/^https?:\/\//i.test(addrPart)) {
            window.open(addrPart, '_blank', 'noopener');
            return;
        }

        const { hash, path } = this._splitAddr(addrPart, tab.hash);
        if (!hash) return;

        if (path.startsWith('/file/')) {
            this._downloadFile(hash, path);
            return;
        }

        const fieldData = { ...varData };
        const spec = a.dataset.nomadFields;
        if (spec) {
            const wantAll = spec === '*';
            const wanted = wantAll ? null : new Set(spec.split('|'));
            this._pageEl.querySelectorAll('input[name], select[name], textarea[name]').forEach((inp) => {
                if (!wantAll && !wanted.has(inp.name)) return;
                if ((inp.type === 'checkbox' || inp.type === 'radio') && !inp.checked) return;
                fieldData[`field_${inp.name}`] = inp.value;
            });
        }
        this._go(hash, path, Object.keys(fieldData).length ? fieldData : null);
    }

    async _downloadFile(hash, path) {
        this._status('pending', `Downloading ${path.split('/').pop()}…`);
        try {
            const r = await fetch('/api/reticulum/nomad/file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ destination_hash: hash, path }),
            });
            if (!r.ok) {
                const err = await r.json().catch(() => ({}));
                this._status('error', err.detail || `Download failed (HTTP ${r.status})`);
                return;
            }
            const blob = await r.blob();
            const cd = r.headers.get('Content-Disposition') || '';
            const m = cd.match(/filename="?([^"]+)"?/);
            const name = m ? m[1] : (path.split('/').pop() || 'downloaded_file');
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = name;
            document.body.appendChild(a);
            a.click();
            a.remove();
            URL.revokeObjectURL(url);
            this._status('success', `Downloaded ${name}`);
        } catch (e) {
            this._status('error', `Download error: ${e.message}`);
        }
    }

    /** The raw identify POST against a bare hash (no tab bookkeeping) --
     * used both by _identify() below and by _fetch()'s auto-identify-
     * before-the-page-request path, which doesn't have an up-to-date
     * `tab.hash` to key off yet (that's only set once the page fetch
     * itself succeeds). Returns whether it actually succeeded. `signal`
     * is optional -- only _fetch()'s own call passes one, so Stop can
     * cancel the identify-before-fetch leg too, not just the page fetch. */
    async _identifyHash(hash, signal) {
        try {
            const r = await fetch('/api/reticulum/nomad/fingerprint', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ destination_hash: hash }),
                signal,
            });
            const data = await r.json().catch(() => ({}));
            return !!(r.ok && data.ok);
        } catch (_e) {
            return false;
        }
    }

    /** Identify to *tab*'s current node -- the manual "turn the toggle on
     * right now" path. Idempotent per node-visit (tab.fingerprinted
     * guards it, reset to false by _fetch() whenever the tab navigates to
     * a different hash). announce surfaces a status line; the auto path
     * (_fetch()'s isNewNode branch) calls _identifyHash() directly
     * instead, before tab.hash is even set, and silently. */
    async _identify(tab, { announce = false } = {}) {
        if (!tab || !tab.hash || tab.fingerprinted) return;
        if (announce) this._status('pending', 'Identifying yourself to this node…');
        const ok = await this._identifyHash(tab.hash);
        if (!ok) {
            if (announce) this._status('error', 'Failed to identify.');
            return;
        }
        tab.fingerprinted = true;
        if (announce) {
            this._status('success', 'Identified — your LXMF address will be included in this node’s next form submission');
        }
    }

    /** Tear down the backend's cached Link to *hash* (backend/nomad.py's
     * drop_link()) -- best-effort, errors are swallowed since this is
     * always followed by a reload that will surface any real connection
     * problem on its own. */
    async _disconnectHash(hash) {
        try {
            await fetch('/api/reticulum/nomad/disconnect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ destination_hash: hash }),
            });
        } catch (_e) { /* best-effort */ }
    }

    /** The auto-identify toggle's click handler. Identification can't be
     * undone over an existing Link -- RNS has no reverse of
     * link.identify() -- so flipping the toggle either direction drops
     * the cached Link for whatever node is currently open and reconnects,
     * rather than just changing *future* navigations while the current,
     * already-identified (or not) Link keeps answering exactly as before.
     * That reconnect costs a real path-discovery + link-establishment
     * round trip -- can take a few seconds on a multi-hop LoRa path, same
     * as any other "first load of a node" (see _fetch()'s own comments). */
    async _toggleAutoIdentify() {
        this._autoIdOn = !this._autoIdOn;
        _rbSetAutoIdentify(this._autoIdOn);
        this._autoIdBtn.classList.toggle('is-active', this._autoIdOn);

        const tab = this._activeTab();
        if (!tab || !tab.hash) return;

        this._status('pending', this._autoIdOn ? 'Identifying…' : 'Reconnecting…');
        await this._disconnectHash(tab.hash);
        tab.fingerprinted = false;
        if (this._autoIdOn) {
            await this._identify(tab, { announce: false });
        }
        if (tab.id === this._activeTabId) {
            await this._reload();
            if (tab.id === this._activeTabId) {
                this._status('success', this._autoIdOn
                    ? 'Identified — your LXMF address will be included in this node’s next form submission'
                    : 'Reconnected with a fresh, unidentified link.');
            }
        }
    }

    /** Silent background probe, fired once per node-change (see _fetch()'s
     * isNewNode branch) -- fetches /page/info.mu over the SAME already-
     * established Link the page load just used (cheap, no extra visible
     * round trip) and checks for the literal "Meshpoint node" text
     * nomad_node.py's _serve_info() always emits (`` `ca Meshpoint
     * node`a `` in its Micron source -- unconditional and never
     * overridable by an operator's own pages, so this is a reliable
     * signature, not just "any node that happens to have an info.mu").
     * Best-effort and silent on purpose: a node without Meshpoint simply
     * won't have this page, which is the normal, expected case, not an
     * error worth surfacing. */
    async _checkMeshpointInfo(tab, hash) {
        try {
            const r = await fetch('/api/reticulum/nomad/page', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ destination_hash: hash, path: '/page/info.mu', field_data: null }),
            });
            const data = await r.json().catch(() => ({}));
            const detected = !!(r.ok && data.ok && (data.content || '').includes('Meshpoint node'));
            if (tab.hash !== hash) return; // navigated elsewhere in this tab while the check was in flight
            tab.meshpointDetected = detected;
        } catch (_) {
            if (tab.hash === hash) tab.meshpointDetected = false;
        }
        if (tab.id === this._activeTabId) this._syncMeshpointBtn(tab);
    }

    _syncMeshpointBtn(tab) {
        if (!this._meshpointBtn) return;
        const detected = !!(tab && tab.meshpointDetected);
        this._meshpointBtn.hidden = !detected;
    }


    _status(kind, msg) {
        if (!this._statusEl) return;
        this._statusEl.dataset.kind = kind;
        this._statusEl.textContent = msg;
    }

    _splitAddr(raw, currentHash) {
        raw = raw.replace(/^nomadnetwork:\/\//, '');
        if (raw.startsWith(':')) return { hash: currentHash || '', path: raw.slice(1) || '/page/index.mu' };
        if (raw.startsWith('/')) return { hash: currentHash || '', path: raw };
        const idx = raw.indexOf(':');
        if (idx === -1) return { hash: raw, path: '/page/index.mu' };
        return { hash: raw.slice(0, idx), path: raw.slice(idx + 1) || '/page/index.mu' };
    }

    // --- Node picker / favourites --------------------------------------------

    async _loadNodes() {
        try {
            const r = await fetch('/api/reticulum/nomad/nodes', { credentials: 'same-origin' });
            if (!r.ok) return;
            this._nodes = await r.json();
            this._renderNodeOptions();
        } catch (_) {}
    }

    _renderNodeOptions() {
        const q = this._nodeSearchQuery;
        const filtered = q
            ? this._nodes.filter((n) =>
                (n.display_name || '').toLowerCase().includes(q)
                || n.destination_hash.toLowerCase().includes(q))
            : this._nodes;
        this._nodesEl.innerHTML = '<option value="">— pick a node —</option>'
            + filtered.map((n) => {
                const name = (n.display_name || n.destination_hash.slice(0, 12)).slice(0, 46);
                return `<option value="${this._esc(n.destination_hash)}" title="${this._esc(n.destination_hash)}">${this._esc(name)}</option>`;
            }).join('');
    }

    _renderFavsSelect() {
        const favs = _rbFavourites();
        this._favsEl.innerHTML = '<option value="">★ Favourites</option>'
            + favs.map((f) => `<option value="${this._esc(f.hash)}">${this._esc(f.name)}</option>`).join('');
        this._favsEl.disabled = !favs.length;
    }

    _syncFavBtn(isFav) {
        const tab = this._activeTab();
        this._favBtn.disabled = !tab || !tab.hash;
        this._favBtn.classList.toggle('rt-nomad__fav--on', !!isFav);
        this._favBtn.innerHTML = isFav ? '&#9733;' : '&#9734;';
        this._favBtn.title = isFav ? 'Remove from favourites' : 'Favourite this node';
    }

    // --- Keyboard shortcuts ---------------------------------------------------
    // Only active while this page is shown (wired in show()/hide()). Ctrl on
    // Windows/Linux, Cmd on macOS -- e.metaKey covers Cmd, e.ctrlKey covers Ctrl.

    _onKeyDown(e) {
        const mod = e.metaKey || e.ctrlKey;
        if (mod && e.key.toLowerCase() === 't') {
            e.preventDefault();
            this._newTab();
        } else if (mod && e.key.toLowerCase() === 'w') {
            e.preventDefault();
            if (this._activeTabId) this._closeTab(this._activeTabId);
        } else if (mod && e.key.toLowerCase() === 'r') {
            e.preventDefault();
            this._reload();
        } else if (e.altKey && e.key === 'ArrowLeft') {
            e.preventDefault();
            this._historyGo(-1);
        } else if (e.altKey && e.key === 'ArrowRight') {
            e.preventDefault();
            this._historyGo(1);
        }
    }

    _esc(s) {
        return String(s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }

    /** Called from outside this class -- the reticulum-dashboard plugin's
     * own Browse actions feature-detect window.reticulumBrowserPanel
     * (set below, same pattern app.js's own window.dabPanel uses for
     * DAB+'s mini-player hook) and hand a hash off here instead of
     * opening their own smaller quick-view modal, when this plugin is
     * installed -- always a fresh tab, so it never clobbers whatever the
     * user already has open here. */
    openHash(hash, label) {
        if (!hash) return;
        this._newTab();
        const tab = this._activeTab();
        if (tab && label) {
            tab.title = label; // provisional -- _fetch() below overwrites
            this._renderTabStrip(); // it with the real name once resolved
        }
        this._go(hash, '/page/index.mu');
    }

    _q(sel) { return this._root ? this._root.querySelector(sel) : null; }
}

window.registerSidebarPage({
    route: 'reticulum-browser',
    make: () => {
        const panel = new ReticulumBrowserPanel();
        window.reticulumBrowserPanel = panel;
        return panel;
    },
});
