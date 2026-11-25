/**
 * LocalMesh page: right-side panel for one Members-tab row (open) or Repeaters-tab row (openRepeater).
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
        const token = this._mount({
            color: LocalMeshMemberDrawer.color(m.member), initials: (m.name || String(m.member)).slice(0, 2).toUpperCase(),
            name: m.name || `Member ${m.member}`, idLine: `LocalMesh member ${m.member}`,
            actions: opts.onMessage && !m.blocked ? `<div class="nd-actions">
                <button class="nd-action-btn nd-action-btn--primary" type="button" data-lm-dm>Send direct message</button>
            </div>` : '',
        });
        this._drawer.querySelector('[data-lm-dm]')?.addEventListener('click', () => opts.onMessage(m.member));
        this._render(m, null, null);
        this._fetchHistory(m, token);
    }

    /**
     * @param {object} r -- one row of GET /api/localmesh/repeaters
     */
    openRepeater(r) {
        const token = this._mount({
            color: LocalMeshMemberDrawer.color(r.node), initials: (r.name || `R${r.node}`).slice(0, 2).toUpperCase(),
            name: r.name || `Repeater ${r.node}`, idLine: `LocalMesh repeater ${r.node}`, actions: '',
        });
        this._renderRepeater(r, null);
        this._fetchRepeaterHistory(r, token);
    }

    /** The shared chrome: backdrop, header and an empty body. Returns the token of this opening. */
    _mount({ color, initials, name, idLine, actions }) {
        this.close();
        const token = ++this._token;
        const esc = LocalMeshMemberDrawer.esc;

        const backdrop = document.createElement('div');
        backdrop.className = 'nd-backdrop';
        backdrop.addEventListener('click', () => this.close());

        const drawer = document.createElement('div');
        drawer.className = 'nd-drawer';
        drawer.addEventListener('click', (e) => e.stopPropagation());
        drawer.innerHTML = `
            <div class="nd-header">
                <div class="nd-header__left">
                    <div class="nd-avatar" style="background:${color}">${esc(initials)}</div>
                    <div class="nd-header__info">
                        <div class="nd-header__name">${esc(name)}</div>
                        <div class="nd-header__id">${esc(idLine)}</div>
                    </div>
                </div>
                <button class="nd-close" title="Close">&times;</button>
            </div>
            ${actions}
            <div class="nd-body"></div>
        `;
        drawer.querySelector('.nd-close').addEventListener('click', () => this.close());

        document.body.appendChild(backdrop);
        document.body.appendChild(drawer);
        document.addEventListener('keydown', this._onKey);
        this._backdrop = backdrop;
        this._drawer = drawer;
        requestAnimationFrame(() => {
            backdrop.classList.add('nd-backdrop--visible');
            drawer.classList.add('nd-drawer--open');
        });
        return token;
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

    async _fetchRepeaterHistory(r, token) {
        let activity = [];
        try {
            const res = await fetch('/api/localmesh/activity?limit=300', { credentials: 'same-origin' });
            if (res.ok) activity = await res.json();
        } catch (_) { /* the drawer still shows what the list knew */ }
        if (token !== this._token || !this._drawer) return;
        this._renderRepeater(r, activity.filter((x) => x.member === r.member).slice(0, 15));
    }

    /** activity: null while loading. */
    _renderRepeater(r, activity) {
        const esc = LocalMeshMemberDrawer.esc;
        const P = window.LocalMeshPanel;
        const when = (t) => (t ? `${esc(new Date(t * 1000).toLocaleString())} (${P.ago(t)})` : null);
        const body = this._drawer.querySelector('.nd-body');
        const hadContent = body.children.length > 0;
        const open = new Set([...body.querySelectorAll('.nd-section')]
            .filter((s) => s.querySelector('.nd-section__content').style.display !== 'none')
            .map((s) => s.dataset.title));
        const expanded = (title, dflt) => (hadContent ? open.has(title) : dflt);
        body.innerHTML = '';

        const status = r.revoked ? '<span class="lw-badge lw-badge--undecrypted">revoked</span>'
            : r.listed ? '<span class="lw-badge lw-badge--join">in the server certificates</span>'
            : '<span class="lw-badge lw-badge--rejoin">not in the certificates</span>';
        body.appendChild(LocalMeshMemberDrawer.section('Repeater', [
            ['Name', esc(r.name)],
            ['Node', String(r.node)],
            ['Region', r.region == null ? null : String(r.region)],
            ['Firmware', r.version ? `${esc(r.version)} (announced ${P.ago(r.announced)})` : null],
            ['Status', status],
            ['Heard directly', r.direct ? 'yes (a neighbour of a receiver)' : 'no (through other repeaters)'],
        ], expanded('Repeater', true)));

        body.appendChild(LocalMeshMemberDrawer.section('Last heard', [
            ['When', when(r.last_heard) || 'not heard since Meshpoint started'],
            ['Packet type', esc(r.type)],
            ['Packets', r.count ? String(r.count) : null],
            ['Heard by', esc((r.receivers || []).join(', '))],
        ], expanded('Last heard', true)));

        if (r.status) {
            body.appendChild(LocalMeshMemberDrawer.section('Status', [['Received', when(r.status.time)],
                ...P.statusRows(r.status).map(([k, v]) => [k, v && esc(v)])], expanded('Status', true)));
        }

        body.appendChild(LocalMeshMemberDrawer.section('Signal', r.last_heard ? [
            ['Hops left', r.hops == null ? null : `${r.hops} (counts down by one at every repeater)`],
            ['Previous hop', r.via ? `node ${r.via}` : null],
            ['RSSI', r.rssi == null ? null : `${r.rssi} dBm`],
            ['SNR', r.snr == null ? null : `${r.snr} dB`],
        ] : [], expanded('Signal', true)));

        const loading = [['', '<span class="nd-loading">Loading…</span>']];
        body.appendChild(LocalMeshMemberDrawer.section('Recent packets', activity === null ? loading
            : activity.map((x) => [P.clock(x.time),
                `${esc(x.type)} #${x.counter} · ${x.hops} hops left · `
                + LocalMeshMemberDrawer.decisionBadge(x)]),
        expanded('Recent packets', true)));
    }

    /** messages/activity: null while loading. */
    _render(m, messages, activity) {
        const esc = LocalMeshMemberDrawer.esc;
        const P = window.LocalMeshPanel;
        const when = (t) => (t ? `${esc(new Date(t * 1000).toLocaleString())} (${P.ago(t)})` : null);
        const body = this._drawer.querySelector('.nd-body');
        // Captured once, before clearing -- a bug here previously re-checked
        // body.children.length *after* each section's own appendChild, so
        // only the very first section ("Member") ever saw its real content
        // (that call always ran against an empty body) and every section
        // after it saw a non-empty body built up by its own siblings,
        // making it read `open` (which was correctly empty on a fresh
        // open) instead of its own intended default. Net effect: every
        // section but Member opened collapsed on first show, every time,
        // regardless of each one's own `expanded('X', true)` call below.
        const hadContent = body.children.length > 0;
        const open = new Set([...body.querySelectorAll('.nd-section')]
            .filter((s) => s.querySelector('.nd-section__content').style.display !== 'none')
            .map((s) => s.dataset.title));   // keep what the user opened or closed across the re-render
        const expanded = (title, dflt) => (hadContent ? open.has(title) : dflt);
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
                + LocalMeshMemberDrawer.decisionBadge(x)]),
        expanded('Recent packets', false)));
    }

    /** The decision as the Activity tab and the popup show it (a repeater's own packets: "repeater info"). */
    static decisionBadge(x) {
        const dec = window.LocalMeshPanel.decisionOf(x);
        return `<span class="lw-badge lw-badge--${dec.badge}">${LocalMeshMemberDrawer.esc(dec.label)}</span>`;
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
