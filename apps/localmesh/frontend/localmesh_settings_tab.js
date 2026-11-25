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
    _MAX_CHANNEL = 127;   // 0..127, matches settings_routes.py's own MAX_CHANNEL (firmware and app; was 39 until 20-11)

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
                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Channel names</h3>
                        <p class="cfg-card__hint">
                            A receiver often never reports a name for a channel slot, so messages on it just show
                            "channel N". Name the ones you use (up to ${this._MAX_CHANNEL + 1}, numbered 0-${this._MAX_CHANNEL})
                            -- this overrides whatever the receiver itself reports, takes effect immediately, no
                            restart needed. Rows for channels the receiver has already reported are pre-filled below;
                            use "+ Add channel" for one it hasn't heard yet.
                        </p>
                    </header>
                    <div class="cfg-companions" data-lm-channels></div>
                    <div class="cfg-companions__add-row">
                        <button class="terminal-button" type="button" data-lm-add-channel>+ Add channel</button>
                    </div>
                    <div class="cfg-card__actions">
                        <button class="terminal-button terminal-button--primary" type="button" data-lm-save-channels>
                            Save channel names
                        </button>
                    </div>
                    <p class="cfg-status" data-lm-channels-status aria-live="polite"></p>
                </article>
                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Repeater status</h3>
                        <p class="cfg-card__hint">
                            With the status key this page shows what repeaters report about themselves (battery,
                            uptime, noise, airtime, neighbours). Make it on your own computer with the plugin's
                            tools/status_key.py; it starts with lmst1-. Never enter the network key here. After a
                            network key change, enter the new one (the old one keeps working for 24 hours).
                        </p>
                    </header>
                    <label class="cfg-field">
                        <span class="cfg-field__label">Status key</span>
                        <input class="cfg-field__input" type="password" autocomplete="new-password" spellcheck="false"
                               placeholder="lmst1-…" data-lm-key>
                        <span class="cfg-field__hint" data-lm-key-state></span>
                    </label>
                    <div class="cfg-card__actions">
                        <button class="terminal-button terminal-button--primary" type="button" data-lm-key-save>
                            Save status key
                        </button>
                        <button class="terminal-button terminal-button--danger" type="button" data-lm-key-remove hidden>
                            Remove
                        </button>
                    </div>
                    <p class="cfg-status" data-lm-key-status aria-live="polite"></p>
                </article>
            </div>
        `;
        this._devicesEl = this._el.querySelector('[data-lm-devices]');
        this._addBtn = this._el.querySelector('[data-lm-add]');
        this._addBtn.addEventListener('click', () => this._addRow());
        this._el.querySelector('[data-lm-save]').addEventListener('click', () => this._save());
        this._el.querySelector('[data-lm-rescan]').addEventListener('click', (e) => this._rescan(e.currentTarget));

        this._el.querySelector('[data-lm-key-save]').addEventListener('click', () => {
            const key = this._el.querySelector('[data-lm-key]').value.trim();
            if (key) this._saveKey(key);   // an empty field does not remove the key; Remove does
        });
        this._el.querySelector('[data-lm-key-remove]').addEventListener('click', () => this._saveKey(''));

        this._channelsEl = this._el.querySelector('[data-lm-channels]');
        this._el.querySelector('[data-lm-add-channel]').addEventListener('click', () => this._addChannelRow());
        this._el.querySelector('[data-lm-save-channels]').addEventListener('click', () => this._saveChannelNames());
    }

    async _load() {
        const [settings, status] = await Promise.all([
            this._request('GET', '/api/localmesh/settings'),
            this._request('GET', '/api/localmesh/status'),
        ]);
        if (settings) {
            this._showKey(settings.status_key_set);
            this._devicesEl.innerHTML = '';
            const devices = Array.isArray(settings.devices) && settings.devices.length
                ? settings.devices : [{ label: '', serial_port: '' }];
            devices.forEach((d) => this._addRow(d));
            await this._refreshPorts();
            this._loadChannelNames(settings.channel_names || {}, status || []);
        }
    }

    /** Pre-fills one row per channel the receiver(s) have actually reported (GET /api/localmesh/status's own
     * per-receiver `channels` map, same source the main page's Receiver tab reads), union'd with whatever already
     * has a saved override -- so a channel with a name saved but not currently reported (receiver offline, or
     * just hasn't re-sent CHANNELS_GET yet) doesn't silently lose its row. Sorted by channel number. */
    _loadChannelNames(channelNames, statusList) {
        this._channelsEl.innerHTML = '';
        const reported = new Map();   // channel -> receiver-reported name (empty string if none)
        (Array.isArray(statusList) ? statusList : []).forEach((s) => {
            Object.entries(s.channels || {}).forEach(([ch, name]) => {
                if (!reported.has(Number(ch))) reported.set(Number(ch), name || '');
            });
        });
        const channels = new Set([...Object.keys(channelNames).map(Number), ...reported.keys()]);
        if (!channels.size) {
            this._addChannelRow();   // nothing known yet -- one empty row to get started
            return;
        }
        [...channels].sort((a, b) => a - b).forEach((ch) => {
            this._addChannelRow({ channel: ch, name: channelNames[ch] ?? channelNames[String(ch)] ?? '',
                                  reportedName: reported.get(ch) || '' });
        });
    }

    _addChannelRow(data = {}) {
        if (this._channelsEl.children.length > this._MAX_CHANNEL) return;   // at most one row per channel number
        const div = document.createElement('div');
        div.className = 'cfg-companion';
        const hint = data.reportedName ? `Receiver reports: "${this._esc(data.reportedName)}"` : 'Not reported by any receiver yet';
        div.innerHTML = `
            <div class="cfg-companion__header">
                <label class="cfg-field" style="flex:0 0 90px">
                    <span class="cfg-field__label">Channel</span>
                    <input class="cfg-field__input" type="number" min="0" max="${this._MAX_CHANNEL}" step="1"
                           value="${data.channel ?? ''}" data-lm-channel-num>
                </label>
                <label class="cfg-companion__label-wrap">
                    <span class="cfg-field__label">Name</span>
                    <input class="cfg-field__input" type="text" maxlength="32"
                           placeholder="e.g. Algemeen" value="${this._esc(data.name || '')}" data-lm-channel-name>
                </label>
                <button class="cfg-companion__remove terminal-button terminal-button--danger"
                        type="button" title="Remove this override">✕</button>
            </div>
            <span class="cfg-field__hint" data-lm-channel-hint>${hint}</span>
        `;
        div.querySelector('.cfg-companion__remove').addEventListener('click', () => div.remove());
        this._channelsEl.appendChild(div);
    }

    /** Both save buttons PUT the *whole* settings object (devices + channel_names) -- omitting either one would
     * silently wipe it server-side (settings_routes.py's update replaces, doesn't merge), so every save reads
     * both cards' current DOM state regardless of which button was clicked. Returns null (and leaves the given
     * status element showing the problem) if a channel row doesn't parse. */
    _collectDevices() {
        return [...this._devicesEl.querySelectorAll('.cfg-companion')]
            .map((div) => ({
                label: div.querySelector('[data-lm-label]').value.trim().toLowerCase(),
                serial_port: div.querySelector('[data-lm-port]').value.trim(),
            }))
            .filter((d) => d.label || d.serial_port);   // an untouched empty row is not a receiver
    }

    _collectChannelNames(statusEl) {
        const channel_names = {};
        let bad = null;
        this._channelsEl.querySelectorAll('.cfg-companion').forEach((div) => {
            const raw = div.querySelector('[data-lm-channel-num]').value.trim();
            const name = div.querySelector('[data-lm-channel-name]').value.trim();
            if (!raw && !name) return;   // an untouched empty row
            const ch = parseInt(raw, 10);
            if (!raw || Number.isNaN(ch) || ch < 0 || ch > this._MAX_CHANNEL) {
                bad = `channel number must be 0-${this._MAX_CHANNEL}`;
                return;
            }
            if (name) channel_names[ch] = name;   // a row with a number but no name just means "no override" -- drop it
        });
        if (bad) {
            statusEl.dataset.kind = 'error';
            statusEl.textContent = bad;
            return null;
        }
        return channel_names;
    }

    /** The key itself never comes back from the server, only whether there is one. */
    _showKey(isSet) {
        this._el.querySelector('[data-lm-key]').value = '';
        this._el.querySelector('[data-lm-key-state]').textContent = isSet ? 'A status key is set.' : 'No status key set.';
        this._el.querySelector('[data-lm-key-remove]').hidden = !isSet;
    }

    async _saveKey(key) {
        const status = this._el.querySelector('[data-lm-key-status]');
        status.dataset.kind = 'pending';
        status.textContent = 'Saving…';
        const result = await this._request('PUT', '/api/localmesh/status-key', { key });
        status.dataset.kind = result ? 'success' : 'error';
        status.textContent = !result ? 'Save failed.'
            : key ? 'Saved. New status packets are shown from now on.' : 'Removed.';
        if (result) this._showKey(result.status_key_set);
    }

    async _saveChannelNames() {
        const status = this._el.querySelector('[data-lm-channels-status]');
        status.dataset.kind = 'pending';
        status.textContent = 'Saving…';
        const channel_names = this._collectChannelNames(status);
        if (channel_names === null) return;
        const result = await this._request('PUT', '/api/localmesh/settings',
            { devices: this._collectDevices(), channel_names });
        if (result) {
            status.dataset.kind = 'success';
            status.textContent = result.restart_required
                ? 'Saved. Restart the service from Settings → System to apply the receiver changes too.'
                : 'Saved.';
            await this._load();
        } else {
            status.dataset.kind = 'error';
            status.textContent = 'Save failed.';
        }
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
        const channelStatus = this._el.querySelector('[data-lm-channels-status]');
        const channel_names = this._collectChannelNames(channelStatus);
        if (channel_names === null) {
            status.dataset.kind = 'error';
            status.textContent = 'Fix the channel names card below, then save again.';
            return;
        }
        const result = await this._request('PUT', '/api/localmesh/settings',
            { devices: this._collectDevices(), channel_names });
        if (result) {
            status.dataset.kind = 'success';
            status.textContent = result.restart_required
                ? 'Saved. Restart the service from Settings → System to apply.'
                : 'Saved.';
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
