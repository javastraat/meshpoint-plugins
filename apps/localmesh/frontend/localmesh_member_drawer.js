/**
 * LocalMesh page: right-side panel for one Members-tab row.
 *
 * Same look as the Meshtastic/MeshCore node drawer and the Reticulum peer drawer: it renders the core's own
 * node_drawer.css classes (nd-backdrop, nd-drawer, nd-header, nd-section, nd-row) with its own DOM, and never
 * touches the core NodeDrawer class or its #node-drawer element. Collapsible sections like the node drawer; the
 * member's recent messages and packets are fetched when it opens. Closes with ×, Esc or a click beside it.
 */

class LocalMeshMemberDrawer {
    constructor() {
        this._backdrop = null;
        this._drawer = null;
        this._token = 0;
        this._onKey = (e) => { if (e.key === 'Escape') this.close(); };
    }

    /**
     * @param {object} m -- one row of GET /api/localmesh/members
     * @param {{onMessage?: function}} opts -- onMessage(member): a "Send direct message" button (admins)
     */
    open(m, opts = {}) {
        this.close();
        const token = ++this._token;
        const esc = LocalMeshMemberDrawer.esc;

        const backdrop = document.createElement('div');
        backdrop.className = 'nd-backdrop';
        backdrop.addEventListener('click', () => this.close());

        const drawer = document.createElement('div');
        drawer.className = 'nd-drawer';
        drawer.addEventListener('click', (e) => e.stopPropagation());
        const name = m.name || `Member ${m.member}`;
        const initials = (m.name || String(m.member)).slice(0, 2).toUpperCase();
        drawer.innerHTML = `
            <div class="nd-header">
                <div class="nd-header__left">
                    <div class="nd-avatar" style="background:${LocalMeshMemberDrawer.color(m.member)}">${esc(initials)}</div>
                    <div class="nd-header__info">
                        <div class="nd-header__name">${esc(name)}</div>
                        <div class="nd-header__id">LocalMesh member ${m.member}</div>
                    </div>
                </div>
                <button class="nd-close" title="Close">&times;</button>
            </div>
            ${opts.onMessage && !m.blocked ? `<div class="nd-actions">
                <button class="nd-action-btn nd-action-btn--primary" type="button" data-lm-dm>Send direct message</button>
            </div>` : ''}
            <div class="nd-body"></div>
        `;
        drawer.querySelector('.nd-close').addEventListener('click', () => this.close());
        drawer.querySelector('[data-lm-dm]')?.addEventListener('click', () => opts.onMessage(m.member));

        document.body.appendChild(backdrop);
        document.body.appendChild(drawer);
        document.addEventListener('keydown', this._onKey);
        this._backdrop = backdrop;
        this._drawer = drawer;
        requestAnimationFrame(() => {
            backdrop.classList.add('nd-backdrop--visible');
            drawer.classList.add('nd-drawer--open');
        });

        this._render(m, null, null);
        this._fetchHistory(m, token);
    }

    close() {
        document.removeEventListener('keydown', this._onKey);
        if (this._drawer) { this._drawer.remove(); this._drawer = null; }
        if (this._backdrop) { this._backdrop.remove(); this._backdrop = null; }
    }

    async _fetchHistory(m, token) {
        const get = async (url) => {
            try {
                const r = await fetch(url, { credentials: 'same-origin' });
                return r.ok ? await r.json() : [];
            } catch (_) { return []; }
        };
        const [messages, activity] = await Promise.all([
            get('/api/localmesh/messages?limit=300'), get('/api/localmesh/activity?limit=300'),
        ]);
        if (token !== this._token || !this._drawer) return;
        this._render(m, messages.filter((x) => x.member === m.member).slice(0, 10),
            activity.filter((x) => x.member === m.member).slice(0, 15));
    }

    /** messages/activity: null while loading. */
    _render(m, messages, activity) {
        const esc = LocalMeshMemberDrawer.esc;
        const P = window.LocalMeshPanel;
        const when = (t) => (t ? `${esc(new Date(t * 1000).toLocaleString())} (${P.ago(t)})` : null);
        const body = this._drawer.querySelector('.nd-body');
        const open = new Set([...body.querySelectorAll('.nd-section')]
            .filter((s) => s.querySelector('.nd-section__content').style.display !== 'none')
            .map((s) => s.dataset.title));   // keep what the user opened or closed across the re-render
        const expanded = (title, dflt) => (body.children.length ? open.has(title) : dflt);
        body.innerHTML = '';

        const status = m.blocked ? '<span class="lw-badge lw-badge--undecrypted">blocked</span>'
            : '<span class="lw-badge lw-badge--join">member</span>';
        body.appendChild(LocalMeshMemberDrawer.section('Member', [
            ['Name', esc(m.name)],
            ['Member', String(m.member)],
            ['Status', status],
            ['This Meshpoint', m.own ? `yes, receiver ${esc(m.own_receiver || '')}` : null],
            ['National', m.national ? 'yes (may send national floods)' : null],
            ['On member list', m.listed ? 'yes' : 'no (only heard)'],
        ], expanded('Member', true)));

        body.appendChild(LocalMeshMemberDrawer.section('Last heard', [
            ['When', when(m.last_heard) || 'not heard since Meshpoint started'],
            ['Packet type', esc(m.type)],
            ['Packets', m.count ? String(m.count) : null],
            ['Heard by', esc((m.receivers || []).join(', '))],
        ], expanded('Last heard', true)));

        body.appendChild(LocalMeshMemberDrawer.section('Signal', m.last_heard ? [
            ['Hops left', m.hops == null ? null : `${m.hops} (counts down by one at every repeater)`],
            ['Previous hop', m.via ? `node ${m.via}` : null],
            ['RSSI', m.rssi == null ? null : `${m.rssi} dBm`],
            ['SNR', m.snr == null ? null : `${m.snr} dB`],
        ] : [], expanded('Signal', true)));

        body.appendChild(LocalMeshMemberDrawer.section('Position', m.lat == null ? [] : [
            ['Location', `<a href="https://www.openstreetmap.org/?mlat=${m.lat}&mlon=${m.lon}#map=15/${m.lat}/${m.lon}"
                target="_blank" rel="noopener">${m.lat.toFixed(5)}, ${m.lon.toFixed(5)}</a>`],
        ], expanded('Position', m.lat != null)));

        const loading = [['', '<span class="nd-loading">Loading…</span>']];
        body.appendChild(LocalMeshMemberDrawer.section('Recent messages', messages === null ? loading
            : messages.map((x) => [P.clock(x.sort_time), `<span style="white-space: pre-wrap">${esc(x.text)}</span>`]),
        expanded('Recent messages', true)));

        body.appendChild(LocalMeshMemberDrawer.section('Recent packets', activity === null ? loading
            : activity.map((x) => [P.clock(x.time),
                `${esc(x.type)} #${x.counter} · ${x.hops} hops left · `
                + `<span class="lw-badge lw-badge--${LM_DECISION_BADGE[x.decision] || 'rejoin'}">${esc(x.decision.replace(/_/g, ' '))}</span>`]),
        expanded('Recent packets', false)));
    }

    /** One collapsible nd-section of [label, html] rows (empty values dropped), like the core NodeDrawer's. */
    static section(title, rows, expanded) {
        const kept = rows.filter(([, v]) => v != null && v !== '');
        const section = document.createElement('div');
        section.className = 'nd-section';
        section.dataset.title = title;
        const header = document.createElement('div');
        header.className = 'nd-section__header';
        header.innerHTML = `<span class="nd-section__title">${title}</span>
            <span class="nd-section__arrow">${expanded ? '▼' : '▶'}</span>`;
        const content = document.createElement('div');
        content.className = 'nd-section__content';
        content.innerHTML = kept.length
            ? kept.map(([label, value]) => `<div class="nd-row"><span class="nd-row__label">${label}</span>
                <span class="nd-row__value">${value}</span></div>`).join('')
            : '<div class="nd-section__empty">No data available</div>';
        content.style.display = expanded ? '' : 'none';
        header.addEventListener('click', () => {
            const visible = content.style.display !== 'none';
            content.style.display = visible ? 'none' : '';
            header.querySelector('.nd-section__arrow').textContent = visible ? '▶' : '▼';
        });
        section.appendChild(header);
        section.appendChild(content);
        return section;
    }

    /** A steady colour per member number, for the avatar. */
    static color(member) {
        return `hsl(${(Number(member) * 137) % 360}, 55%, 45%)`;
    }

    static esc(s) {
        return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }
}

window.LocalMeshMemberDrawer = LocalMeshMemberDrawer;
