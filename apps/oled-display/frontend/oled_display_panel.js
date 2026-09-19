/**
 * OLED Display -- sidebar page for the oled-display plugin.
 *
 * Settings form (on/off, I2C address/driver, blank timeout) plus a live
 * preview of exactly what's currently on the physical screen -- fetched
 * from GET /api/oled-display/preview.png, the same PNG DisplayService
 * captured from its last luma canvas draw.
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
            <div class="plugin-page oled-page">
                <h2>OLED Display</h2>
                <p>Drives the board's I2C status OLED: a boot logo, then live device
                    status (IP address, active capture sources, uptime). Auto-blanks
                    after a timeout to avoid burn-in.</p>

                <article class="oled-card">
                    <h3 class="oled-card__title">Live preview</h3>
                    <p class="auth-status" data-oled-preview-status aria-live="polite">Loading…</p>
                    <div class="oled-preview">
                        <img data-oled-preview alt="Current OLED contents" hidden>
                    </div>
                </article>

                <article class="oled-card">
                    <h3 class="oled-card__title">Settings</h3>
                    <form data-oled-form>
                        <label class="cfg-field">
                            <span class="cfg-field__label">
                                <input type="checkbox" data-oled-enabled> Display enabled
                            </span>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">I2C address</span>
                            <input class="cfg-field__input" data-oled-address placeholder="0x3D">
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Controller</span>
                            <select class="cfg-field__input" data-oled-driver>
                                <option value="ssd1306">SSD1306</option>
                                <option value="sh1106">SH1106</option>
                                <option value="ssd1309">SSD1309</option>
                            </select>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Blank after (minutes, 0 = never)</span>
                            <input class="cfg-field__input" type="number" min="0" max="1440" data-oled-blank>
                        </label>
                        <label class="cfg-field">
                            <span class="cfg-field__label">Refresh interval (seconds)</span>
                            <input class="cfg-field__input" type="number" min="1" max="300" data-oled-refresh>
                        </label>
                        <p class="cfg-status" data-oled-save-status aria-live="polite"></p>
                        <button class="terminal-button" type="submit">Save</button>
                    </form>
                </article>
            </div>
        `;

        this._previewImg = this._q('[data-oled-preview]');
        this._previewStatus = this._q('[data-oled-preview-status]');
        this._form = this._q('[data-oled-form]');
        this._saveStatus = this._q('[data-oled-save-status]');

        this._form.addEventListener('submit', (e) => {
            e.preventDefault();
            this._save();
        });

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
        }
    }

    async _save() {
        const body = {
            enabled: this._q('[data-oled-enabled]').checked,
            i2c_address: this._q('[data-oled-address]').value.trim(),
            driver: this._q('[data-oled-driver]').value,
            blank_after_minutes: parseInt(this._q('[data-oled-blank]').value, 10),
            refresh_seconds: parseInt(this._q('[data-oled-refresh]').value, 10),
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
