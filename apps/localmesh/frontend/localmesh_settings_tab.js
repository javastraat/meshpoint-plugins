/**
 * LocalMesh page: "Settings" tab (admin).
 *
 * The receiver list (label + serial port), saved through PUT /api/localmesh/settings (settings_routes.py) into
 * plugins.localmesh in local.yaml. A cut-down copy of the DAPNET plugin's device editor: same cfg-* classes, the
 * same shared GET /api/config/serial-ports port picker, and the same small _request/_toast stand-ins (a plugin
 * page has no access to the Configuration page's API wrapper). Turning the plugin on and off stays with
 * Settings -> Plugins.
 */

class LocalMeshSettingsTab {
    _MAX_DEVICES = 4;

    constructor(el) {
        this._el = el;
        this._mounted = false;
        this._ports = [];
    }

    show() {
        if (!this._mounted) this._mount();
        this._load();
    }

    hide() {}

    _mount() {
        this._mounted = true;
        this._el.innerHTML = `
            <div class="cfg-section">
                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">LocalMesh receivers</h3>
                        <p class="cfg-card__hint">
                            A Heltec V4 with LocalMesh companion firmware on USB, one per entry (up to
                            ${this._MAX_DEVICES}). The board must be a member of the network. Changes need a
                            service restart.
                        </p>
                    </header>
                    <div class="cfg-companions" data-lm-devices></div>
                    <datalist id="lm-settings-ports-list"></datalist>
                    <div class="cfg-companions__add-row">
                        <button class="terminal-button" type="button" data-lm-add>+ Add receiver</button>
                        <button class="terminal-button" type="button" data-lm-rescan
                                title="Re-scan connected USB devices for the port picker">↻ Rescan USB</button>
                    </div>
                    <div class="cfg-card__actions">
                        <button class="terminal-button terminal-button--primary" type="button" data-lm-save>
                            Save receivers
                        </button>
                    </div>
                    <p class="cfg-status" data-lm-status aria-live="polite"></p>
                </article>
            </div>
        `;
        this._devicesEl = this._el.querySelector('[data-lm-devices]');
        this._addBtn = this._el.querySelector('[data-lm-add]');
        this._addBtn.addEventListener('click', () => this._addRow());
        this._el.querySelector('[data-lm-save]').addEventListener('click', () => this._save());
        this._el.querySelector('[data-lm-rescan]').addEventListener('click', (e) => this._rescan(e.currentTarget));
    }

    async _load() {
        const settings = await this._request('GET', '/api/localmesh/settings');
        if (!settings) return;
        this._devicesEl.innerHTML = '';
        const devices = Array.isArray(settings.devices) && settings.devices.length
            ? settings.devices : [{ label: '', serial_port: '' }];
        devices.forEach((d) => this._addRow(d));
        await this._refreshPorts();
    }

    _addRow(data = {}) {
        if (this._devicesEl.children.length >= this._MAX_DEVICES) return;
        const div = document.createElement('div');
        div.className = 'cfg-companion';
        div.innerHTML = `
            <div class="cfg-companion__header">
                <span class="cfg-companion__num"></span>
                <label class="cfg-companion__label-wrap">
                    <span class="cfg-field__label">Label</span>
                    <input class="cfg-field__input cfg-companion__label-input" type="text" maxlength="16"
                           placeholder="e.g. base" value="${this._esc(data.label)}" data-lm-label>
                </label>
                <button class="cfg-companion__remove terminal-button terminal-button--danger"
                        type="button" title="Remove receiver">✕</button>
            </div>
            <label class="cfg-field">
                <span class="cfg-field__label">Serial port</span>
                <input class="cfg-field__input" type="text" list="lm-settings-ports-list"
                       placeholder="/dev/serial/by-id/usb-Espressif_..." value="${this._esc(data.serial_port)}"
                       data-lm-port>
                <span class="cfg-field__resolved" data-lm-port-resolved></span>
                <span class="cfg-field__hint">
                    Pick the board from the list (an Espressif entry), or type a path. The
                    /dev/serial/by-id/... name stays the same whichever USB port the board is in.
                </span>
            </label>
        `;
        div.querySelector('.cfg-companion__remove').addEventListener('click', () => {
            div.remove();
            this._sync();
        });
        const port = div.querySelector('[data-lm-port]');
        port.addEventListener('input', () => this._resolve(port));
        this._devicesEl.appendChild(div);
        this._resolve(port);
        this._sync();
    }

    /** Renumber the rows and hide "+ Add" at the maximum. */
    _sync() {
        this._devicesEl.querySelectorAll('.cfg-companion__num').forEach((el, i) => {
            el.textContent = `Receiver ${i + 1}`;
        });
        this._addBtn.hidden = this._devicesEl.children.length >= this._MAX_DEVICES;
    }

    async _save() {
        const status = this._el.querySelector('[data-lm-status]');
        status.dataset.kind = 'pending';
        status.textContent = 'Saving…';
        const devices = [...this._devicesEl.querySelectorAll('.cfg-companion')]
            .map((div) => ({
                label: div.querySelector('[data-lm-label]').value.trim().toLowerCase(),
                serial_port: div.querySelector('[data-lm-port]').value.trim(),
            }))
            .filter((d) => d.label || d.serial_port);   // an untouched empty row is not a receiver
        const result = await this._request('PUT', '/api/localmesh/settings', { devices });
        if (result) {
            status.dataset.kind = 'success';
            status.textContent = 'Saved. Restart the service from Settings → System to apply.';
            await this._load();
        } else {
            status.dataset.kind = 'error';
            status.textContent = 'Save failed.';
        }
    }

    async _rescan(button) {
        const text = button.textContent;
        button.disabled = true;
        button.textContent = 'Scanning…';
        try { await this._refreshPorts(); } finally {
            button.textContent = text;
            button.disabled = false;
        }
    }

    /** The core's shared port list (also used by the Serial/MeshCore/DAPNET settings); best effort. */
    async _refreshPorts() {
        const result = await this._request('GET', '/api/config/serial-ports');
        this._ports = (result && Array.isArray(result.ports)) ? result.ports : [];
        this._el.querySelector('#lm-settings-ports-list').innerHTML = this._ports.map((p) => {
            const label = [(p.device || '').split('/').pop(), p.description].filter(Boolean).join(' — ');
            return `<option value="${this._esc(p.stable_path)}" label="${this._esc(label)}"></option>`;
        }).join('');
        this._devicesEl.querySelectorAll('[data-lm-port]').forEach((input) => this._resolve(input));
    }

    /** Shows which /dev/tty... a stable path points to right now, so you can see the board is there. */
    _resolve(input) {
        const out = input.parentElement.querySelector('[data-lm-port-resolved]');
        const value = input.value.trim();
        const match = value && this._ports.find((p) =>
            p.stable_path === value || p.by_id === value || p.by_path === value || p.device === value);
        out.textContent = match && match.device && match.device !== value ? `→ ${match.device}` : '';
    }

    async _request(method, url, body) {
        const init = { method, headers: { 'Content-Type': 'application/json' }, credentials: 'same-origin' };
        if (body !== undefined) init.body = JSON.stringify(body);
        try {
            const res = await fetch(url, init);
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                this._toast(`${method === 'GET' ? 'Error' : 'Save failed'}: ${err.detail || res.status}`);
                return null;
            }
            return await res.json();
        } catch (e) {
            this._toast(`${method === 'GET' ? 'Error' : 'Save failed'}: ${e.message}`);
            return null;
        }
    }

    /** The Configuration page's #cfg-toast (configuration.css is loaded on every page). */
    _toast(text) {
        let toast = document.getElementById('cfg-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'cfg-toast';
            toast.className = 'cfg-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = text;
        toast.classList.add('cfg-toast--visible');
        setTimeout(() => toast.classList.remove('cfg-toast--visible'), 2800);
    }

    _esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}

window.LocalMeshSettingsTab = LocalMeshSettingsTab;
