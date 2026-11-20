/**
 * LocalMesh Bot -- an extra "Bot" tab on the LocalMesh page (the localmesh plugin adds one tab per page hook for host
 * 'localmesh', admin only). Three cards: the bot (on/off, receiver and its name, channels, prefix, limits), the
 * commands (built-in .help/.ping/.trace plus your own command -> answer) and a log of what it heard and answered.
 *
 * GET/PUT /api/localmesh-bot/config, GET /api/localmesh-bot/log (backend/routes.py); the bot's name is the receiver's
 * own name on the network (the firmware puts it in front of every channel message), changed through the localmesh
 * plugin's PUT /api/localmesh/name like on the Send tab.
 */
(function () {
    'use strict';

    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    class LocalMeshBotTab {
        constructor() {
            this._el = null;
            this._data = null;     // GET /config
            this._timer = null;
        }

        mount(el) {
            this._el = el;
            el.innerHTML = `
                <div class="cfg-section lmbot">
                    <article class="cfg-card">
                        <header class="cfg-card__head">
                            <h3 class="cfg-card__title">Bot</h3>
                            <p class="cfg-card__hint">Answers commands such as <code>.ping</code> on the channels you
                                pick, through a LocalMesh receiver. Every answer is a flood through every repeater, so
                                the bot keeps to a cooldown per member and a limit per hour.</p>
                        </header>
                        <label class="lmbot__switch">
                            <input type="checkbox" data-bot-enabled> <span>Bot on</span>
                        </label>
                        <div class="lmbot__grid">
                            <label class="cfg-field">
                                <span class="cfg-field__label">Receiver</span>
                                <select class="cfg-field__input" data-bot-receiver></select>
                            </label>
                            <label class="cfg-field">
                                <span class="cfg-field__label">Bot name (= the receiver's name on the network)</span>
                                <span class="lmbot__row">
                                    <input class="cfg-field__input" type="text" maxlength="32" data-bot-name>
                                    <button class="terminal-button" type="button" data-bot-rename>Change</button>
                                </span>
                            </label>
                        </div>
                        <span class="cfg-field__hint">The receiver puts its own name in front of every channel message,
                            so the bot always answers under that name. Changing it here changes the receiver's name.</span>

                        <div class="cfg-field lmbot__channels">
                            <span class="cfg-field__label">Listens on channels</span>
                            <div data-bot-channels></div>
                            <span class="lmbot__row">
                                <input class="cfg-field__input lmbot__num" type="number" min="0" max="254"
                                       placeholder="0-254" data-bot-channel-add>
                                <button class="terminal-button" type="button" data-bot-channel-add-btn>+ Channel</button>
                            </span>
                        </div>

                        <div class="lmbot__grid lmbot__grid--small">
                            <label class="cfg-field">
                                <span class="cfg-field__label">Prefix</span>
                                <input class="cfg-field__input lmbot__num" type="text" maxlength="1" data-bot-prefix>
                            </label>
                            <label class="cfg-field">
                                <span class="cfg-field__label">Cooldown per member (s)</span>
                                <input class="cfg-field__input lmbot__num" type="number" min="5" max="3600" data-bot-cooldown>
                            </label>
                            <label class="cfg-field">
                                <span class="cfg-field__label">Answers per hour (all)</span>
                                <input class="cfg-field__input lmbot__num" type="number" min="1" max="120" data-bot-hour>
                            </label>
                        </div>
                        <label class="cfg-field lmbot__admins">
                            <span class="cfg-field__label">Admin members (no cooldown, no hourly limit)</span>
                            <input class="cfg-field__input" type="text" placeholder="e.g. 3277263, 535246" data-bot-admins>
                            <span class="cfg-field__hint">Member numbers, separated by commas. Only for questions that
                                came through a repeater (a repeater checked the member key; heard directly, anyone with
                                the channel key could use an admin's number). The receiver's own airtime budget still
                                applies.</span>
                        </label>
                    </article>

                    <article class="cfg-card">
                        <header class="cfg-card__head">
                            <h3 class="cfg-card__title">Commands</h3>
                            <p class="cfg-card__hint" data-bot-builtin></p>
                        </header>
                        <div class="cfg-companions" data-bot-commands></div>
                        <div class="cfg-companions__add-row">
                            <button class="terminal-button" type="button" data-bot-command-add>+ Add command</button>
                        </div>
                        <div class="cfg-card__actions">
                            <button class="terminal-button terminal-button--primary" type="button" data-bot-save>Save</button>
                        </div>
                        <p class="cfg-status" data-bot-status aria-live="polite"></p>
                    </article>

                    <article class="cfg-card">
                        <header class="cfg-card__head">
                            <h3 class="cfg-card__title">Log</h3>
                            <p class="cfg-card__hint">The last questions and what the bot did (newest on top).
                                Ordinary chat is not shown.</p>
                        </header>
                        <div class="lw-table-wrap">
                            <table class="lw-table lmbot__log">
                                <thead><tr><th>Time</th><th>Ch</th><th>From</th><th>Question</th><th>Answer</th><th>Status</th></tr></thead>
                                <tbody data-bot-log></tbody>
                            </table>
                            <p class="lw-empty" data-bot-log-empty>Nothing yet.</p>
                        </div>
                    </article>
                </div>`;
            const q = (s) => el.querySelector(s);
            q('[data-bot-save]').addEventListener('click', () => this._save());
            q('[data-bot-command-add]').addEventListener('click', () => this._addCommandRow());
            q('[data-bot-channel-add-btn]').addEventListener('click', () => this._addChannel());
            q('[data-bot-rename]').addEventListener('click', () => this._rename());
            q('[data-bot-receiver]').addEventListener('change', () => this._renderReceiver());
        }

        show() {
            this._load();
            this._loadLog();
            clearInterval(this._timer);
            this._timer = setInterval(() => this._loadLog(), 5000);
        }

        hide() {
            clearInterval(this._timer);
            this._timer = null;
        }

        // --- data ---

        async _request(method, url, body) {
            const init = { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' } };
            if (body !== undefined) init.body = JSON.stringify(body);
            try {
                const res = await fetch(url, init);
                const data = await res.json().catch(() => ({}));
                if (!res.ok) return { error: data.detail || `HTTP ${res.status}` };
                return { data };
            } catch (e) {
                return { error: e.message };
            }
        }

        async _load() {
            const { data, error } = await this._request('GET', '/api/localmesh-bot/config');
            if (error) { this._status(`Could not load: ${error}`, true); return; }
            this._data = data;
            const c = data.config;
            const q = (s) => this._el.querySelector(s);
            q('[data-bot-enabled]').checked = c.enabled;
            q('[data-bot-prefix]').value = c.prefix;
            q('[data-bot-cooldown]').value = c.cooldown_s;
            q('[data-bot-hour]').value = c.max_per_hour;
            q('[data-bot-admins]').value = (c.admins || []).join(', ');
            const sel = q('[data-bot-receiver]');
            sel.innerHTML = (data.receivers.length ? data.receivers : [{ label: '', name: '' }]).map((r) =>
                `<option value="${esc(r.label)}">${esc(r.label || 'receiver')}${r.name ? ` -- ${esc(r.name)}` : ''}</option>`).join('');
            sel.value = c.receiver || (data.receivers[0] ? data.receivers[0].label : '');
            this._channels = new Set(c.channels);
            q('[data-bot-builtin]').innerHTML = `Built in: ${data.builtin.map((b) => `<code>${esc(c.prefix + b)}</code>`).join(' ')}
                (<code>.trace</code> names the repeaters the question came through). Your own commands below: the
                command without the prefix, and the answer (at most ${data.answer_max} characters).`;
            const list = q('[data-bot-commands]');
            list.innerHTML = '';
            Object.entries(c.commands).forEach(([k, v]) => this._addCommandRow(k, v));
            this._renderReceiver();
        }

        _receiver() {
            const label = this._el.querySelector('[data-bot-receiver]').value;
            return (this._data?.receivers || []).find((r) => r.label === label) || null;
        }

        _renderReceiver() {
            const r = this._receiver();
            this._el.querySelector('[data-bot-name]').value = r ? r.name : '';
            this._renderChannels();
        }

        _renderChannels() {
            const r = this._receiver();
            const known = r ? r.channels : {};
            const ids = new Set([...Object.keys(known).map(Number), ...this._channels]);
            const box = this._el.querySelector('[data-bot-channels]');
            box.innerHTML = [...ids].sort((a, b) => a - b).map((id) => `
                <label class="lmbot__chip">
                    <input type="checkbox" value="${id}" ${this._channels.has(id) ? 'checked' : ''}>
                    ${id}${known[id] ? ` · ${esc(known[id])}` : ' · <em>no key on this receiver</em>'}
                </label>`).join('') || '<span class="cfg-field__hint">This receiver reports no channels yet.</span>';
            box.querySelectorAll('input').forEach((cb) => cb.addEventListener('change', () => {
                const id = Number(cb.value);
                if (cb.checked) this._channels.add(id); else this._channels.delete(id);
            }));
        }

        _addChannel() {
            const input = this._el.querySelector('[data-bot-channel-add]');
            const id = Number(input.value);
            if (input.value === '' || !Number.isInteger(id) || id < 0 || id > 254) {
                this._status('A channel is a number 0-254.', true);
                return;
            }
            this._channels.add(id);
            input.value = '';
            this._renderChannels();
        }

        _addCommandRow(name = '', answer = '') {
            const max = this._data ? this._data.answer_max : 140;
            const div = document.createElement('div');
            div.className = 'cfg-companion';
            div.innerHTML = `
                <div class="cfg-companion__header">
                    <label class="cfg-field" style="flex:0 0 160px">
                        <span class="cfg-field__label">Command</span>
                        <input class="cfg-field__input" type="text" maxlength="16" placeholder="weer"
                               value="${esc(name)}" data-bot-cmd>
                    </label>
                    <label class="cfg-companion__label-wrap">
                        <span class="cfg-field__label">Answer</span>
                        <input class="cfg-field__input" type="text" maxlength="${max}"
                               placeholder="Kijk even naar buiten" value="${esc(answer)}" data-bot-answer>
                    </label>
                    <button class="cfg-companion__remove terminal-button terminal-button--danger" type="button"
                            title="Remove this command">✕</button>
                </div>`;
            div.querySelector('.cfg-companion__remove').addEventListener('click', () => div.remove());
            this._el.querySelector('[data-bot-commands]').appendChild(div);
        }

        _collect() {
            const q = (s) => this._el.querySelector(s);
            const commands = {};
            q('[data-bot-commands]').querySelectorAll('.cfg-companion').forEach((div) => {
                const k = div.querySelector('[data-bot-cmd]').value.trim().toLowerCase().replace(/^[.!/#]/, '');
                const v = div.querySelector('[data-bot-answer]').value.trim();
                if (k || v) commands[k] = v;
            });
            return {
                enabled: q('[data-bot-enabled]').checked,
                receiver: q('[data-bot-receiver]').value,
                channels: [...this._channels].sort((a, b) => a - b),
                prefix: q('[data-bot-prefix]').value || '.',
                commands,
                cooldown_s: Number(q('[data-bot-cooldown]').value) || 20,
                max_per_hour: Number(q('[data-bot-hour]').value) || 30,
                // numbers only; anything else goes to the server as text so it says what is wrong
                admins: q('[data-bot-admins]').value.split(/[\s,;]+/).filter(Boolean)
                    .map((x) => (/^\d+$/.test(x) ? Number(x) : x)),
            };
        }

        async _save() {
            const { error } = await this._request('PUT', '/api/localmesh-bot/config', this._collect());
            if (error) { this._status(`Not saved: ${error}`, true); return; }
            this._status('Saved.');
            this._load();
        }

        async _rename() {
            const name = this._el.querySelector('[data-bot-name]').value.trim();
            const r = this._receiver();
            if (!name) { this._status('The name is empty.', true); return; }
            const { error } = await this._request('PUT', '/api/localmesh/name', { receiver: r ? r.label : null, name });
            if (error) { this._status(`Name not changed: ${error}`, true); return; }
            this._status(`The receiver is now called "${name}"; it announces the new name itself in a few seconds.`);
            this._load();
        }

        async _loadLog() {
            const { data } = await this._request('GET', '/api/localmesh-bot/log');
            if (!Array.isArray(data)) return;
            const rows = data.slice().reverse().map((e) => {
                const t = new Date(e.time * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
                const answer = e.reply ? esc(e.reply) : `<em>${esc(e.why)}</em>`;
                return `<tr><td>${t}</td><td>${esc(e.channel)}</td><td>${esc(e.member)}</td><td>${esc(e.text)}</td>
                        <td>${answer}</td><td>${esc(e.status)}</td></tr>`;
            });
            this._el.querySelector('[data-bot-log]').innerHTML = rows.join('');
            this._el.querySelector('[data-bot-log-empty]').style.display = rows.length ? 'none' : '';
        }

        _status(text, bad = false) {
            const el = this._el.querySelector('[data-bot-status]');
            el.textContent = text;
            el.classList.toggle('cfg-status--error', bad);
        }
    }

    if (typeof window.registerPageHook === 'function') {
        window.registerPageHook({ host: 'localmesh', label: 'Bot', make: () => new LocalMeshBotTab() });
    } else {
        console.warn('LocalMesh Bot: page hook registry missing');
    }
}());
