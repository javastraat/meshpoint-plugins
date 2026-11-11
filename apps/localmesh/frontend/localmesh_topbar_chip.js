/**
 * Topbar -- LocalMesh receiver chip(s): one badge per configured receiver, polling GET /api/localmesh/status.
 * Same visual language as the DAPNET chip (reuses the core .topbar-serial classes): brand, lamp, node, battery.
 * Clicking it opens the LocalMesh page.
 * Hidden when no receiver is configured.
 */
class LocalMeshTopbarChip {
    constructor() { this._group = null; this._timer = null; }

    mount(rootEl) { this._group = rootEl; this._group.hidden = true; }

    init() { this._refresh(); this._timer = setInterval(() => this._refresh(), 10_000); }

    destroy() { clearInterval(this._timer); this._timer = null; }

    async _refresh() {
        let devices = [];
        try {
            const res = await fetch('/api/localmesh/status', { credentials: 'same-origin' });
            if (res.ok) devices = await res.json();
        } catch (_e) { /* keep the last badges */ }
        this._paint(Array.isArray(devices) ? devices : []);
    }

    _paint(devices) {
        this._group.hidden = devices.length === 0;
        this._group.textContent = '';
        devices.forEach((dev) => this._group.appendChild(this._badge(dev)));
    }

    _badge(dev) {
        const connected = Boolean(dev.connected);
        const info = dev.info || {}; const st = dev.status || {};
        const root = document.createElement('a');   // a link to the LocalMesh page, like the other pills
        root.className = 'topbar-serial';
        root.href = '#/localmesh';
        const node = info.node != null ? `node ${info.node}` : (dev.label || '----');
        root.title = `LocalMesh receiver ${node}${info.firmware ? ' · firmware ' + info.firmware : ''}`
            + `${st.rx_ok != null ? ' · heard ' + st.rx_ok + ', rejected ' + st.rx_rejected : ''}`;
        root.setAttribute('aria-label', `LocalMesh ${node} ${connected ? 'connected' : 'offline'} -- go to LocalMesh page`);

        const brand = document.createElement('span');
        brand.className = 'topbar-serial__brand';
        brand.textContent = 'LocalMesh';
        root.appendChild(brand);

        const lamp = document.createElement('span');
        lamp.className = `topbar-serial__lamp topbar-serial__lamp--${connected ? 'online' : 'offline'}`;
        lamp.setAttribute('role', 'status');
        const dot = document.createElement('span');
        dot.className = 'topbar-serial__dot';
        dot.setAttribute('aria-hidden', 'true');
        lamp.appendChild(dot);
        root.appendChild(lamp);

        const call = document.createElement('span');
        call.className = 'topbar-serial__call';
        call.textContent = node;
        root.appendChild(call);

        if (st.battery != null) {
            const sep = document.createElement('span');
            sep.className = 'topbar-serial__sep';
            sep.setAttribute('aria-hidden', 'true');
            sep.textContent = '·';
            root.appendChild(sep);
            const bat = document.createElement('span');
            bat.className = 'topbar-serial__freq';
            bat.textContent = `${st.battery} %`;
            root.appendChild(bat);
        }
        root.classList.toggle('topbar-serial--offline', !connected);
        return root;
    }
}

window.registerTopbarChip({ id: 'localmesh', make: () => new LocalMeshTopbarChip() });
