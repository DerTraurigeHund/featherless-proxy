// Featherless Proxy — Admin Dashboard (v3, sidebar shell)
// Requires common.js (toasts, modals, fetch helpers, formatters, chartOpts).

let currentHours = 24;
let currentBucket = 60;
let currentView = 'overview';
let charts = {};
let usersCache = [];
let modelsCache = [];
let statsTimer = null, systemTimer = null;

const VIEWS = {
    overview: 'Overview', system: 'Live System', users: 'Users',
    keys: 'API Keys', models: 'Models', logs: 'Request Logs', tx: 'Transactions',
};

/* ============================ View routing ============================ */

function showView(name) {
    currentView = name;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
    setText('page-title', VIEWS[name] || '');
    document.getElementById('chart-tools').style.visibility = (name === 'overview') ? 'visible' : 'hidden';
    closeSidebar();
    refreshActive();
}
function refreshActive() {
    ({
        overview: loadOverview, system: loadSystem, users: loadUsers,
        keys: loadKeys, models: loadModels, logs: loadLogs, tx: loadTransactions,
    }[currentView] || (() => {}))();
}

/* ============================ Overview ============================ */

async function loadOverview() {
    currentHours = readChartHours();
    currentBucket = readChartBucket(currentHours);
    let stats, timeseries;
    try {
        [stats, timeseries] = await Promise.all([
            fetchJSON(`/admin/api/stats?hours=${currentHours}`),
            fetchJSON(`/admin/api/timeseries?hours=${currentHours}&bucket_minutes=${currentBucket}`),
        ]);
    } catch (e) {
        if (String(e).includes('401')) location.href = '/login';
        return;
    }
    const o = stats.overall || {};
    setText('t-cost', fmtCost(o.total_cost));
    setText('t-requests', fmtNum(o.total_requests));
    setText('t-hitrate', (o.total_requests > 0 ? ((o.cache_hits / o.total_requests) * 100).toFixed(1) : '0') + '%');
    setText('t-input', fmtNum(o.total_input_tokens));
    setText('t-cached', fmtNum(o.total_cached_read_tokens));
    setText('t-output', fmtNum(o.total_output_tokens));

    const labels = (timeseries || []).map(t => fmtBucket(t.bucket, currentHours, currentBucket));
    updateChart('cost', labels, [(timeseries || []).map(t => Number(t.cost || 0))]);
    updateChart('tokens', labels, [
        (timeseries || []).map(t => Number(t.input_tokens || 0)),
        (timeseries || []).map(t => Number(t.cached_read_tokens || 0)),
        (timeseries || []).map(t => Number(t.output_tokens || 0)),
    ]);
    updateChart('requests', labels, [
        (timeseries || []).map(t => Number(t.requests || 0)),
        (timeseries || []).map(t => Number(t.cache_hits || 0)),
    ]);

    setHTML('model-stats-tbody', (stats.per_model || []).length === 0 ? emptyRow(7) :
        stats.per_model.map(m => `<tr><td>${escapeHtml(m.model)}</td><td class="num">${fmtNum(m.requests)}</td>
            <td class="num">${fmtNum(m.input_tokens)}</td><td class="num">${fmtNum(m.cached_read_tokens)}</td>
            <td class="num">${fmtNum(m.output_tokens)}</td><td class="num">${fmtNum(m.cache_hits)}</td>
            <td class="cost">${fmtCost(m.cost)}</td></tr>`).join(''));
    setHTML('key-stats-tbody', (stats.per_key || []).length === 0 ? emptyRow(6) :
        stats.per_key.map(k => `<tr><td>${escapeHtml(k.api_key_name || 'unknown')}</td><td class="num">${fmtNum(k.requests)}</td>
            <td class="num">${fmtNum(k.input_tokens)}</td><td class="num">${fmtNum(k.cached_read_tokens)}</td>
            <td class="num">${fmtNum(k.output_tokens)}</td><td class="cost">${fmtCost(k.cost)}</td></tr>`).join(''));
}

function initCharts() {
    charts.cost = new Chart(document.getElementById('chart-cost'), {
        type: 'line', options: chartOpts(),
        data: { labels: [], datasets: [{ label: 'Cost ($)', data: [], borderColor: '#34d399', backgroundColor: 'rgba(52,211,153,0.12)', fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 }] },
    });
    charts.tokens = new Chart(document.getElementById('chart-tokens'), {
        type: 'line', options: chartOpts(),
        data: { labels: [], datasets: [
            { label: 'Input', data: [], borderColor: '#4f8cff', backgroundColor: 'rgba(79,140,255,0.10)', fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 },
            { label: 'Cached', data: [], borderColor: '#fbbf24', backgroundColor: 'transparent', tension: 0.35, pointRadius: 0, borderWidth: 2 },
            { label: 'Output', data: [], borderColor: '#2dd4bf', backgroundColor: 'transparent', tension: 0.35, pointRadius: 0, borderWidth: 2 },
        ] },
    });
    charts.requests = new Chart(document.getElementById('chart-requests'), {
        type: 'bar', options: chartOpts(),
        data: { labels: [], datasets: [
            { label: 'Requests', data: [], backgroundColor: '#4f8cff', borderRadius: 4, maxBarThickness: 22 },
            { label: 'Cache Hits', data: [], backgroundColor: '#34d399', borderRadius: 4, maxBarThickness: 22 },
        ] },
    });
}
function updateChart(name, labels, datasets) {
    const ch = charts[name];
    if (!ch) return;
    ch.data.labels = labels;
    datasets.forEach((d, i) => { if (ch.data.datasets[i]) ch.data.datasets[i].data = d; });
    ch.update('none');
}

/* ============================ Live system ============================ */

async function loadSystem() {
    let sys;
    try { sys = await fetchJSON('/admin/api/system'); } catch { return; }
    const q = sys.queue || {}, c = sys.cache || {};
    const used = q.used_connections || 0, max = q.max_connections || 1;
    const pct = Math.round((used / max) * 100);
    const ring = document.getElementById('conn-gauge');
    if (ring) {
        ring.style.setProperty('--val', pct);
        ring.style.setProperty('--col', pct >= 80 ? 'var(--red)' : pct >= 50 ? 'var(--amber)' : 'var(--green)');
        setText('conn-gauge-val', `${used}/${max}`);
    }
    setText('sys-free', q.free_connections ?? 0);
    setText('sys-queue', q.queue_size ?? 0);
    setText('sys-oldest', (q.oldest_wait_seconds ?? 0) + 's');
    setText('sys-peak', `${q.peak_used ?? 0}/${max}`);
    setText('sys-processed', fmtNum(q.total_processed));
    setText('sys-timeout', fmtNum(q.total_timeout));
    setText('sys-aborted', fmtNum(q.total_aborted));
    setText('sys-avgwait', (q.avg_wait_ms ?? 0) + ' ms');
    setText('cache-active', fmtNum(c.active_chunks));
    setText('cache-models', fmtNum(c.models_cached));
    setText('cache-hits', fmtNum(c.total_chunk_hits));
    setText('cache-ttl', (c.ttl_seconds ?? 0) + 's');

    const ws = q.waiting || [];
    setHTML('waiters-tbody', ws.length === 0 ? '<tr><td colspan="4" class="empty">Queue empty — slots available</td></tr>' :
        ws.map(w => `<tr>
            <td>${w.priority === 1 ? '<span class="badge badge-prio-1">P1</span>' : '<span class="badge badge-prio-2">P2</span>'}</td>
            <td>${escapeHtml(w.model || '-')}</td><td class="num">${w.cost}</td><td class="num">${w.wait_seconds}s</td></tr>`).join(''));
}

/* ============================ Logs ============================ */

async function loadLogs() {
    let logs;
    try { logs = await fetchJSON('/admin/api/logs?limit=80'); } catch { return; }
    setHTML('logs-tbody', (logs || []).length === 0 ? emptyRow(9) :
        logs.map(l => `<tr><td class="muted">${fmtTime(l.timestamp)}</td><td>${escapeHtml(l.api_key_name || '-')}</td>
            <td>${escapeHtml(l.model)}</td>
            <td>${l.priority === 1 ? '<span class="badge badge-prio-1">P1</span>' : '<span class="badge badge-prio-2">P2</span>'}</td>
            <td class="num">${fmtNum(l.input_tokens)}</td><td class="num">${fmtNum(l.cached_read_tokens)}</td>
            <td class="num">${fmtNum(l.output_tokens)}</td>
            <td>${l.cache_hit ? '<span class="badge badge-hit">HIT</span>' : '<span class="badge badge-miss">MISS</span>'}</td>
            <td class="cost">${fmtCost6(l.cost)}</td></tr>`).join(''));
}

/* ============================ Users ============================ */

async function loadUsers() {
    usersCache = await fetchJSON('/admin/api/users');
    setHTML('users-tbody', usersCache.length === 0 ? emptyRow(7) :
        usersCache.map(u => `<tr>
            <td><strong>${escapeHtml(u.username)}</strong></td>
            <td>${u.is_admin ? '<span class="badge badge-admin">Admin</span>' : '<span class="badge badge-user">User</span>'}</td>
            <td><span style="color:${u.credits < 1 ? 'var(--red)' : 'var(--green)'};font-weight:600">$${Number(u.credits).toFixed(2)}</span></td>
            <td>${u.credit_limit > 0 ? '$' + u.credit_limit : '∞'}</td>
            <td>${u.enabled ? '<span class="badge badge-on">enabled</span>' : '<span class="badge badge-off">disabled</span>'}</td>
            <td class="muted">${fmtDate(u.created_at)}</td>
            <td><div class="row"><button class="sm" onclick="showTopupModal(${u.id})">+ Credits</button>
                <button class="sm ghost" onclick="showEditUserModal(${u.id})">Edit</button>
                <button class="sm danger" onclick="deleteUser(${u.id})">Delete</button></div></td></tr>`).join(''));
}
function showCreateUserModal() {
    const o = openModal(`<h3>Create user</h3>
        <div class="field"><label>Username</label><input id="nu-username" placeholder="e.g. max_mustermann"></div>
        <div class="field"><label>Password</label><input id="nu-password" type="password"></div>
        <div class="field-grid"><div class="field"><label>Initial credits ($)</label><input id="nu-credits" type="number" step="0.01" value="0"></div>
        <div class="field"><label>Credit limit ($, 0=∞)</label><input id="nu-limit" type="number" step="0.01" value="0"></div></div>
        <div class="check"><input type="checkbox" id="nu-admin"><label>Admin account (bypasses credit checks, P1)</label></div>
        <div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="createUser()">Create</button></div>`);
    o.querySelector('#nu-username').focus();
}
async function createUser() {
    const r = await postJSON('/admin/api/users/create', {
        username: val('nu-username'), password: val('nu-password'),
        credits: parseFloat(val('nu-credits') || '0'), credit_limit: parseFloat(val('nu-limit') || '0'),
        is_admin: document.getElementById('nu-admin').checked ? 1 : 0,
    });
    if (r.error || r.detail) return toast('error', 'Could not create user', r.error || r.detail);
    closeModal(); toast('success', 'User created', r.username); loadUsers();
}
function showEditUserModal(id) {
    const u = usersCache.find(x => x.id === id); if (!u) return;
    openModal(`<h3>Edit — ${escapeHtml(u.username)}</h3>
        <div class="field-grid"><div class="field"><label>Credits ($)</label><input id="eu-credits" type="number" step="0.01" value="${u.credits}"></div>
        <div class="field"><label>Credit limit ($, 0=∞)</label><input id="eu-limit" type="number" step="0.01" value="${u.credit_limit}"></div></div>
        <div class="field"><label>New password (blank = keep)</label><input id="eu-password" type="password" placeholder="••••••"></div>
        <div class="check"><input type="checkbox" id="eu-admin" ${u.is_admin ? 'checked' : ''}><label>Admin account</label></div>
        <div class="check"><input type="checkbox" id="eu-enabled" ${u.enabled ? 'checked' : ''}><label>Account enabled</label></div>
        <div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="updateUser(${id})">Save</button></div>`);
}
async function updateUser(id) {
    const body = { credits: parseFloat(val('eu-credits') || '0'), credit_limit: parseFloat(val('eu-limit') || '0'),
        is_admin: document.getElementById('eu-admin').checked ? 1 : 0, enabled: document.getElementById('eu-enabled').checked ? 1 : 0 };
    const pw = val('eu-password'); if (pw) body.password = pw;
    const r = await postJSON(`/admin/api/users/${id}/update`, body);
    if (r.error || r.detail) return toast('error', 'Update failed', r.error || r.detail);
    closeModal(); toast('success', 'User updated'); loadUsers();
}
async function deleteUser(id) {
    const u = usersCache.find(x => x.id === id);
    if (!await confirmDialog(`Delete "${u ? u.username : id}" and all their API keys?`, { okLabel: 'Delete user' })) return;
    await deleteJSON(`/admin/api/users/${id}`); toast('success', 'User deleted'); loadUsers();
}
function showTopupModal(id) {
    const u = usersCache.find(x => x.id === id);
    openModal(`<h3>Add credits${u ? ' — ' + escapeHtml(u.username) : ''}</h3>
        <div class="field"><label>Amount ($)</label><input id="tc-amount" type="number" step="0.01" value="10"></div>
        <div class="field"><label>Reason</label><input id="tc-reason" value="admin_topup"></div>
        <div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="addCredits(${id})">Add</button></div>`);
}
async function addCredits(id) {
    const r = await postJSON(`/admin/api/users/${id}/credits`, { amount: parseFloat(val('tc-amount') || '0'), reason: val('tc-reason') });
    if (r.error || r.detail) return toast('error', 'Failed', r.error || r.detail);
    closeModal(); toast('success', 'Credits added', '$' + Number(r.balance).toFixed(2)); loadUsers();
}

/* ============================ API keys ============================ */

async function loadKeys() {
    if (!usersCache.length) usersCache = await fetchJSON('/admin/api/users');
    const keys = await fetchJSON('/admin/api/keys');
    setHTML('keys-tbody', keys.length === 0 ? emptyRow(5) :
        keys.map(k => `<tr><td>${escapeHtml(k.name)}</td>
            <td><span class="copy-chip" data-copy="${escapeHtml(k.key)}">${escapeHtml(k.key.substring(0, 16))}… 📋</span></td>
            <td>${k.username ? escapeHtml(k.username) : '<span class="muted">— system key</span>'}</td>
            <td>${k.priority === 1 ? '<span class="badge badge-prio-1">P1 · Immediate</span>' : '<span class="badge badge-prio-2">P2 · Queue</span>'}</td>
            <td><button class="sm danger" onclick="deleteKey(${k.id})">Delete</button></td></tr>`).join(''));
}
function showCreateKeyModal() {
    const opts = (usersCache || []).map(u => `<option value="${u.id}">${escapeHtml(u.username)} (${u.is_admin ? 'Admin' : 'User'}, $${Number(u.credits).toFixed(2)})</option>`).join('');
    const o = openModal(`<h3>Create API key</h3>
        <div class="field"><label>Key name</label><input id="nk-name" placeholder="e.g. Production server"></div>
        <div class="field"><label>Owner</label><select id="nk-user"><option value="">No owner (system key, P1)</option>${opts}</select></div>
        <div class="field"><label>Priority</label><select id="nk-priority"><option value="1">P1 · Immediate</option><option value="2" selected>P2 · Queue</option></select></div>
        <div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="createKey()">Create</button></div>`);
    o.querySelector('#nk-user').addEventListener('change', e => { document.getElementById('nk-priority').value = e.target.value === '' ? '1' : '2'; });
    o.querySelector('#nk-name').focus();
}
async function createKey() {
    const uv = val('nk-user');
    const r = await postJSON('/admin/api/keys/create', { name: val('nk-name'), user_id: uv ? parseInt(uv) : null, priority: parseInt(val('nk-priority')) });
    if (r.error || r.detail) return toast('error', 'Failed', r.error || r.detail);
    showKeyResult(r.key); loadKeys();
}
function showKeyResult(key) {
    openModal(`<h3>API key created</h3><p class="muted" style="margin-bottom:12px">Copy it now — it can't be shown again.</p>
        <pre class="code" style="white-space:pre-wrap;word-break:break-all">${escapeHtml(key)}</pre>
        <div class="modal-actions"><button class="ghost" data-copy="${escapeHtml(key)}">📋 Copy</button><button onclick="closeModal()">Done</button></div>`);
}
async function deleteKey(id) {
    if (!await confirmDialog('Delete this API key? Clients using it will stop working.', { okLabel: 'Delete key' })) return;
    await deleteJSON(`/admin/api/keys/${id}`); toast('success', 'API key deleted'); loadKeys();
}

/* ============================ Models ============================ */

async function loadModels() {
    modelsCache = await fetchJSON('/admin/api/models');
    setHTML('models-tbody', modelsCache.length === 0 ? emptyRow(7) :
        modelsCache.map(m => `<tr>
            <td><span class="copy-chip" data-copy="${escapeHtml(m.model_id)}">${escapeHtml(m.model_id)}</span></td>
            <td>${escapeHtml(m.display_name)}</td><td><span class="badge badge-admin">${m.concurrent_cost}</span></td>
            <td class="num">$${m.input_price}</td><td class="num">$${m.cached_read_price}</td><td class="num">$${m.output_price}</td>
            <td><div class="row"><button class="sm ghost" onclick="showEditModelModal(${m.id})">Edit</button><button class="sm danger" onclick="deleteModel(${m.id})">Delete</button></div></td></tr>`).join(''));
}
function modelFields(m = {}) {
    return `<div class="field"><label>Model ID (used in API calls)</label><input id="m-id" placeholder="featherless/zai-org/GLM-5.2" value="${escapeHtml(m.model_id || '')}" ${m.id ? 'disabled' : ''}></div>
        <div class="field"><label>Display name</label><input id="m-name" placeholder="GLM-5.2" value="${escapeHtml(m.display_name || '')}"></div>
        <div class="field"><label>Concurrent connection cost (slots)</label><input id="m-cost" type="number" value="${m.concurrent_cost ?? 1}"></div>
        <div class="field-grid"><div class="field"><label>Input $/1M</label><input id="m-ip" type="number" step="0.01" value="${m.input_price ?? 0}"></div>
        <div class="field"><label>Cached read $/1M</label><input id="m-crp" type="number" step="0.01" value="${m.cached_read_price ?? 0}"></div></div>
        <div class="field"><label>Output $/1M</label><input id="m-op" type="number" step="0.01" value="${m.output_price ?? 0}"></div>`;
}
function showCreateModelModal() {
    openModal(`<h3>Add model</h3>${modelFields()}<div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="createModel()">Add</button></div>`);
}
function showEditModelModal(id) {
    const m = modelsCache.find(x => x.id === id); if (!m) return;
    openModal(`<h3>Edit — ${escapeHtml(m.display_name)}</h3>${modelFields(m)}<div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="updateModel(${id})">Save</button></div>`);
}
function collectModel() {
    return { model_id: val('m-id'), display_name: val('m-name'), concurrent_cost: parseInt(val('m-cost') || '1'),
        input_price: parseFloat(val('m-ip') || '0'), cached_read_price: parseFloat(val('m-crp') || '0'), output_price: parseFloat(val('m-op') || '0') };
}
async function createModel() {
    const r = await postJSON('/admin/api/models/create', collectModel());
    if (r.error || r.detail) return toast('error', 'Failed', r.error || r.detail);
    closeModal(); toast('success', 'Model added'); loadModels();
}
async function updateModel(id) {
    const b = collectModel(); delete b.model_id;
    const r = await postJSON(`/admin/api/models/${id}/update`, b);
    if (r.error || r.detail) return toast('error', 'Failed', r.error || r.detail);
    closeModal(); toast('success', 'Model updated'); loadModels();
}
async function deleteModel(id) {
    if (!await confirmDialog('Delete this model configuration?', { okLabel: 'Delete model' })) return;
    await deleteJSON(`/admin/api/models/${id}`); toast('success', 'Model deleted'); loadModels();
}

/* ============================ Transactions ============================ */

async function loadTransactions() {
    const tx = await fetchJSON('/admin/api/transactions?limit=100');
    setHTML('tx-tbody', (tx || []).length === 0 ? emptyRow(6) :
        tx.map(t => `<tr><td class="muted">${fmtTime(t.timestamp)}</td><td>User #${t.user_id}</td>
            <td>${escapeHtml(t.reason)}</td><td>${escapeHtml(t.model || '-')}</td>
            <td style="color:${t.amount < 0 ? 'var(--red)' : 'var(--green)'}">${t.amount < 0 ? '' : '+'}${Number(t.amount).toFixed(4)}</td>
            <td>$${Number(t.balance_after).toFixed(2)}</td></tr>`).join(''));
}

/* ============================ Helpers ============================ */

function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function setHTML(id, v) { const el = document.getElementById(id); if (el) el.innerHTML = v; }
function val(id) { const el = document.getElementById(id); return el ? el.value : ''; }
function emptyRow(c) { return `<tr><td colspan="${c}" class="empty">No data</td></tr>`; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function bindChartControls() {
    ['cc-unit', 'cc-interval'].forEach(id => document.getElementById(id).addEventListener('change', loadOverview));
    document.getElementById('cc-range').addEventListener('input', debounce(loadOverview, 350));
}
function toggleSidebar() { document.querySelector('.sidebar').classList.toggle('open'); document.querySelector('.scrim').classList.toggle('show'); }
function closeSidebar() { document.querySelector('.sidebar')?.classList.remove('open'); document.querySelector('.scrim')?.classList.remove('show'); }

function tile(cls, label, id, foot = '') {
    return `<div class="tile ${cls}"><div class="label"><span class="dot"></span>${label}</div><div class="value" id="${id}">—</div>${foot ? `<div class="foot">${foot}</div>` : ''}</div>`;
}
function tableCard(title, btn, head, bodyId) {
    return `<div class="table-card"><div class="toolbar"><h2>${title}</h2><div class="spacer" style="flex:1"></div>${btn}</div>
        <div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody id="${bodyId}"></tbody></table></div></div>`;
}

/* ============================ Init ============================ */

document.addEventListener('DOMContentLoaded', async () => {
    let session;
    try { session = await fetchJSON('/api/session'); } catch { location.href = '/login'; return; }
    if (!session.authenticated || !session.is_admin) { location.href = '/login'; return; }

    const navItem = (v, ico, label) => `<button class="nav-item${v === 'overview' ? ' active' : ''}" data-view="${v}" onclick="showView('${v}')"><span class="ico">${ico}</span>${label}</button>`;

    document.getElementById('app').innerHTML = `
    <div class="scrim" onclick="closeSidebar()"></div>
    <div class="app-shell">
        <aside class="sidebar">
            <div class="sidebar-brand"><span class="logo">🪶</span><span class="brand-gradient">Featherless</span><span class="sidebar-role">Admin</span></div>
            <nav class="sidebar-nav">
                ${navItem('overview', '📊', 'Overview')}
                ${navItem('system', '⚙️', 'Live System')}
                <div class="nav-section">Manage</div>
                ${navItem('users', '👥', 'Users')}
                ${navItem('keys', '🔑', 'API Keys')}
                ${navItem('models', '📦', 'Models')}
                <div class="nav-section">Records</div>
                ${navItem('logs', '📋', 'Request Logs')}
                ${navItem('tx', '💰', 'Transactions')}
            </nav>
            <div class="sidebar-foot">
                <div class="avatar">${escapeHtml((session.username || 'A')[0].toUpperCase())}</div>
                <div class="who"><b>${escapeHtml(session.username || 'admin')}</b><span>Administrator</span></div>
                <button class="icon-btn ghost sm" title="Logout" onclick="logout()">⏻</button>
            </div>
        </aside>
        <div class="main">
            <header class="topbar">
                <button class="menu-btn ghost sm" onclick="toggleSidebar()">☰</button>
                <div><h1 id="page-title">Overview</h1></div>
                <div class="spacer"></div>
                <div id="chart-tools">${chartControlsHTML()}</div>
                <span class="live-badge"><span class="live-dot"></span>Live</span>
            </header>
            <div class="content">
                <!-- Overview -->
                <section class="view active" id="view-overview">
                    <div class="grid tiles">
                        ${tile('green', 'Total Cost', 't-cost')}
                        ${tile('blue', 'Requests', 't-requests')}
                        ${tile('teal', 'Cache Hit Rate', 't-hitrate')}
                        ${tile('', 'Input Tokens', 't-input')}
                        ${tile('amber', 'Cached Read', 't-cached')}
                        ${tile('', 'Output Tokens', 't-output')}
                    </div>
                    <div class="grid cols-2 mt">
                        <div class="chart-card"><h2>Cost over time</h2><div class="chart-box"><canvas id="chart-cost"></canvas></div></div>
                        <div class="chart-card"><h2>Token usage</h2><div class="chart-box"><canvas id="chart-tokens"></canvas></div></div>
                    </div>
                    <div class="chart-card mt"><h2>Requests &amp; cache hits</h2><div class="chart-box"><canvas id="chart-requests"></canvas></div></div>
                    <div class="grid cols-2 mt">
                        ${tableCard('Per-model', '', '<th>Model</th><th>Req</th><th>Input</th><th>Cached</th><th>Output</th><th>Hits</th><th>Cost</th>', 'model-stats-tbody')}
                        ${tableCard('Per-key', '', '<th>Key</th><th>Req</th><th>Input</th><th>Cached</th><th>Output</th><th>Cost</th>', 'key-stats-tbody')}
                    </div>
                </section>

                <!-- System -->
                <section class="view" id="view-system">
                    <div class="grid cols-2">
                        <div class="card">
                            <h2 style="margin-bottom:16px">Connections</h2>
                            <div class="gauge">
                                <div class="gauge-ring" id="conn-gauge"><div class="gauge-inner"><b id="conn-gauge-val">0/0</b><span>conns</span></div></div>
                                <div class="kv">
                                    <div class="between"><span>Free slots</span><b id="sys-free">0</b></div>
                                    <div class="between"><span>Queued</span><b id="sys-queue">0</b></div>
                                    <div class="between"><span>Oldest wait</span><b id="sys-oldest">0s</b></div>
                                    <div class="between"><span>Peak used</span><b id="sys-peak">0/0</b></div>
                                </div>
                            </div>
                            <div class="grid cols-4 mt">
                                ${tile('', 'Processed', 'sys-processed')}
                                ${tile('amber', 'Timeouts', 'sys-timeout')}
                                ${tile('red', 'Aborted', 'sys-aborted')}
                                ${tile('', 'Avg wait', 'sys-avgwait')}
                            </div>
                        </div>
                        <div class="card">
                            <h2 style="margin-bottom:14px">Queue</h2>
                            <div class="table-wrap" style="max-height:220px;overflow-y:auto"><table><thead><tr><th>Prio</th><th>Model</th><th>Cost</th><th>Wait</th></tr></thead><tbody id="waiters-tbody"></tbody></table></div>
                            <h2 style="margin:20px 0 12px">Cache</h2>
                            <div class="grid cols-4">
                                ${tile('', 'Active chunks', 'cache-active')}
                                ${tile('blue', 'Models', 'cache-models')}
                                ${tile('green', 'Chunk hits', 'cache-hits')}
                                ${tile('', 'TTL', 'cache-ttl')}
                            </div>
                        </div>
                    </div>
                </section>

                <section class="view" id="view-users">${tableCard('Users', '<button onclick="showCreateUserModal()">➕ Create user</button>', '<th>Username</th><th>Role</th><th>Credits</th><th>Limit</th><th>Status</th><th>Created</th><th>Actions</th>', 'users-tbody')}</section>
                <section class="view" id="view-keys">${tableCard('API keys', '<button onclick="showCreateKeyModal()">➕ Create key</button>', '<th>Name</th><th>Key</th><th>Owner</th><th>Priority</th><th>Actions</th>', 'keys-tbody')}</section>
                <section class="view" id="view-models">${tableCard('Models', '<button onclick="showCreateModelModal()">➕ Add model</button>', '<th>Model ID</th><th>Name</th><th>Conn</th><th>Input $/1M</th><th>Cached $/1M</th><th>Output $/1M</th><th>Actions</th>', 'models-tbody')}</section>
                <section class="view" id="view-logs">${tableCard('Recent requests', '', '<th>Time</th><th>Key</th><th>Model</th><th>Prio</th><th>Input</th><th>Cached</th><th>Output</th><th>Cache</th><th>Cost</th>', 'logs-tbody')}</section>
                <section class="view" id="view-tx">${tableCard('Credit transactions', '', '<th>Time</th><th>User</th><th>Reason</th><th>Model</th><th>Amount</th><th>Balance</th>', 'tx-tbody')}</section>
            </div>
        </div>
    </div>`;

    initCharts();
    bindChartControls();
    loadOverview();
    statsTimer = setInterval(() => { if (currentView === 'overview') loadOverview(); }, 9000);
    systemTimer = setInterval(() => { if (currentView === 'system') loadSystem(); }, 3000);
});
