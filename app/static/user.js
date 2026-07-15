// Featherless Proxy — User Dashboard (v3, sidebar shell)
// Requires common.js (toasts, modals, fetch helpers, formatters, chartOpts).

let currentHours = 24;
let currentBucket = 60;
let currentView = 'overview';
let charts = {};
let userInfo = null;
let statsTimer = null;
let lbHours = 24;
let lbSortField = 'cost';
let lbSortDir = 'desc';
let lbData = [];
let cmpUsers = new Set();
let cmpAllUsers = [];
let cmpInitialized = false;
let cmpCharts = {};
const CMP_COLORS = ['#4f8cff', '#34d399', '#fbbf24', '#f87171', '#a78bfa', '#2dd4bf', '#fb923c', '#f472b6', '#93c5fd', '#c084fc'];

const VIEWS = { overview: 'Overview', leaderboard: 'Leaderboard', compare: 'Vergleich', keys: 'API Keys', models: 'Models', usage: 'How to use', account: 'Account' };

async function loadMe() {
    try { userInfo = await fetchJSON('/user/api/me'); return true; }
    catch { location.href = '/login'; return false; }
}

function showView(name) {
    currentView = name;
    document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.view === name));
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${name}`));
    setText('page-title', VIEWS[name] || '');
    document.getElementById('chart-tools').style.visibility = (name === 'overview' || name === 'compare') ? 'visible' : 'hidden';
    closeSidebar();
    if (name === 'models') loadUserModels();
    if (name === 'overview') loadStats();
    if (name === 'leaderboard') loadLeaderboard();
    if (name === 'compare') loadCompareView();
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
    } catch (e) {
        if (String(e).includes('401')) location.href = '/login';
        else console.error('Failed to load dashboard stats:', e);
        return;
    }

    // Queue/system info is best-effort: a missing endpoint (e.g. server not
    // restarted after deploy) must not hide the rest of the dashboard.
    let system = {};
    try { system = await fetchJSON('/user/api/system'); } catch (e) {
        console.warn('Queue status unavailable:', e);
    }
    try { userInfo = await fetchJSON('/user/api/me'); renderCredits(); } catch (e) {
        if (String(e).includes('401')) { location.href = '/login'; return; }
        console.warn('Could not refresh user info:', e);
    }

    const q = system || {};
    const free = q.free_connections ?? 0, max = q.max_connections || 1;
    const used = q.used_connections || 0;
    setText('user-free-slots', `${free}/${max}`);
    const qsEl = document.getElementById('user-queue-status');
    if (qsEl) qsEl.textContent = q.queue_size ? `${q.queue_size} waiting` : (used >= max ? 'full' : 'ready');

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

/* ---------- Leaderboard ---------- */
async function loadLeaderboard() {
    try {
        const q = lbHours ? `?hours=${lbHours}` : '';
        const res = await fetchJSON(`/user/api/leaderboard${q}`);
        lbData = res.rows || [];
        renderLeaderboard();
    } catch (e) { if (String(e).includes('401')) location.href = '/login'; }
}

function renderLeaderboard() {
    const dir = lbSortDir === 'asc' ? 1 : -1;
    const sorted = [...lbData].sort((a, b) => {
        if (lbSortField === 'username') return dir * a.username.localeCompare(b.username);
        const val = (r) => lbSortField === 'cache_rate' ? (r.requests ? r.cache_hits / r.requests : 0) : Number(r[lbSortField] || 0);
        return dir * (val(a) - val(b));
    });
    const meId = userInfo ? userInfo.id : null;
    const medal = i => i === 0 ? '🥇 ' : i === 1 ? '🥈 ' : i === 2 ? '🥉 ' : '';

    setHTML('lb-tbody', sorted.length === 0 ? emptyRow(6) :
        sorted.map((r, i) => `<tr class="${r.user_id === meId ? 'lb-me' : ''}">
            <td class="num">${medal(i)}${i + 1}</td>
            <td>${escapeHtml(r.username)}${r.user_id === meId ? ' <span class="badge badge-user">Du</span>' : ''}</td>
            <td class="num">${fmtNum(r.requests)}</td>
            <td class="num">${fmtNum(r.total_tokens)}</td>
            <td class="cost">${fmtCost(r.cost)}</td>
            <td class="num">${r.requests > 0 ? ((r.cache_hits / r.requests) * 100).toFixed(0) : 0}%</td>
        </tr>`).join(''));

    ['username', 'requests', 'total_tokens', 'cost', 'cache_rate'].forEach(f => {
        const el = document.getElementById('lb-arrow-' + f);
        if (el) el.textContent = f === lbSortField ? (lbSortDir === 'asc' ? '▲' : '▼') : '';
    });

    const meIdx = sorted.findIndex(r => r.user_id === meId);
    const meRow = meIdx >= 0 ? sorted[meIdx] : null;
    setHTML('lb-me-card', meRow ? `
        <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:18px">
            <div><div class="label muted" style="font-size:0.74rem;font-weight:600;letter-spacing:0.03em">DEIN RANG</div>
            <div class="value" style="font-size:2.2rem;font-weight:750">#${meIdx + 1}<span class="muted" style="font-size:1rem;font-weight:500"> von ${sorted.length}</span></div></div>
            <div class="grid cols-3" style="flex:1;min-width:280px">
                ${tile('blue', 'Requests', 'lb-me-requests')}
                ${tile('', 'Tokens', 'lb-me-tokens')}
                ${tile('green', 'Kosten', 'lb-me-cost')}
            </div>
        </div>` : `<p class="muted">Noch keine Nutzung erfasst — stelle eine Anfrage, um im Leaderboard zu erscheinen.</p>`);
    if (meRow) {
        setText('lb-me-requests', fmtNum(meRow.requests));
        setText('lb-me-tokens', fmtNum(meRow.total_tokens));
        setText('lb-me-cost', fmtCost(meRow.cost));
    }
}

function setLbSort(field) {
    if (lbSortField === field) {
        lbSortDir = lbSortDir === 'asc' ? 'desc' : 'asc';
    } else {
        lbSortField = field;
        lbSortDir = field === 'username' ? 'asc' : 'desc';
    }
    renderLeaderboard();
}

function bindLeaderboardControls() {
    document.querySelectorAll('#lb-range button').forEach(b => b.addEventListener('click', () => {
        document.querySelectorAll('#lb-range button').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        lbHours = b.dataset.hours;
        loadLeaderboard();
    }));
}

/* ---------- Vergleich (compare users) ---------- */
async function loadCompareView() {
    if (!cmpInitialized) {
        cmpInitialized = true;
        try { cmpAllUsers = await fetchJSON('/user/api/users'); } catch { cmpAllUsers = []; }
        if (userInfo) cmpUsers.add(userInfo.id);
        renderUserPicker();
    }
    loadCompare();
}

function renderUserPicker() {
    setHTML('cmp-user-picker', (cmpAllUsers || []).map(u => `
        <button class="chip-toggle${cmpUsers.has(u.id) ? ' active' : ''}" onclick="toggleCmpUser(${u.id})">
            ${escapeHtml(u.username)}${userInfo && u.id === userInfo.id ? ' (Du)' : ''}
        </button>`).join('') || '<span class="muted">Keine Nutzer gefunden</span>');
    setHTML('cmp-count', `${cmpUsers.size} <span class="muted" style="font-size:1rem;font-weight:500">${cmpUsers.size === 1 ? 'Nutzer ausgewählt' : 'Nutzer ausgewählt'}</span>`);
}

function toggleCmpUser(id) {
    if (cmpUsers.has(id)) cmpUsers.delete(id); else cmpUsers.add(id);
    renderUserPicker();
    loadCompare();
}

function clearCmpUsers() {
    cmpUsers.clear();
    if (userInfo) cmpUsers.add(userInfo.id);
    renderUserPicker();
    loadCompare();
}

async function loadCompare() {
    const ids = [...cmpUsers];
    const empty = document.getElementById('cmp-empty');
    const content = document.getElementById('cmp-content');
    if (ids.length === 0) {
        if (empty) empty.style.display = 'block';
        if (content) content.style.display = 'none';
        return;
    }
    if (empty) empty.style.display = 'none';
    if (content) content.style.display = 'block';

    // Same shared range/interval controls as the Overview chart-tools.
    const hours = readChartHours();
    const bucket = readChartBucket(hours);
    let res, trend;
    try {
        [res, trend] = await Promise.all([
            fetchJSON(`/user/api/compare?user_ids=${ids.join(',')}&hours=${hours}`),
            fetchJSON(`/user/api/compare/timeseries?user_ids=${ids.join(',')}&hours=${hours}&bucket_minutes=${bucket}`),
        ]);
    } catch (e) { if (String(e).includes('401')) location.href = '/login'; return; }
    renderCompare(res);
    renderCompareTrend(trend, hours, bucket);
}

function renderCompare(res) {
    const totals = res.totals || [];
    const byModel = res.by_model || [];
    const colorFor = i => CMP_COLORS[i % CMP_COLORS.length];

    // Tiles mirror the Overview tiles 1:1, but each one lists every selected
    // user's value for that metric instead of a single number.
    const metricRows = (fmt) => totals.length === 0 ? '<div class="muted" style="font-size:0.8rem">Keine Daten</div>' :
        totals.map((t, i) => `<div class="cmp-tile-row">
            <span class="cmp-tile-dot" style="background:${colorFor(i)}"></span>
            <span class="cmp-tile-name">${escapeHtml(t.username)}</span>
            <span class="cmp-tile-val">${fmt(t)}</span>
        </div>`).join('');

    setHTML('cmp-t-cost', metricRows(t => fmtCost(t.cost)));
    setHTML('cmp-t-requests', metricRows(t => fmtNum(t.requests)));
    setHTML('cmp-t-hitrate', metricRows(t => (t.requests > 0 ? ((t.cache_hits / t.requests) * 100).toFixed(1) : '0') + '%'));
    setHTML('cmp-t-input', metricRows(t => fmtNum(t.input_tokens)));
    setHTML('cmp-t-cached', metricRows(t => fmtNum(t.cached_read_tokens)));
    setHTML('cmp-t-output', metricRows(t => fmtNum(t.output_tokens)));

    setHTML('cmp-model-tbody', byModel.length === 0 ? emptyRow(7) :
        byModel.map(r => `<tr>
            <td>${escapeHtml(r.username)}</td>
            <td>${escapeHtml(r.model)}</td>
            <td class="num">${fmtNum(r.requests)}</td>
            <td class="num">${fmtNum(r.input_tokens)}</td>
            <td class="num">${fmtNum(r.cached_read_tokens)}</td>
            <td class="num">${fmtNum(r.output_tokens)}</td>
            <td class="cost">${fmtCost(r.cost)}</td>
        </tr>`).join(''));

    setHTML('cmp-tbody', totals.length === 0 ? emptyRow(7) :
        totals.map(t => `<tr class="${userInfo && t.user_id === userInfo.id ? 'lb-me' : ''}">
            <td>${escapeHtml(t.username)}${userInfo && t.user_id === userInfo.id ? ' <span class="badge badge-user">Du</span>' : ''}</td>
            <td class="num">${fmtNum(t.requests)}</td>
            <td class="num">${fmtNum(t.input_tokens)}</td>
            <td class="num">${fmtNum(t.cached_read_tokens)}</td>
            <td class="num">${fmtNum(t.output_tokens)}</td>
            <td class="cost">${fmtCost(t.cost)}</td>
            <td class="num">${t.requests > 0 ? ((t.cache_hits / t.requests) * 100).toFixed(0) : 0}%</td>
        </tr>`).join(''));
}

function renderCompareTrend(trend, hours, bucketMin) {
    const buckets = trend.buckets || [];
    const labels = buckets.map(b => fmtBucket(b, hours, bucketMin));
    const seriesByUser = trend.series || {};
    const colorFor = i => CMP_COLORS[i % CMP_COLORS.length];
    const ids = [...cmpUsers];
    const nameFor = (id) => (cmpAllUsers.find(u => u.id === id) || {}).username || ('#' + id);

    // Same two charts as the Overview ("Cost over time" / "Token usage"),
    // just with one line per selected user instead of one line for "me".
    const costDatasets = ids.map((id, i) => {
        const rows = seriesByUser[String(id)] || [];
        const color = colorFor(i);
        return { label: nameFor(id), data: rows.map(r => Number(r.cost || 0)), borderColor: color, backgroundColor: hexToRgba(color, 0.08), fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 };
    });
    // Token usage is shown per type (Input / Cached / Output), not summed —
    // one line per user per type, same color per user, distinct dash pattern
    // per token type so both the user and the token type stay readable.
    const TOKEN_TYPES = [
        { key: 'input_tokens', label: 'Input', dash: [] },
        { key: 'cached_read_tokens', label: 'Cached', dash: [6, 4] },
        { key: 'output_tokens', label: 'Output', dash: [2, 3] },
    ];
    const tokenDatasets = [];
    ids.forEach((id, i) => {
        const rows = seriesByUser[String(id)] || [];
        const color = colorFor(i);
        const name = nameFor(id);
        TOKEN_TYPES.forEach(tt => {
            tokenDatasets.push({
                label: `${name} · ${tt.label}`,
                data: rows.map(r => Number(r[tt.key] || 0)),
                borderColor: color, backgroundColor: 'transparent', borderDash: tt.dash,
                tension: 0.35, pointRadius: 0, borderWidth: 2,
            });
        });
    });
    updateCmpChart('cost', labels, costDatasets);
    updateCmpChart('tokens', labels, tokenDatasets);
}

function initCmpCharts() {
    cmpCharts.cost = new Chart(document.getElementById('chart-cmp-cost'), {
        type: 'line', options: chartOpts(),
        data: { labels: [], datasets: [] },
    });
    cmpCharts.tokens = new Chart(document.getElementById('chart-cmp-tokens'), {
        type: 'line', options: chartOpts(),
        data: { labels: [], datasets: [] },
    });
}
function updateCmpChart(name, labels, datasets) {
    const ch = cmpCharts[name]; if (!ch) return;
    ch.data.labels = labels;
    ch.data.datasets = datasets;
    ch.update('none');
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
    const refresh = () => { if (currentView === 'compare') loadCompare(); else loadStats(); };
    ['cc-unit', 'cc-interval'].forEach(id => document.getElementById(id).addEventListener('change', refresh));
    document.getElementById('cc-range').addEventListener('input', debounce(refresh, 350));
}
function toggleSidebar() { document.querySelector('.sidebar').classList.toggle('open'); document.querySelector('.scrim').classList.toggle('show'); }
function closeSidebar() { document.querySelector('.sidebar')?.classList.remove('open'); document.querySelector('.scrim')?.classList.remove('show'); }
function tile(cls, label, id) { return `<div class="tile ${cls}"><div class="label"><span class="dot"></span>${label}</div><div class="value" id="${id}">—</div></div>`; }
function compareTile(cls, label, id) { return `<div class="tile ${cls}"><div class="label"><span class="dot"></span>${label}</div><div class="cmp-tile-list" id="${id}"></div></div>`; }
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
                ${navItem('leaderboard', '🏆', 'Leaderboard')}
                ${navItem('compare', '⚖️', 'Vergleich')}
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
                    <div class="card" style="margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px">
                        <div><div class="label" style="color:var(--muted);font-size:0.74rem;font-weight:600">YOUR CREDITS</div>
                        <div id="user-credits" class="value credit-value" style="font-size:2.4rem;font-weight:750">$0.00</div>
                        <div class="foot muted" style="font-size:0.78rem">Deducted per request based on token usage</div></div>
                        <div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">
                            <div style="text-align:right">
                                <div class="label" style="color:var(--muted);font-size:0.74rem;font-weight:600">FREE SLOTS</div>
                                <div id="user-free-slots" class="value" style="font-size:2.2rem;font-weight:750">—</div>
                                <div id="user-queue-status" class="foot muted" style="font-size:0.78rem">—</div>
                            </div>
                            <button onclick="showView('keys')">🔑 Manage API keys</button>
                        </div>
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

                <section class="view" id="view-leaderboard">
                    <div class="card" style="margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
                        <div class="seg" id="lb-range">
                            <button data-hours="24" class="active">24h</button>
                            <button data-hours="168">7d</button>
                            <button data-hours="720">30d</button>
                            <button data-hours="">All-time</button>
                        </div>
                        <div class="muted" style="font-size:0.78rem">Klick auf eine Spalte, um danach zu sortieren</div>
                    </div>
                    <div class="card" id="lb-me-card" style="margin-bottom:16px"></div>
                    ${tableCard('Leaderboard', '', `
                        <th>#</th>
                        <th class="sortable" onclick="setLbSort('username')">User <span id="lb-arrow-username" class="sort-arrow"></span></th>
                        <th class="sortable num" onclick="setLbSort('requests')">Requests <span id="lb-arrow-requests" class="sort-arrow"></span></th>
                        <th class="sortable num" onclick="setLbSort('total_tokens')">Tokens <span id="lb-arrow-total_tokens" class="sort-arrow"></span></th>
                        <th class="sortable num" onclick="setLbSort('cost')">Kosten <span id="lb-arrow-cost" class="sort-arrow"></span></th>
                        <th class="sortable num" onclick="setLbSort('cache_rate')">Cache-Hit <span id="lb-arrow-cache_rate" class="sort-arrow"></span></th>
                    `, 'lb-tbody')}
                </section>
                <section class="view" id="view-compare">
                    <div class="card" style="margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
                        <div style="flex:1;min-width:240px">
                            <div class="label" style="color:var(--muted);font-size:0.74rem;font-weight:600">VERGLEICHE</div>
                            <div id="cmp-count" class="value" style="font-size:2.4rem;font-weight:750">0 <span class="muted" style="font-size:1rem;font-weight:500">Nutzer ausgewählt</span></div>
                            <div class="foot muted" style="font-size:0.78rem">Klicke auf einen Nutzer, um ihn hinzuzufügen oder zu entfernen</div>
                            <div id="cmp-user-picker" class="chip-picker mt"></div>
                        </div>
                        <button class="ghost" onclick="clearCmpUsers()">↺ Zurücksetzen</button>
                    </div>
                    <div id="cmp-empty" class="card muted" style="text-align:center;padding:40px;display:none">Wähle mindestens einen Nutzer aus, um den Vergleich zu sehen.</div>
                    <div id="cmp-content">
                        <div class="grid tiles">
                            ${compareTile('green', 'Total Cost', 'cmp-t-cost')}
                            ${compareTile('blue', 'Requests', 'cmp-t-requests')}
                            ${compareTile('teal', 'Cache Hit Rate', 'cmp-t-hitrate')}
                            ${compareTile('', 'Input Tokens', 'cmp-t-input')}
                            ${compareTile('amber', 'Cached Read', 'cmp-t-cached')}
                            ${compareTile('', 'Output Tokens', 'cmp-t-output')}
                        </div>
                        <div class="grid cols-2 mt">
                            <div class="chart-card"><h2>Kosten im Verlauf</h2><div class="chart-box"><canvas id="chart-cmp-cost"></canvas></div></div>
                            <div class="chart-card"><h2>Token-Nutzung im Verlauf</h2><div class="chart-box"><canvas id="chart-cmp-tokens"></canvas></div></div>
                        </div>
                        <div class="section-title">Modell-Vergleich</div>
                        ${tableCard('Nach Modell', '', '<th>User</th><th>Modell</th><th>Requests</th><th>Input</th><th>Cached</th><th>Output</th><th>Kosten</th>', 'cmp-model-tbody')}
                        <div class="section-title">Zusammenfassung</div>
                        ${tableCard('Vergleich', '', '<th>User</th><th>Requests</th><th>Input</th><th>Cached</th><th>Output</th><th>Kosten</th><th>Cache-Hit</th>', 'cmp-tbody')}
                    </div>
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
    initCmpCharts();
    bindChartControls();
    bindLeaderboardControls();
    loadStats();
    statsTimer = setInterval(() => {
        if (currentView === 'overview') loadStats();
        else if (currentView === 'compare') loadCompare();
    }, 10000);
});
