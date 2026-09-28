/**
 * OLED Display -- sidebar page for the oled-display plugin.
 *
 * Settings form (on/off, I2C address/driver, blank timeout) plus a live
 * preview of the last real status screen drawn -- fetched from GET
 * /api/oled-display/preview.png. Deliberately doesn't go black when the
 * physical panel auto-blanks for burn-in; a separate note (driven by
 * /status's `blanked` flag) says so instead, and the Wake button forces
 * the physical panel back on and restarts its blank timer.
 *
 * `route` below MUST match plugin.toml's [sidebar].route.
 */
window.registerSidebarPage({
    route: 'oled-display',
    make: () => new OledDisplayPage(),
});

class OledDisplayPage {
    constructor() {
        this.root = null;
        this._pollTimer = null;
    }

    mount(rootEl) {
        this.root = rootEl;
        rootEl.innerHTML = `
            <header class="lw-panel__head">
                <h2 class="lw-panel__title">OLED Display</h2>
            </header>
            <p class="oled-page__intro">Drives the board's I2C status OLED: a boot logo, then live device
                status (IP address, active capture sources, uptime). Auto-blanks
                after a timeout to avoid burn-in.</p>

            <div class="cfg-section cfg-section--grid">
                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Live preview</h3>
                    </header>
                    <p class="auth-status" data-oled-preview-status aria-live="polite">Loading…</p>
                    <div class="oled-preview">
                        <img data-oled-preview alt="Current OLED contents" hidden>
                    </div>
                    <p class="oled-blanked-note" data-oled-blanked-note hidden>
                        Physical screen is off (idle timeout, or put to sleep) -- showing its last contents.
                    </p>
                    <div class="oled-preview-actions">
                        <button class="terminal-button" type="button" data-oled-wake>Wake display</button>
                        <button class="terminal-button" type="button" data-oled-sleep>Sleep display</button>
                    </div>
                    <div class="oled-preview-actions" data-oled-page-controls hidden>
                        <button class="terminal-button" type="button" data-oled-prev-page>&lsaquo; Prev page</button>
                        <span class="oled-current-page" data-oled-current-page></span>
                        <button class="terminal-button" type="button" data-oled-next-page>Next page &rsaquo;</button>
                    </div>
                </article>

                <form class="oled-settings-form" data-oled-form>
                    <article class="cfg-card">
                        <header class="cfg-card__head">
                            <h3 class="cfg-card__title">Hardware</h3>
                        </header>
                        <div class="cfg-row">
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">I2C address</span>
                                <input class="cfg-field__input" data-oled-address placeholder="0x3D">
                            </label>
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Controller</span>
                                <select class="cfg-field__input" data-oled-driver>
                                    <option value="ssd1306">SSD1306</option>
                                    <option value="sh1106">SH1106</option>
                                    <option value="ssd1309">SSD1309</option>
                                </select>
                            </label>
                        </div>
                    </article>

                    <article class="cfg-card">
                        <header class="cfg-card__head">
                            <h3 class="cfg-card__title">Settings</h3>
                        </header>
                        <label class="cfg-field cfg-field--toggle">
                            <input type="checkbox" data-oled-enabled>
                            <span class="cfg-field__label">Display enabled</span>
                        </label>
                        <div class="cfg-row">
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Blank after (minutes, 0 = never)</span>
                                <input class="cfg-field__input" type="number" min="0" max="1440" data-oled-blank>
                            </label>
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Refresh interval (seconds)</span>
                                <input class="cfg-field__input" type="number" min="1" max="300" data-oled-refresh>
                            </label>
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Boot logo duration (seconds, 0 = skip it)</span>
                                <input class="cfg-field__input" type="number" min="0" max="30" step="0.5" data-oled-boot-logo>
                            </label>
                        </div>
                        <label class="cfg-field cfg-field--toggle">
                            <input type="checkbox" data-oled-rotate>
                            <span class="cfg-field__label">Rotate screens (Overview + one page per active protocol)</span>
                        </label>
                        <div class="cfg-row">
                            <label class="cfg-field cfg-field--narrow">
                                <span class="cfg-field__label">Seconds per page (when rotating)</span>
                                <input class="cfg-field__input" type="number" min="1" max="60" step="0.5" data-oled-rotate-seconds>
                            </label>
                        </div>
                        <div class="cfg-card__actions">
                            <button class="terminal-button terminal-button--primary" type="submit">Save</button>
                        </div>
                        <p class="cfg-status" data-oled-save-status aria-live="polite"></p>
                    </article>
                </form>
            </div>
        `;

        this._previewImg = this._q('[data-oled-preview]');
        this._previewStatus = this._q('[data-oled-preview-status]');
        this._blankedNote = this._q('[data-oled-blanked-note]');
        this._wakeButton = this._q('[data-oled-wake]');
        this._sleepButton = this._q('[data-oled-sleep]');
        this._pageControls = this._q('[data-oled-page-controls]');
        this._prevPageButton = this._q('[data-oled-prev-page]');
        this._nextPageButton = this._q('[data-oled-next-page]');
        this._currentPageLabel = this._q('[data-oled-current-page]');
        this._form = this._q('[data-oled-form]');
        this._saveStatus = this._q('[data-oled-save-status]');

        this._form.addEventListener('submit', (e) => {
            e.preventDefault();
            this._save();
        });
        this._wakeButton.addEventListener('click', () => this._wake());
        this._sleepButton.addEventListener('click', () => this._sleep());
        this._prevPageButton.addEventListener('click', () => this._prevPage());
        this._nextPageButton.addEventListener('click', () => this._nextPage());

        this._loadSettings();
        this._refreshPreview();
        this._pollTimer = setInterval(() => this._refreshPreview(), 5000);
    }

    unmount() {
        if (this._pollTimer) clearInterval(this._pollTimer);
        this._pollTimer = null;
    }

    _q(sel) { return this.root.querySelector(sel); }

    async _loadSettings() {
        try {
            const r = await fetch('/api/oled-display/status', { credentials: 'same-origin' });
            if (!r.ok) return;
            const s = await r.json();
            this._q('[data-oled-enabled]').checked = !!s.enabled;
            this._q('[data-oled-address]').value = s.i2c_address || '0x3D';
            this._q('[data-oled-driver]').value = s.driver || 'ssd1306';
            this._q('[data-oled-blank]').value = s.blank_after_minutes ?? 30;
            this._q('[data-oled-refresh]').value = s.refresh_seconds ?? 5;
            this._q('[data-oled-boot-logo]').value = s.boot_logo_seconds ?? 3;
            this._q('[data-oled-rotate]').checked = !!s.rotate_screens;
            this._q('[data-oled-rotate-seconds]').value = s.rotate_seconds ?? 4;
            this._pageControls.hidden = !s.rotate_screens;
        } catch (_) {}
    }

    async _refreshPreview() {
        try {
            const r = await fetch('/api/oled-display/preview.png?' + Date.now(), { credentials: 'same-origin' });
            if (!r.ok) {
                this._previewStatus.textContent = r.status === 503
                    ? 'No frame rendered yet (display off, or not started).'
                    : `Preview unavailable (HTTP ${r.status}).`;
                this._previewImg.hidden = true;
                this._blankedNote.hidden = true;
                return;
            }
            const blob = await r.blob();
            const url = URL.createObjectURL(blob);
            if (this._previewImg.src) URL.revokeObjectURL(this._previewImg.src);
            this._previewImg.src = url;
            this._previewImg.hidden = false;
            this._previewStatus.textContent = '';
        } catch (e) {
            this._previewStatus.textContent = `Network error: ${e.message}`;
            return;
        }

        // Separate call: preview.png always serves the last real status
        // frame now (never a blank one), so whether the physical panel
        // is actually blanked right now -- and which rotate_screens page
        // is currently up -- has to come from /status instead.
        try {
            const r = await fetch('/api/oled-display/status', { credentials: 'same-origin' });
            const s = r.ok ? await r.json() : {};
            this._blankedNote.hidden = !s.blanked;
            this._pageControls.hidden = !s.rotate_screens;
            this._currentPageLabel.textContent = s.current_page ? `Showing: ${s.current_page}` : '';
        } catch (_) {}
    }

    _wake() { return this._triggerAction(this._wakeButton, 'wake', 'Waking…'); }
    _sleep() { return this._triggerAction(this._sleepButton, 'sleep', 'Sleeping…'); }
    _prevPage() { return this._triggerAction(this._prevPageButton, 'page/prev', '…'); }
    _nextPage() { return this._triggerAction(this._nextPageButton, 'page/next', '…'); }

    async _triggerAction(button, endpoint, pendingLabel) {
        button.disabled = true;
        const originalLabel = button.textContent;
        button.textContent = pendingLabel;
        try {
            const r = await fetch(`/api/oled-display/${endpoint}`, { method: 'POST', credentials: 'same-origin' });
            if (r.ok) {
                await this._refreshPreview();
            } else {
                const data = await r.json().catch(() => ({}));
                this._previewStatus.textContent = data.detail || `${endpoint} failed (HTTP ${r.status})`;
            }
        } catch (e) {
            this._previewStatus.textContent = `Network error: ${e.message}`;
        } finally {
            button.disabled = false;
            button.textContent = originalLabel;
        }
    }

    async _save() {
        const body = {
            enabled: this._q('[data-oled-enabled]').checked,
            i2c_address: this._q('[data-oled-address]').value.trim(),
            driver: this._q('[data-oled-driver]').value,
            blank_after_minutes: parseInt(this._q('[data-oled-blank]').value, 10),
            refresh_seconds: parseInt(this._q('[data-oled-refresh]').value, 10),
            boot_logo_seconds: parseFloat(this._q('[data-oled-boot-logo]').value),
            rotate_screens: this._q('[data-oled-rotate]').checked,
            rotate_seconds: parseFloat(this._q('[data-oled-rotate-seconds]').value),
        };
        this._saveStatus.dataset.kind = 'pending';
        this._saveStatus.textContent = 'Saving…';
        try {
            const r = await fetch('/api/oled-display/settings', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify(body),
            });
            const data = await r.json().catch(() => ({}));
            if (!r.ok) {
                this._saveStatus.dataset.kind = 'error';
                this._saveStatus.textContent = data.detail || `Failed (HTTP ${r.status})`;
                return;
            }
            this._saveStatus.dataset.kind = 'success';
            this._saveStatus.textContent = 'Saved -- restart the service to apply.';
        } catch (e) {
            this._saveStatus.dataset.kind = 'error';
            this._saveStatus.textContent = `Network error: ${e.message}`;
        }
    }
}
