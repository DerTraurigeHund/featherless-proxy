// Featherless Proxy — User Dashboard (v3, sidebar shell)
// Requires common.js (toasts, modals, fetch helpers, formatters, chartOpts).

let currentHours = 24;
let currentBucket = 60;
let currentView = 'overview';
let charts = {};
let userInfo = null;
let statsTimer = null;

const VIEWS = { overview: 'Overview', keys: 'API Keys', models: 'Models', usage: 'How to use', account: 'Account' };

async function loadMe() {
    try { userInfo = await fetchJSON('/user/api/me'); return true; }
    catch { location.href = '/login'; return false; }
}

function showView(name) {
    currentView = name;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
    setText('page-title', VIEWS[name] || '');
    document.getElementById('chart-tools').style.visibility = (name === 'overview') ? 'visible' : 'hidden';
    closeSidebar();
    if (name === 'models') loadUserModels();
    if (name === 'overview') loadStats();
}

function renderCredits() {
    if (!userInfo) return;
    const c = document.getElementById('user-credits');
    if (c) { c.textContent = '$' + Number(userInfo.credits).toFixed(2); c.className = 'value credit-value' + (userInfo.credits < 1 ? ' credit-low' : ''); }
}

async function loadStats() {
    currentHours = readChartHours();
    currentBucket = readChartBucket(currentHours);
    let stats, timeseries, logs;
    try {
        [stats, timeseries, logs] = await Promise.all([
            fetchJSON(`/user/api/stats?hours=${currentHours}`),
            fetchJSON(`/user/api/timeseries?hours=${currentHours}&bucket_minutes=${currentBucket}`),
            fetchJSON('/user/api/logs?limit=60'),
        ]);
        userInfo = await fetchJSON('/user/api/me'); renderCredits();
    } catch (e) { if (String(e).includes('401')) location.href = '/login'; return; }

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

    setHTML('logs-tbody', (logs || []).length === 0 ? emptyRow(7) :
        logs.map(l => `<tr><td class="muted">${fmtTime(l.timestamp)}</td><td>${escapeHtml(l.model)}</td>
            <td class="num">${fmtNum(l.input_tokens)}</td><td class="num">${fmtNum(l.cached_read_tokens)}</td>
            <td class="num">${fmtNum(l.output_tokens)}</td>
            <td>${l.cache_hit ? '<span class="badge badge-hit">HIT</span>' : '<span class="badge badge-miss">MISS</span>'}</td>
            <td class="cost">${fmtCost6(l.cost)}</td></tr>`).join(''));

    loadKeys(); loadTx();
}

async function loadKeys() {
    const keys = await fetchJSON('/user/api/keys');
    setHTML('keys-tbody', (keys || []).length === 0 ? '<tr><td colspan="4" class="empty">No API keys yet — create one above</td></tr>' :
        keys.map(k => `<tr><td>${escapeHtml(k.name)}</td>
            <td><span class="copy-chip" data-copy="${escapeHtml(k.key)}">${escapeHtml(k.key.substring(0, 16))}… 📋</span></td>
            <td>${k.enabled ? '<span class="badge badge-on">enabled</span>' : '<span class="badge badge-off">disabled</span>'}</td>
            <td><button class="sm danger" onclick="deleteMyKey(${k.id})">Delete</button></td></tr>`).join(''));
}
async function loadTx() {
    const tx = await fetchJSON('/user/api/transactions?limit=25');
    setHTML('tx-tbody', (tx || []).length === 0 ? emptyRow(4) :
        tx.map(t => `<tr><td class="muted">${fmtTime(t.timestamp)}</td><td>${escapeHtml(t.reason)}</td>
            <td style="color:${t.amount < 0 ? 'var(--red)' : 'var(--green)'}">${t.amount < 0 ? '' : '+'}${Number(t.amount).toFixed(4)}</td>
            <td>$${Number(t.balance_after).toFixed(2)}</td></tr>`).join(''));
}
async function loadUserModels() {
    try {
        const models = await fetchJSON('/user/api/models');
        setHTML('umodels-tbody', (models || []).length === 0 ? emptyRow(4) :
            models.map(m => `<tr><td><strong>${escapeHtml(m.display_name)}</strong><br>
                <span class="copy-chip" data-copy="${escapeHtml(m.model_id)}">${escapeHtml(m.model_id)} 📋</span></td>
                <td class="num">$${m.input_price}</td><td class="num">$${m.cached_read_price}</td><td class="num">$${m.output_price}</td></tr>`).join(''));
    } catch { /* ignore */ }
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
}
function updateChart(name, labels, datasets) {
    const ch = charts[name]; if (!ch) return;
    ch.data.labels = labels;
    datasets.forEach((d, i) => { if (ch.data.datasets[i]) ch.data.datasets[i].data = d; });
    ch.update('none');
}

/* ---------- API keys ---------- */
function showCreateKeyModal() {
    const o = openModal(`<h3>Create API key</h3>
        <div class="field"><label>Key name</label><input id="uk-name" placeholder="e.g. My App"></div>
        <div class="modal-actions"><button class="subtle" onclick="closeModal()">Cancel</button><button onclick="createMyKey()">Create</button></div>`);
    o.querySelector('#uk-name').focus();
}
async function createMyKey() {
    const name = val('uk-name'); if (!name) return toast('error', 'Name required');
    const r = await postJSON('/user/api/keys/create', { name });
    if (r.error || r.detail) return toast('error', 'Failed', r.error || r.detail);
    openModal(`<h3>API key created</h3><p class="muted" style="margin-bottom:12px">Copy it now — it can't be shown again.</p>
        <pre class="code" style="white-space:pre-wrap;word-break:break-all">${escapeHtml(r.key)}</pre>
        <div class="modal-actions"><button class="ghost" data-copy="${escapeHtml(r.key)}">📋 Copy</button><button onclick="closeModal()">Done</button></div>`);
    loadKeys();
}
async function deleteMyKey(id) {
    if (!await confirmDialog('Delete this API key?', { okLabel: 'Delete key' })) return;
    await deleteJSON(`/user/api/keys/${id}`); toast('success', 'API key deleted'); loadKeys();
}

/* ---------- Password ---------- */
async function changePassword() {
    const current = val('pw-current'), newPw = val('pw-new'), confirmPw = val('pw-confirm');
    if (newPw !== confirmPw) return toast('error', 'Passwords do not match');
    if (newPw.length < 4) return toast('error', 'Password too short', 'Minimum 4 characters');
    const r = await postJSON('/user/api/password', { current_password: current, new_password: newPw });
    if (r.ok) { toast('success', 'Password changed'); ['pw-current', 'pw-new', 'pw-confirm'].forEach(id => document.getElementById(id).value = ''); }
    else toast('error', 'Failed', r.detail || r.error || 'Could not change password');
}

/* ---------- Helpers ---------- */
function setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
function setHTML(id, v) { const el = document.getElementById(id); if (el) el.innerHTML = v; }
function val(id) { const el = document.getElementById(id); return el ? el.value : ''; }
function emptyRow(c) { return `<tr><td colspan="${c}" class="empty">No data</td></tr>`; }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
function bindChartControls() {
    ['cc-unit', 'cc-interval'].forEach(id => document.getElementById(id).addEventListener('change', loadStats));
    document.getElementById('cc-range').addEventListener('input', debounce(loadStats, 350));
}
function toggleSidebar() { document.querySelector('.sidebar').classList.toggle('open'); document.querySelector('.scrim').classList.toggle('show'); }
function closeSidebar() { document.querySelector('.sidebar')?.classList.remove('open'); document.querySelector('.scrim')?.classList.remove('show'); }
function tile(cls, label, id) { return `<div class="tile ${cls}"><div class="label"><span class="dot"></span>${label}</div><div class="value" id="${id}">—</div></div>`; }
function tableCard(title, btn, head, bodyId) {
    return `<div class="table-card"><div class="toolbar"><h2>${title}</h2><div style="flex:1"></div>${btn}</div>
        <div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody id="${bodyId}"></tbody></table></div></div>`;
}

/* ---------- Init ---------- */
document.addEventListener('DOMContentLoaded', async () => {
    if (!await loadMe()) return;

    const snippet = `from openai import OpenAI

client = OpenAI(
    base_url="${location.origin}/v1",
    api_key="fp_your_key_here",
)

resp = client.chat.completions.create(
    model="featherless/zai-org/GLM-5.2",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)`;

    const navItem = (v, ico, label) => `<button class="nav-item${v === 'overview' ? ' active' : ''}" data-view="${v}" onclick="showView('${v}')"><span class="ico">${ico}</span>${label}</button>`;

    document.getElementById('app').innerHTML = `
    <div class="scrim" onclick="closeSidebar()"></div>
    <div class="app-shell">
        <aside class="sidebar">
            <div class="sidebar-brand"><span class="logo">🪶</span><span class="brand-gradient">Featherless</span></div>
            <nav class="sidebar-nav">
                ${navItem('overview', '📊', 'Overview')}
                ${navItem('keys', '🔑', 'API Keys')}
                ${navItem('models', '📦', 'Models')}
                ${navItem('usage', '🚀', 'How to use')}
                ${navItem('account', '⚙️', 'Account')}
            </nav>
            <div class="sidebar-foot">
                <div class="avatar">${escapeHtml((userInfo.username || 'U')[0].toUpperCase())}</div>
                <div class="who"><b>${escapeHtml(userInfo.username || 'user')}</b><span>${userInfo.is_admin ? 'Administrator' : 'User'}</span></div>
                <button class="ghost sm" title="Logout" onclick="logout()">⏻</button>
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
                <section class="view active" id="view-overview">
                    <div class="card" style="margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
                        <div><div class="label" style="color:var(--muted);font-size:0.74rem;font-weight:600">YOUR CREDITS</div>
                        <div id="user-credits" class="value credit-value" style="font-size:2.4rem;font-weight:750">$0.00</div>
                        <div class="foot muted" style="font-size:0.78rem">Deducted per request based on token usage</div></div>
                        <button onclick="showView('keys')">🔑 Manage API keys</button>
                    </div>
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
                    <div class="section-title">Recent requests</div>
                    ${tableCard('My requests', '', '<th>Time</th><th>Model</th><th>Input</th><th>Cached</th><th>Output</th><th>Cache</th><th>Cost</th>', 'logs-tbody')}
                    <div class="section-title">Credit transactions</div>
                    ${tableCard('Transactions', '', '<th>Time</th><th>Reason</th><th>Amount</th><th>Balance</th>', 'tx-tbody')}
                </section>

                <section class="view" id="view-keys">${tableCard('My API keys', '<button onclick="showCreateKeyModal()">➕ Create key</button>', '<th>Name</th><th>Key</th><th>Status</th><th>Actions</th>', 'keys-tbody')}</section>
                <section class="view" id="view-models">${tableCard('Available models', '', '<th>Model</th><th>Input $/1M</th><th>Cached $/1M</th><th>Output $/1M</th>', 'umodels-tbody')}</section>
                <section class="view" id="view-usage">
                    <div class="card">
                        <h2 style="margin-bottom:10px">OpenAI-compatible endpoint</h2>
                        <p class="muted" style="margin-bottom:14px">Point any OpenAI client at <span class="copy-chip" data-copy="${location.origin}/v1">${location.origin}/v1 📋</span> and use one of your API keys.</p>
                        <pre class="code">${escapeHtml(snippet)}</pre>
                        <div class="row mt"><button class="ghost" data-copy="${escapeHtml(snippet)}">📋 Copy snippet</button></div>
                    </div>
                </section>
                <section class="view" id="view-account">
                    <div class="card" style="max-width:440px">
                        <h2 style="margin-bottom:14px">Change password</h2>
                        <div class="field"><label>Current password</label><input id="pw-current" type="password"></div>
                        <div class="field"><label>New password</label><input id="pw-new" type="password"></div>
                        <div class="field"><label>Confirm new password</label><input id="pw-confirm" type="password"></div>
                        <button onclick="changePassword()">Change password</button>
                    </div>
                </section>
            </div>
        </div>
    </div>`;

    renderCredits();
    initCharts();
    bindChartControls();
    loadStats();
    statsTimer = setInterval(() => { if (currentView === 'overview') loadStats(); }, 10000);
});
