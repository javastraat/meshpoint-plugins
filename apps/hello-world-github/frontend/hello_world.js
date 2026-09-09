/**
 * Hello World -- the minimal reference plugin for the "sidebar" seam.
 *
 * Its route/label/category live in plugin.toml's [sidebar] table (parsed by
 * src/plugins/manifest.py, pushed to the browser by
 * src/plugins/assets.py:sidebar_descriptor_tags -- see
 * frontend/sidebar/sidebar_plugin_registry.js for how that becomes an
 * actual sidebar <li> + <section>). This script only supplies the content:
 * a mount()/show()/hide() object, same shape the Listener-tab ("panel")
 * seam already uses.
 *
 * Also the reference HOST for the "hook" seam (frontend/sidebar/
 * page_hook_registry.js): a hook mount point is only added to the page
 * when at least one hook plugin actually targets 'hello-world-github',
 * so this page renders identically to before for anyone who hasn't
 * installed one.
 *
 * NOTE: the `route` below MUST match plugin.toml's [sidebar].route --
 * the manifest builds the sidebar <li>, this call fills its content;
 * a mismatch means the nav item appears but the page stays blank.
 */
window.registerSidebarPage({
    route: 'hello-world-github',
    make: () => ({
        mount(rootEl) {
            const hasHooks = (window.MESHPOINT_PAGE_HOOKS || [])
                .some((h) => h.host === 'hello-world-github');
            rootEl.innerHTML = `
                <div class="plugin-page">
                    <h2>Hello, World.</h2>
                    <p>This page is rendered entirely by a plugin --
                    <code>plugins/apps/hello-world-github/</code>, installed from a
                    plugin source. Its position in the sidebar (under Networks),
                    its icon, and its title all come from <code>plugin.toml</code>'s
                    <code>[sidebar]</code> table, not from anything hardcoded in
                    the dashboard.</p>
                    <span class="hello-world-badge">Styled by hello_world.css</span>
                    ${hasHooks ? '<div class="hello-world-hooks" data-hello-world-hooks></div>' : ''}
                </div>
            `;
            if (hasHooks && typeof window.mountPageHooks === 'function') {
                window.mountPageHooks('hello-world-github', rootEl.querySelector('[data-hello-world-hooks]'));
            }
        },
        show() {},
        hide() {},
    }),
});
