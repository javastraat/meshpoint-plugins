/**
 * Reticulum Scraper -- a "Scrape" tab hooked into the Reticulum page
 * (window.registerPageHook, host: "reticulum" -- see plugin.toml's [hook]
 * table and frontend/sidebar/page_hook_registry.js for the seam itself).
 *
 * Talks to the *reticulum* plugin's own backend, not one of its own --
 * this plugin has no backend beyond a no-op register() (see
 * backend/__init__.py), same reason reticulum-call has none: the actual
 * crawl needs the reticulum plugin's own node_pages_dir config and its
 * live LxmfService.reload_node_pages() (plugins/apps/reticulum/backend/
 * scraper.py + scrape_routes.py):
 *
 *   POST   /api/reticulum/scrape/start                -- begin a crawl
 *   POST   /api/reticulum/scrape/stop                  -- stop it (keeps
 *                                                          pages already
 *                                                          written)
 *   GET    /api/reticulum/scrape/status                -- live progress
 *   GET    /api/reticulum/scrape/collections            -- what's already
 *                                                          scraped
 *   DELETE /api/reticulum/scrape/collections/{name}    -- remove one
 */
class ReticulumScraperHookPanel {
    constructor() {
        this._root = null;
        this._pollTimer = null;
        this._wasRunning = false;
    }

    mount(rootEl) {
        this._root = rootEl;
        rootEl.innerHTML = `
            <div class="rtscrape">
                <p class="cfg-field__hint">
                    Mirrors a website into Micron pages this node can serve over
                    Reticulum with zero internet -- e.g. an offline copy of a
                    first-aid wiki. The source stays reachable only while you're
                    scraping it; once saved, the pages are served locally from
                    this device.
                </p>

                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Scraped collections</h3>
                    </header>
                    <div data-rtscrape-collections>Loading…</div>
                </article>

                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Scrape a new site</h3>
                    </header>
                    <form class="cfg-form" data-rtscrape-form>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Source URL</span>
                            <input class="cfg-field__input" type="url" name="source_url" required
                                   placeholder="https://www.eerstehulpwiki.nl/wiki/index.php?title=Eerste_Hulp_Wiki">
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Name</span>
                            <input class="cfg-field__input" type="text" name="name" required
                                   pattern="[a-z0-9][a-z0-9-]{0,31}" maxlength="32"
                                   placeholder="hulp">
                            <p class="cfg-field__hint">
                                Lowercase letters/digits/- only. Pages land at
                                node_pages_dir/&lt;name&gt;/, browsable as
                                <code>:/page/&lt;name&gt;/index.mu</code>.
                            </p>
                        </label>
                        <div class="cfg-row">
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Max pages</span>
                                <input class="cfg-field__input" type="number" name="max_pages"
                                       min="1" max="200" placeholder="40">
                            </label>
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Max link depth</span>
                                <input class="cfg-field__input" type="number" name="max_depth"
                                       min="0" max="6" placeholder="3">
                            </label>
                        </div>
                        <p class="cfg-field__hint">
                            Stays on the source's own host, skips obvious non-article
                            links (search, edit, talk pages). No images are downloaded
                            yet -- a placeholder line replaces each one.
                        </p>
                        <div class="cfg-card__actions">
                            <button class="terminal-button terminal-button--primary" type="submit"
                                    data-rtscrape-start>Start scraping</button>
                            <button class="terminal-button terminal-button--danger" type="button"
                                    data-rtscrape-stop hidden>Stop</button>
                        </div>
                    </form>
                    <p class="auth-status" data-rtscrape-status aria-live="polite"></p>
                    <pre class="rtscrape-log" data-rtscrape-log hidden></pre>
                </article>
            </div>
        `;

        this._collectionsEl = this._q('[data-rtscrape-collections]');
        this._formEl = this._q('[data-rtscrape-form]');
        this._startBtn = this._q('[data-rtscrape-start]');
        this._stopBtn = this._q('[data-rtscrape-stop]');
        this._statusEl = this._q('[data-rtscrape-status]');
        this._logEl = this._q('[data-rtscrape-log]');

        this._formEl.addEventListener('submit', (e) => { e.preventDefault(); this._start(); });
        this._stopBtn.addEventListener('click', () => this._stop());

        this._loadCollections();
        this._refreshStatus();
    }

    show() {
        if (!this._pollTimer) this._pollTimer = setInterval(() => this._refreshStatus(), 2000);
    }

    hide() {
        if (this._pollTimer) { clearInterval(this._pollTimer); this._pollTimer = null; }
    }

    _q(sel) {
        return this._root ? this._root.querySelector(sel) : null;
    }

    _esc(value) {
        const div = document.createElement('div');
        div.textContent = String(value ?? '');
        return div.innerHTML;
    }

    async _loadCollections() {
        try {
            const r = await fetch('/api/reticulum/scrape/collections', { credentials: 'same-origin' });
            if (!r.ok) { this._collectionsEl.textContent = 'Reticulum companion is disabled.'; return; }
            const body = await r.json();
            this._renderCollections(body.collections || []);
        } catch (_e) {
            this._collectionsEl.textContent = 'Could not load collections.';
        }
    }

    _renderCollections(collections) {
        if (!collections.length) {
            this._collectionsEl.innerHTML = '<p class="cfg-card__hint">Nothing scraped yet.</p>';
            return;
        }
        this._collectionsEl.innerHTML = collections.map((c) => `
            <div class="rtscrape__row" data-rtscrape-row="${this._esc(c.name)}">
                <div>
                    <strong>${this._esc(c.name)}</strong>
                    <span class="cfg-card__hint">
                        ${c.page_count} page${c.page_count === 1 ? '' : 's'} ·
                        from ${this._esc(c.source_url)} · scraped ${this._esc(c.scraped_at)}
                    </span>
                </div>
                <button class="terminal-button terminal-button--danger" type="button"
                        data-rtscrape-delete="${this._esc(c.name)}">Delete</button>
            </div>
        `).join('');
        this._collectionsEl.querySelectorAll('[data-rtscrape-delete]').forEach((btn) => {
            btn.addEventListener('click', () => this._deleteCollection(btn.dataset.rtscrapeDelete));
        });
    }

    async _deleteCollection(name) {
        if (!window.confirm(`Delete the "${name}" collection? This removes its pages from disk.`)) return;
        try {
            const r = await fetch(`/api/reticulum/scrape/collections/${encodeURIComponent(name)}`, {
                method: 'DELETE', credentials: 'same-origin',
            });
            if (!r.ok) {
                const body = await r.json().catch(() => ({}));
                this._setStatus('error', body.detail || `Delete failed (HTTP ${r.status}).`);
                return;
            }
            this._loadCollections();
        } catch (e) {
            this._setStatus('error', e.message || 'Delete failed.');
        }
    }

    async _start() {
        const fd = new FormData(this._formEl);
        const body = {
            source_url: fd.get('source_url'),
            name: fd.get('name'),
        };
        const maxPages = fd.get('max_pages');
        const maxDepth = fd.get('max_depth');
        if (maxPages) body.max_pages = Number(maxPages);
        if (maxDepth !== '' && maxDepth !== null) body.max_depth = Number(maxDepth);

        this._setStatus('pending', 'Starting…');
        try {
            const r = await fetch('/api/reticulum/scrape/start', {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const respBody = await r.json().catch(() => ({}));
            if (!r.ok) {
                this._setStatus('error', respBody.detail || `Failed to start (HTTP ${r.status}).`);
                return;
            }
            this._render(respBody);
        } catch (e) {
            this._setStatus('error', e.message || 'Failed to start.');
        }
    }

    async _stop() {
        this._stopBtn.disabled = true;
        try {
            const r = await fetch('/api/reticulum/scrape/stop', {
                method: 'POST', credentials: 'same-origin',
            });
            const body = await r.json().catch(() => ({}));
            if (r.ok) this._render(body);
        } finally {
            this._stopBtn.disabled = false;
        }
    }

    async _refreshStatus() {
        try {
            const r = await fetch('/api/reticulum/scrape/status', { credentials: 'same-origin' });
            if (!r.ok) return;
            this._render(await r.json());
        } catch (_e) { /* keep showing the last known state */ }
    }

    _render(s) {
        // Edge-triggered, not level-triggered -- without this, every status
        // poll after a completed job (running stays false forever until the
        // next Start) would re-fetch the collections list for no reason.
        const justFinished = this._wasRunning && !s.running;
        this._wasRunning = !!s.running;

        this._startBtn.disabled = !!s.running;
        this._stopBtn.hidden = !s.running;

        if (s.running) {
            const queued = s.pages_queued ? `, ${s.pages_queued} queued` : '';
            this._setStatus('pending', `Scraping "${s.name}" -- ${s.pages_done} page(s) done${queued}. Current: ${s.current_url || '…'}`);
        } else if (s.last_error) {
            this._setStatus('error', s.last_error);
        } else if (s.pages_done) {
            this._setStatus('success', `Done -- ${s.pages_done} page(s) saved to "${s.name}".`);
        } else {
            this._setStatus('', '');
        }

        if (justFinished) this._loadCollections();

        if (s.log_lines && s.log_lines.length) {
            this._logEl.hidden = false;
            this._logEl.textContent = s.log_lines.join('\n');
            this._logEl.scrollTop = this._logEl.scrollHeight;
        } else {
            this._logEl.hidden = true;
        }
    }

    _setStatus(kind, text) {
        if (!this._statusEl) return;
        this._statusEl.dataset.kind = kind;
        this._statusEl.textContent = text;
    }
}

window.registerPageHook({
    host: 'reticulum',
    label: 'Scrape',
    make: () => new ReticulumScraperHookPanel(),
});
