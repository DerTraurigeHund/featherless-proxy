// Featherless Proxy — shared frontend utilities
// Toasts, modals, clipboard, fetch helpers and formatters used by both
// the admin and user dashboards.

/* ---------- HTTP ---------- */
async function fetchJSON(url) {
    const resp = await fetch(url, { credentials: 'same-origin' });
    if (!resp.ok) throw new Error(`API error: ${resp.status}`);
    return resp.json();
}
async function postJSON(url, data) {
    const resp = await fetch(url, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data || {}),
    });
    const out = await resp.json().catch(() => ({}));
    if (!resp.ok && !out.error && !out.detail) out.error = `HTTP ${resp.status}`;
    return out;
}
async function deleteJSON(url) {
    const resp = await fetch(url, { method: 'DELETE', credentials: 'same-origin' });
    return resp.json().catch(() => ({}));
}

/* ---------- Formatters ---------- */
const fmtNum = (n) => Number(n || 0).toLocaleString('de-DE');
const fmtCost = (n) => '$' + Number(n || 0).toFixed(4);
const fmtCost6 = (n) => '$' + Number(n || 0).toFixed(6);
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/* ---------- Toasts ---------- */
function ensureToastContainer() {
    let c = document.getElementById('toast-container');
    if (!c) {
        c = document.createElement('div');
        c.id = 'toast-container';
        c.className = 'toast-container';
        document.body.appendChild(c);
    }
    return c;
}
function toast(type, title, msg = '', ttl = 4200) {
    const icons = { success: '✓', error: '✕', info: 'ℹ' };
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `<div class="toast-icon">${icons[type] || 'ℹ'}</div>
        <div class="toast-body"><div class="toast-title">${escapeHtml(title)}</div>
        ${msg ? `<div class="toast-msg">${escapeHtml(msg)}</div>` : ''}</div>`;
    ensureToastContainer().appendChild(el);
    const remove = () => { el.classList.add('fade-out'); setTimeout(() => el.remove(), 250); };
    el.addEventListener('click', remove);
    if (ttl) setTimeout(remove, ttl);
}

/* ---------- Clipboard ---------- */
async function copyText(text, label = 'Copied to clipboard') {
    try {
        await navigator.clipboard.writeText(text);
        toast('success', label);
    } catch {
        // Fallback for non-secure contexts.
        const ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        try { document.execCommand('copy'); toast('success', label); }
        catch { toast('error', 'Copy failed', text); }
        ta.remove();
    }
}
// Delegated copy: any element with data-copy="..." copies its value on click.
document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-copy]');
    if (el) copyText(el.getAttribute('data-copy'));
});

/* ---------- Modal ---------- */
function openModal(innerHtml) {
    closeModal();
    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.innerHTML = `<div class="modal">${innerHtml}</div>`;
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) closeModal(); });
    document.addEventListener('keydown', escClose);
    document.body.appendChild(overlay);
    return overlay;
}
function closeModal() {
    const o = document.querySelector('.modal-overlay');
    if (o) o.remove();
    document.removeEventListener('keydown', escClose);
}
function escClose(e) { if (e.key === 'Escape') closeModal(); }

// Promise-based confirm dialog (replaces window.confirm).
function confirmDialog(message, { title = 'Please confirm', okLabel = 'Confirm', danger = true } = {}) {
    return new Promise((resolve) => {
        const overlay = openModal(`
            <h3>${escapeHtml(title)}</h3>
            <p class="muted" style="margin-bottom:4px">${escapeHtml(message)}</p>
            <div class="modal-actions">
                <button class="subtle" id="cd-cancel">Cancel</button>
                <button class="${danger ? 'danger' : ''}" id="cd-ok">${escapeHtml(okLabel)}</button>
            </div>`);
        overlay.querySelector('#cd-cancel').onclick = () => { closeModal(); resolve(false); };
        overlay.querySelector('#cd-ok').onclick = () => { closeModal(); resolve(true); };
    });
}

/* ---------- Auth ---------- */
async function logout() {
    await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' });
    window.location.href = '/login';
}

/* ---------- Charts ---------- */

// Bucket-aware x-axis labels: show time for sub-day buckets, dates for the rest,
// scaled to the overall range so long ranges stay readable.
function fmtBucket(ts, hours, bucketMin) {
    const d = new Date(ts * 1000);
    const t = { hour: '2-digit', minute: '2-digit' };
    const sub = !bucketMin || bucketMin < 1440; // sub-day granularity → include time
    if (hours <= 24) return d.toLocaleTimeString('de-DE', t);
    if (hours <= 168) return sub
        ? d.toLocaleString('de-DE', { weekday: 'short', ...t })
        : d.toLocaleDateString('de-DE', { weekday: 'short', day: '2-digit', month: '2-digit' });
    return sub
        ? d.toLocaleString('de-DE', { day: '2-digit', month: '2-digit', ...t })
        : d.toLocaleDateString('de-DE', { day: '2-digit', month: 'short' });
}

// Choose a sensible bucket (minutes) for a range targeting ~45 points.
const BUCKET_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440, 2880, 10080];
function autoBucket(hours) {
    const target = (hours * 60) / 45;
    return BUCKET_STEPS.find(s => s >= target) || 10080;
}
// Keep the number of points sane regardless of what the user picks.
function clampBucket(hours, bucket) {
    bucket = Math.max(1, Math.round(bucket));
    const max = 1500;
    if ((hours * 60) / bucket > max) return Math.ceil((hours * 60) / max);
    return bucket;
}

// --- Dynamic chart range/interval controls (shared by both dashboards) ---
function chartControlsHTML() {
    return `<div class="chart-controls">
        <div class="ctrl"><span>Range</span>
            <input id="cc-range" type="number" min="1" step="1" value="24">
            <select id="cc-unit"><option value="0.016666667">min</option><option value="1" selected>hours</option><option value="24">days</option></select>
        </div>
        <div class="ctrl"><span>Interval</span>
            <select id="cc-interval">
                <option value="auto" selected>Auto</option>
                <option value="1">1 min</option><option value="5">5 min</option>
                <option value="15">15 min</option><option value="30">30 min</option>
                <option value="60">1 h</option><option value="120">2 h</option>
                <option value="360">6 h</option><option value="720">12 h</option>
                <option value="1440">1 day</option>
            </select>
        </div>
    </div>`;
}
function readChartHours() {
    const v = parseFloat(document.getElementById('cc-range')?.value) || 24;
    const mult = parseFloat(document.getElementById('cc-unit')?.value) || 1;
    return Math.max(0.05, v * mult);
}
function readChartBucket(hours) {
    const sel = document.getElementById('cc-interval')?.value || 'auto';
    const raw = sel === 'auto' ? autoBucket(hours) : parseInt(sel);
    return clampBucket(hours, raw);
}

function chartOpts(extra = {}) {
    const { scales: exScales, plugins: exPlugins, ...rest } = extra;
    const base = {
        responsive: true, maintainAspectRatio: false,
        interaction: { intersect: false, mode: 'index' },
        animation: { duration: 250 },
        scales: {
            x: {
                ticks: { color: '#7d8597', font: { size: 10 }, maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
                grid: { display: false },
                border: { color: 'rgba(255,255,255,0.06)' },
            },
            y: {
                ticks: { color: '#7d8597', font: { size: 10 }, maxTicksLimit: 5, padding: 6 },
                grid: { color: 'rgba(255,255,255,0.05)' },
                border: { display: false },
                beginAtZero: true,
            },
        },
        plugins: {
            legend: { labels: { color: '#c4cad6', usePointStyle: true, pointStyle: 'circle', boxWidth: 7, padding: 14, font: { size: 11 } } },
            tooltip: {
                backgroundColor: '#0b0e14', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
                titleColor: '#e8ebf2', bodyColor: '#c4cad6', padding: 10, cornerRadius: 8, displayColors: true, boxPadding: 4,
            },
        },
    };
    // Shallow-merge scales/plugins one level deep so callers can tweak a
    // single option (e.g. { scales: { x: { stacked: true } } }) without
    // clobbering the rest of the base styling.
    if (exScales) {
        if (exScales.x) base.scales.x = { ...base.scales.x, ...exScales.x };
        if (exScales.y) base.scales.y = { ...base.scales.y, ...exScales.y };
    }
    if (exPlugins) {
        if (exPlugins.legend) base.plugins.legend = { ...base.plugins.legend, ...exPlugins.legend };
        if (exPlugins.tooltip) base.plugins.tooltip = { ...base.plugins.tooltip, ...exPlugins.tooltip };
    }
    return { ...base, ...rest };
}

// Hex color ("#rrggbb") -> "rgba(r,g,b,alpha)", used to derive translucent
// line fills for the compare view's multi-user charts from the shared
// comparison color palette.
function hexToRgba(hex, alpha = 1) {
    const h = hex.replace('#', '');
    const r = parseInt(h.substring(0, 2), 16), g = parseInt(h.substring(2, 4), 16), b = parseInt(h.substring(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
}
