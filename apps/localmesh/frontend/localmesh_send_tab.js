/**
 * LocalMesh page: "Send" tab (admin). The only place the Meshpoint transmits on LocalMesh.
 *
 *  - Send a message: to a channel the receiver has the key for, or a direct message to a member. A byte counter
 *    (LocalMesh counts bytes, not characters; a channel message carries the receiver's name in front).
 *  - Name on the network: the receiver's name (the board broadcasts it 5 s after a change) and "Broadcast now".
 *  - Recently sent: what this Meshpoint sent, with its status (sent, repeated = heard back from a repeater,
 *    delivered / failed for direct messages).
 *
 * POST /api/localmesh/send, PUT /api/localmesh/name, POST /api/localmesh/advert (send_routes.py); the panel hands
 * over the receivers (GET /api/localmesh/status) on every refresh via update().
 */

const LM_CHANNEL_TEXT_MAX = 155;   // protocol.py CHANNEL_TEXT_MAX: includes "Name: " in front
const LM_DIRECT_TEXT_MAX = 149;
const LM_SENT_BADGE = { sent: 'data', repeated: 'join', delivered: 'join', failed: 'undecrypted' };
const LM_SENT_TEXT = {
    sent: 'sent', repeated: 'repeated', delivered: 'delivered', failed: 'not delivered',
};

class LocalMeshSendTab {
    constructor(el) {
        this._el = el;
        this._mounted = false;
        this._devices = [];
        this._members = [];
        this._presetTo = null;
    }

    show() {
        if (!this._mounted) this._mount();
        this._render();
        this._loadMembers();
        this._loadSent();
    }

    hide() {}

    /** The receivers, from the panel's refresh (GET /api/localmesh/status). */
    update(devices) {
        this._devices = Array.isArray(devices) ? devices : [];
        if (this._mounted) this._render();
    }

    /** Open with a direct message to this member selected (from the member side panel). */
    presetTo(member) {
        this._presetTo = member;
        if (this._mounted) this._fillTargets();
    }

    _mount() {
        this._mounted = true;
        this._el.innerHTML = `
            <div class="cfg-section">
                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Send a message</h3>
                        <p class="cfg-card__hint">Goes out over the air from the receiver, within its airtime budget.</p>
                    </header>
                    <label class="cfg-field" data-lm-send-rx-wrap hidden>
                        <span class="cfg-field__label">Receiver</span>
                        <select class="cfg-field__input" data-lm-send-rx></select>
                    </label>
                    <label class="cfg-field">
                        <span class="cfg-field__label">To</span>
                        <select class="cfg-field__input" data-lm-send-to></select>
                    </label>
                    <label class="cfg-field">
                        <span class="cfg-field__label">Message</span>
                        <textarea class="cfg-field__input" rows="3" data-lm-send-text
                                  placeholder="Message text"></textarea>
                        <span class="cfg-field__hint" data-lm-send-count></span>
                    </label>
                    <div class="cfg-card__actions">
                        <button class="terminal-button terminal-button--primary" type="button" data-lm-send>Send</button>
                    </div>
                    <p class="cfg-status" data-lm-send-status aria-live="polite"></p>
                </article>

                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Name on the network</h3>
                        <p class="cfg-card__hint">
                            Other members see this name instead of the member number, and it goes in front of every
                            channel message this receiver sends. After a change the receiver broadcasts it by itself
                            within 5 seconds; it also does so every 24 hours.
                        </p>
                    </header>
                    <label class="cfg-field">
                        <span class="cfg-field__label">Name (max. 32 bytes)</span>
                        <input class="cfg-field__input" type="text" maxlength="32" data-lm-name
                               placeholder="e.g. Meshpoint attic">
                        <span class="cfg-field__hint" data-lm-name-hint></span>
                    </label>
                    <div class="cfg-card__actions">
                        <button class="terminal-button terminal-button--primary" type="button" data-lm-name-save>Save name</button>
                        <button class="terminal-button" type="button" data-lm-advert>Broadcast name now</button>
                    </div>
                    <p class="cfg-status" data-lm-name-status aria-live="polite"></p>
                </article>

                <article class="cfg-card">
                    <header class="cfg-card__head">
                        <h3 class="cfg-card__title">Recently sent</h3>
                        <p class="cfg-card__hint">
                            Repeated: a repeater was heard passing it on. Delivered / not delivered: the answer to a
                            direct message. Also on the Messages tab.
                        </p>
                    </header>
                    <div class="lw-table-wrap" data-lm-sent></div>
                </article>
            </div>
        `;
        const q = (sel) => this._el.querySelector(sel);
        q('[data-lm-send-rx]').addEventListener('change', () => this._render());
        q('[data-lm-send-to]').addEventListener('change', () => this._count());
        q('[data-lm-send-text]').addEventListener('input', () => this._count());
        q('[data-lm-send-text]').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) this._send();   // Ctrl/Cmd+Enter sends
        });
        q('[data-lm-send]').addEventListener('click', () => this._send());
        q('[data-lm-name-save]').addEventListener('click', () => this._saveName());
        q('[data-lm-advert]').addEventListener('click', () => this._advert());
    }

    _device() {
        const label = this._el.querySelector('[data-lm-send-rx]').value;
        return this._devices.find((d) => (d.label || '') === label) || this._devices[0] || null;
    }

    _render() {
        const rx = this._el.querySelector('[data-lm-send-rx]');
        const current = rx.value;
        rx.innerHTML = this._devices.map((d) =>
            `<option value="${this._esc(d.label || '')}">${this._esc(d.label || 'receiver')}${d.connected ? '' : ' (not connected)'}</option>`).join('');
        if (this._devices.some((d) => (d.label || '') === current)) rx.value = current;
        this._el.querySelector('[data-lm-send-rx-wrap]').hidden = this._devices.length < 2;
        const d = this._device();
        const name = this._el.querySelector('[data-lm-name]');
        if (document.activeElement !== name) name.value = (d && d.net_name) || '';
        // Firmware 13-11 tells its own name (name_from_board); older firmware: only what this Meshpoint last set.
        this._el.querySelector('[data-lm-name-hint]').textContent = !d ? ''
            : d.name_from_board ? (d.net_name ? 'As stored on the board.' : 'No name set on the board yet.')
            : d.net_name ? 'As last set from this Meshpoint (this firmware does not tell its name).'
            : 'Not set from this Meshpoint yet (this firmware does not tell its name).';
        this._fillTargets();
    }

    _fillTargets() {
        const to = this._el.querySelector('[data-lm-send-to]');
        const d = this._device();
        const own = d && d.info ? d.info.member : null;
        const keep = this._presetTo != null ? `m${this._presetTo}` : to.value;
        const channels = Object.entries((d && d.channels) || {});
        const members = this._members.filter((m) => m.member !== own && !m.blocked);
        to.innerHTML = `
            <optgroup label="Channels">${channels.length
                ? channels.map(([id, n]) => `<option value="c${id}">${this._esc(n || `channel ${id}`)}</option>`).join('')
                : '<option value="" disabled>no channel key on this receiver</option>'}</optgroup>
            <optgroup label="Direct message">${members.map((m) =>
                `<option value="m${m.member}">${this._esc(m.name ? `${m.name} (${m.member})` : `member ${m.member}`)}</option>`).join('')}</optgroup>`;
        if ([...to.options].some((o) => o.value === keep && !o.disabled)) to.value = keep;
        this._presetTo = null;
        this._count();
    }

    /** Byte limit for the selected target; a channel message loses room to "Name: " in front. */
    _limit() {
        const to = this._el.querySelector('[data-lm-send-to]').value;
        if (to.startsWith('m')) return LM_DIRECT_TEXT_MAX;
        const name = (this._device() || {}).net_name || '';
        return LM_CHANNEL_TEXT_MAX - (name ? new TextEncoder().encode(name).length + 2 : 0);
    }

    _count() {
        const text = this._el.querySelector('[data-lm-send-text]').value.trim();
        const n = new TextEncoder().encode(text).length;
        const limit = this._limit();
        const el = this._el.querySelector('[data-lm-send-count]');
        el.textContent = `${n} / ${limit} bytes${n > limit ? ' — too long' : ''}`;
        el.style.color = n > limit ? 'var(--accent-red)' : '';
        this._el.querySelector('[data-lm-send]').disabled = !text || n > limit || !this._el.querySelector('[data-lm-send-to]').value;
    }

    async _send() {
        const btn = this._el.querySelector('[data-lm-send]');
        if (btn.disabled) return;
        const d = this._device();
        const to = this._el.querySelector('[data-lm-send-to]').value;
        const textEl = this._el.querySelector('[data-lm-send-text]');
        const body = { receiver: d ? d.label || '' : null, text: textEl.value.trim() };
        if (to.startsWith('m')) body.to = Number(to.slice(1)); else body.channel = Number(to.slice(1));
        btn.disabled = true;
        const result = await this._request('POST', '/api/localmesh/send', body, '[data-lm-send-status]', 'Sending…');
        if (result) {
            this._status('[data-lm-send-status]', 'success',
                `Sent (#${result.counter}, ${result.airtime_ms} ms airtime).`);
            textEl.value = '';
            this._loadSent();
        }
        this._count();
    }

    async _saveName() {
        const d = this._device();
        const name = this._el.querySelector('[data-lm-name]').value.trim();
        const result = await this._request('PUT', '/api/localmesh/name', { receiver: d ? d.label || '' : null, name },
            '[data-lm-name-status]', 'Saving…', 'Not saved');
        if (result) {
            if (d) d.net_name = result.name;
            this._status('[data-lm-name-status]', 'success', 'Saved. The receiver broadcasts it within 5 seconds if it changed.');
            this._render();
        }
    }

    async _advert() {
        const d = this._device();
        const result = await this._request('POST', '/api/localmesh/advert', { receiver: d ? d.label || '' : null },
            '[data-lm-name-status]', 'Broadcasting…');
        if (result) this._status('[data-lm-name-status]', 'success', `Name broadcast (${result.airtime_ms} ms airtime).`);
    }

    async _loadMembers() {
        try {
            const res = await fetch('/api/localmesh/members', { credentials: 'same-origin' });
            if (res.ok) this._members = await res.json();
        } catch (_) { /* keep the last list */ }
        this._fillTargets();
    }

    async _loadSent() {
        let list = [];
        try {
            const res = await fetch('/api/localmesh/messages?limit=300', { credentials: 'same-origin' });
            if (res.ok) list = (await res.json()).filter((m) => m.outgoing).slice(0, 20);
        } catch (_) { return; }
        const P = window.LocalMeshPanel;
        const el = this._el.querySelector('[data-lm-sent]');
        el.innerHTML = list.length ? `
            <table class="lw-table lm-table">
                <colgroup><col class="lm-time"><col class="lm-channel"><col><col class="lm-type"></colgroup>
                <thead><tr><th>Time</th><th>To</th><th>Text</th><th>Status</th></tr></thead>
                <tbody>${list.map((m) => `<tr>
                    <td>${P.clock(m.time)}</td>
                    <td>${this._esc(m.channel_name)}</td>
                    <td>${this._esc(m.text)}</td>
                    <td><span class="lw-badge lw-badge--${LM_SENT_BADGE[m.status] || 'data'}">${LM_SENT_TEXT[m.status] || this._esc(m.status)}</span></td>
                </tr>`).join('')}</tbody>
            </table>` : '<p class="cfg-card__hint">Nothing sent from this Meshpoint yet.</p>';
    }

    _status(sel, kind, text) {
        const el = this._el.querySelector(sel);
        el.dataset.kind = kind;
        el.textContent = text;
    }

    /** fetch with the error text from the API in the card's status line. */
    async _request(method, url, body, statusSel, pending, failed = 'Not sent') {
        this._status(statusSel, 'pending', pending);
        try {
            const res = await fetch(url, {
                method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                const detail = typeof err.detail === 'string' ? err.detail : `error ${res.status}`;
                this._status(statusSel, 'error', `${failed}: ${detail}.`);
                return null;
            }
            return await res.json();
        } catch (e) {
            this._status(statusSel, 'error', `${failed}: ${e.message}.`);
            return null;
        }
    }

    _esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}

window.LocalMeshSendTab = LocalMeshSendTab;
