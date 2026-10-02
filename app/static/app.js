const $ = s => document.querySelector(s), root = $('#app');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const fmt = v => { const n = parseFloat(v), s = Math.abs(n).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}); return n < 0 ? `(${s})` : s; };
const cls = v => parseFloat(v) < 0 ? 'neg' : '';
async function api(p, method = 'GET', body) {
  const o = {method, credentials: 'same-origin', headers: {'X-Requested-With': 'fetch'}};
  if (body instanceof FormData) o.body = body; else if (body) { o.body = JSON.stringify(body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch('/api' + p, o), d = await r.json().catch(() => ({}));
  if (r.status === 401 && !p.startsWith('/auth/')) { authView(); throw new Error('Session missing or expired - please sign in again'); }
  if (r.status >= 500) throw new Error('Server error - check the terminal running uvicorn for the traceback');
  if (!r.ok) throw new Error(typeof d.detail === 'string' ? d.detail : 'Check your input');
  return d;
}
let S, T, editing = null, scope = 'current', rf = '', rt = '', tab = 'main', M = [], mCat = '', me = '';
let fInc = '', fExp = '', fDesc = '', fMin = '', fMax = ''; // transaction column filters

function authView(mode = 'login') {
  const su = mode === 'signup';
  root.innerHTML = `<div id="auth" class="card"><img class="icon" src="/favicon.svg" alt=""><h1>ExpTracker by Wickz</h1><p>login or create an account, it's free!</p><p class="m">${su ? 'Create your private account' : 'Sign in to your account'}</p>
  <input id="em" type="email" placeholder="Email" autocomplete="username"><input id="pw" type="password" placeholder="Password${su ? ' (10+ characters)' : ''}" autocomplete="${su ? 'new-password' : 'current-password'}">
  <div class="err" id="er"></div><div class="row"><button id="go">${su ? 'Sign up' : 'Sign in'}</button><button class="g" id="sw">${su ? 'I have an account' : 'Create account'}</button></div></div>`;
  $('#sw').onclick = () => authView(su ? 'login' : 'signup');
  const go = async () => { try { me = (await api(su ? '/auth/signup' : '/auth/login', 'POST', {email: $('#em').value, password: $('#pw').value})).email; await load(); } catch (e) { $('#er').textContent = e.message; } };
  $('#go').onclick = go; $('#pw').onkeydown = e => { if (e.key === 'Enter') go(); };
}

async function load() {
  if (tab === 'charts') return loadCharts();
  [S, T] = await Promise.all([api('/summary'), api('/transactions?scope=' + scope + (scope === 'range' ? (rf ? '&date_from=' + rf : '') + (rt ? '&date_to=' + rt : '') : ''))]);
  S.email = me;
  view();
}

const tabs = () => `<button class="${tab === 'main' ? '' : 'g'}" data-tab="main">Overview</button> <button class="${tab === 'charts' ? '' : 'g'}" data-tab="charts">Visualisation</button>`;
function bindNav() {
  document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { tab = b.dataset.tab; load().catch(x => alert(x.message)); });
  $('#lo').onclick = async () => { await api('/auth/logout', 'POST'); tab = 'main'; authView(); };
}

async function loadCharts() {
  S = await api('/summary');
  if (!S.categories.some(c => String(c.id) === String(mCat))) mCat = '';
  M = await api('/charts/monthly-expenses' + (mCat ? '?category_id=' + mCat : ''));
  S.email = me; chartsView();
}

const PAL = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const mlabel = s => { const [y, mo] = s.split('-'); return MON[mo - 1] + ' ' + y.slice(2); };
const niceStep = x => { const p = Math.pow(10, Math.floor(Math.log10(x))), f = x / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p; };

function pie(title, note, items, colors) {
  const data = items.map((it, i) => ({name: it.name, v: parseFloat(it.value), c: colors ? colors[i] : PAL[i % PAL.length]})).filter(d => d.v > 0);
  const tot = data.reduce((a, d) => a + d.v, 0);
  let body = '<p class="m">No data in this period.</p>';
  if (tot > 0) {
    let a0 = -Math.PI / 2;
    const P = a => [(90 + 80 * Math.cos(a)).toFixed(2), (90 + 80 * Math.sin(a)).toFixed(2)];
    const paths = data.map(d => {
      d.pct = (d.v / tot * 100).toFixed(1);
      const a1 = a0 + d.v / tot * 2 * Math.PI, tip = `<title>${esc(d.name)}: ${fmt(d.v)} (${d.pct}%)</title>`;
      let el;
      if (data.length === 1) el = `<circle cx="90" cy="90" r="80" fill="${d.c}">${tip}</circle>`;
      else { const [x0, y0] = P(a0), [x1, y1] = P(a1); el = `<path class="sl" d="M90 90L${x0} ${y0}A80 80 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${x1} ${y1}Z" fill="${d.c}">${tip}</path>`; }
      a0 = a1; return el;
    }).join('');
    body = `<svg viewBox="0 0 180 180" width="180" height="180" role="img" aria-label="${esc(title)}">${paths}</svg>
     <ul class="lg">${data.map(d => `<li><span class="sw" style="background:${d.c}"></span>${esc(d.name)}<span class="m" style="margin-left:auto">${fmt(d.v)} · ${d.pct}%</span></li>`).join('')}</ul>`;
  }
  return `<div class="card"><h2>${esc(title)}</h2><p class="m">${note}</p>${body}</div>`;
}

function monthly() {
  const vals = M.map(r => parseFloat(r.total)), n = vals.length;
  if (!n) return '<p class="m">No expenses recorded for this selection.</p>';
  const W = 760, H = 320, L = 64, R = 16, T0 = 24, B = 46, pw = W - L - R, ph = H - T0 - B;
  const mx = Math.max(0, ...vals), mn = Math.min(0, ...vals), step = niceStep((mx - mn) / 5 || 1);
  let hi = Math.ceil(mx / step) * step, lo = Math.floor(mn / step) * step; if (hi === lo) hi = lo + step;
  const y = v => T0 + ph - (v - lo) / (hi - lo) * ph, bw = pw / n, cx = i => L + bw * (i + .5), k = Math.round((hi - lo) / step);
  const ticks = Array.from({length: k + 1}, (_, i) => lo + i * step);
  const grid = ticks.map(t => `<line class="gl" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text class="tx" text-anchor="end" x="${L - 6}" y="${y(t) + 4}">${t.toLocaleString('en-US', {maximumFractionDigits: step < 1 ? 2 : 0})}</text>`).join('');
  const every = Math.ceil(n / 12);
  const bars = vals.map((v, i) => `<rect class="bar" x="${cx(i) - bw * .3}" y="${Math.min(y(v), y(0))}" width="${bw * .6}" height="${Math.abs(y(v) - y(0))}"><title>${mlabel(M[i].month)}: ${fmt(v)}</title></rect>`
    + (i % every === 0 ? `<text class="tx" text-anchor="middle" x="${cx(i)}" y="${H - B + 16}">${mlabel(M[i].month)}</text>` : '')
    + (n <= 12 ? `<text class="tx" text-anchor="middle" x="${cx(i)}" y="${y(Math.max(v, 0)) - 9}">${Math.round(v).toLocaleString('en-US')}</text>` : '')).join('');
  let line = '', trend = '';
  if (n >= 2) {
    line = `<polyline class="ln" points="${vals.map((v, i) => cx(i) + ',' + y(v)).join(' ')}"/>` + vals.map((v, i) => `<circle cx="${cx(i)}" cy="${y(v)}" r="3.5" fill="#f28e2b"/>`).join('');
    let sx = 0, sy = 0, sxy = 0, sxx = 0; vals.forEach((v, i) => { sx += i; sy += v; sxy += i * v; sxx += i * i; });
    const den = n * sxx - sx * sx, b = den ? (n * sxy - sx * sy) / den : 0, a = (sy - b * sx) / n;
    trend = `<line class="tr" x1="${cx(0)}" y1="${y(a)}" x2="${cx(n - 1)}" y2="${y(a + b * (n - 1))}"/>`;
  }
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto" role="img" aria-label="Month-on-month expenses">${grid}${bars}${line}${trend}</svg>
   <p class="m"><span class="sw" style="background:var(--acc)"></span>ExpTracker by Wickz <span class="sw" style="background:#f28e2b;margin-left:12px"></span>Month-on-month line <span class="sw" style="background:#e15759;margin-left:12px"></span>Linear trend</p>`;
}

function chartsView() {
  const exp = S.categories.filter(c => c.kind === 'expense'), cur = esc(S.currency);
  root.innerHTML = `<h1>${esc(S.title)}</h1>
                    <header><h2>Graphical Summary & VIsualisation</h2></header>
                    <header><h5>logged in as : ${esc(S.email || '')}</h5></header>
                    <header><div>${tabs()}</div> <button class="g" id="lo">Sign out</button> </header>
  <p class="m">Pie charts cover the current period: ${S.period_start} to ${S.period_end_date}. Categories with zero or negative totals are not drawn.</p>
  <div class="pies">
   ${pie('Balance', `Income vs expenses. Balance: ${cur} ${fmt(S.balance)}`, [{name: 'Income', value: S.income_total}, {name: 'Expenses', value: -parseFloat(S.expense_total)}], ['#59a14f', '#e15759'])}
   ${pie('Expenses by category', `Total: ${cur} ${fmt(-parseFloat(S.expense_total))}`, S.expenses.map(r => ({name: r.name, value: r.total})))}
   ${pie('Income by category', `Total: ${cur} ${fmt(S.income_total)}`, S.income.map(r => ({name: r.name, value: r.total})))}</div>
  <div class="card"><div class="row" style="justify-content:space-between"><h2>Month-on-month expenses</h2>
   <label>Expense category<select id="mc"><option value="">All expense categories</option>${exp.map(c => `<option value="${c.id}" ${String(c.id) === String(mCat) ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label></div>
   <p class="m">Calendar months across all recorded transactions (latest 60 months).</p>${monthly()}</div>`;
  bindNav();
  $('#mc').onchange = ev => { mCat = ev.target.value; loadCharts().catch(x => alert(x.message)); };
}

const opts = (kind, sel) => `<option value="">—</option>` + S.categories.filter(c => c.kind === kind).map(c => `<option value="${c.id}" ${c.id == sel ? 'selected' : ''}>${esc(c.name)}</option>`).join('');
const catRows = (rows, kind) => !rows.length ? '<tr><td class="m">None yet - add one below.</td></tr>' : rows.map(r => `<tr><td>${esc(r.name)}</td><td class="n ${cls(r.total)}">${fmt(r.total)}</td><td class="n"><button class="g" data-delcat="${r.id}" title="Delete category">×</button></td></tr>`).join('');

function filterRows(rows) {
  return rows.filter(t =>
    (!fInc || String(t.income_cat_id) === fInc) &&
    (!fExp || String(t.expense_cat_id) === fExp) &&
    (!fDesc || (t.description || '').toLowerCase().includes(fDesc.toLowerCase())) &&
    (!fMin || parseFloat(t.amount) >= parseFloat(fMin)) &&
    (!fMax || parseFloat(t.amount) <= parseFloat(fMax))
  );
}

function view() {
  const e = editing ? T.find(t => t.id === editing) : null, cur = esc(S.currency);
  root.innerHTML = `<h1>${esc(S.title)}</h1>
                    <header><h2>Data Entry and Tabular View</h2></header>
                    <header><h5>logged in as : ${esc(S.email || '')}</h5></header>
                    <header><div>${tabs()}</div> <button class="g" id="lo">Sign out</button> </header>
  <input type="file" id="file" accept=".csv,text/csv" hidden>
  <div class="grid">
    <div class="card"><h2>Period</h2><div class="row">
    <label>Period Starting Date<input type="date" id="ps" value="${S.period_start}"></label>
    <label>Period Ending Date<input type="date" id="ns" value="${S.period_end_date}"></label>
    <button id="sp">Save</button>${S.start_is_override || S.end_is_override ? '<button class="g" id="auto">Reset to auto</button>' : ''}</div>
    <p class="m">${S.start_is_override || S.end_is_override ? 'Manual dates in use. Reset to auto for the current month (1st to last day).' : 'Auto: current month, 1st to last day. Pick other dates to override.'}</p></div>
   <div class="card"><h2>Balance</h2><div class="big ${cls(S.balance)}">${cur} ${fmt(S.balance)}</div>
    <p class="m">Daily average until Period Ending Date: <b class="${cls(S.daily_average)}">${S.daily_average === null ? 'Only applicable for current month' : fmt(S.daily_average)}</b><br>
    Days until Period Ending Date: <b>${S.days_until_period_end}</b>${S.days_until_period_end <= 0 ? ' <span>Only applicable for current month</span>' : ''}<br>Now: ${esc(S.current_date.replace('T', ' '))} (${esc(S.tz)})</p></div>
   </div>
  <div class="grid">
   <div class="card"><h2>Income <span class="pos">${fmt(S.income_total)}</span></h2><table>${catRows(S.income)}</table>
    <div class="row"><input id="nci" placeholder="New income type"><button class="g" data-addcat="income">Add</button></div></div>
   <div class="card"><h2>Expenses <span class="neg">${fmt(S.expense_total)}</span></h2><table>${catRows(S.expenses)}</table>
    <div class="row"><input id="nce" placeholder="New expense type"><button class="g" data-addcat="expense">Add</button></div></div></div>
  <div class="card"><h2>${e ? 'Edit transaction' : 'Add transaction'}</h2>
   <div class="row" style="margin-bottom:12px"><button class="g" id="tpl">Download CSV Template</button> <button class="g" id="imp">Upload Filled CSV</button></div>
   <p class="m">Or enter a transaction manually:</p>
   <div class="row">
    <label>Date<input type="date" id="d" value="${e ? e.date : new Date().toISOString().slice(0, 10)}"></label>
    <label>Income type<select id="ic">${opts('income', e?.income_cat_id)}</select></label>
    <label>Exp type<select id="ec">${opts('expense', e?.expense_cat_id)}</select></label>
    <label>Description<input id="ds" maxlength="200" value="${esc(e?.description)}"></label>
    <label>Amount (e.g. 300+320)<input id="am" value="${esc(e ? (e.expr || e.amount) : '')}"></label>
    <button id="sv">${e ? 'Update' : 'Add'}</button>${e ? '<button class="g" id="cx">Cancel</button>' : ''}</div><div class="err" id="te"></div></div>
  <div class="card"><div class="row" style="justify-content:space-between"><h2>Transactions</h2>
   <div class="row" style="margin:8px 0">
    <form id="ff" class="row" style="margin:8px 0">
    <label>Income type<select id="fi"><option value="">All</option>${S.categories.filter(c => c.kind === 'income').map(c => `<option value="${c.id}" ${String(c.id) === fInc ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
    <label>Exp type<select id="fe"><option value="">All</option>${S.categories.filter(c => c.kind === 'expense').map(c => `<option value="${c.id}" ${String(c.id) === fExp ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select></label>
    <label>Description<input id="fd" placeholder="Search..." value="${esc(fDesc)}"></label>
    <label>Min amount<input type="number" id="fmn" value="${esc(fMin)}"></label>
    <label>Max amount<input type="number" id="fmx" value="${esc(fMax)}"></label>
    <button type="submit">Apply filters</button> <button type="button" class="g" id="fclr">Clear filters</button></form>
    <select id="sc"><option value="current" ${scope === 'current' ? 'selected' : ''}>Current period</option><option value="all" ${scope === 'all' ? 'selected' : ''}>All</option><option value="range" ${scope === 'range' ? 'selected' : ''}>Date range</option></select>
    ${scope === 'range' ? `<label>From<input type="date" id="rf" value="${rf}"></label><label>To<input type="date" id="rt" value="${rt}"></label>` : ''}</div>
   <div class="wrap"><table><tr><th>Date</th><th>Income type</th><th>Exp type</th><th>Description</th><th class="n">Amount</th><th>Week</th><th></th></tr>
   ${filterRows(T).map(t => `<tr class="${t.current ? '' : 'dim'}"><td>${t.date}</td><td>${esc(t.income_type)}</td><td>${esc(t.expense_type)}</td><td>${esc(t.description)}</td><td class="n ${cls(t.amount)}">${fmt(t.amount)}</td><td>${t.week}</td>
   <td class="n"><button class="g" data-ed="${t.id}">Edit</button> <button class="g" data-dt="${t.id}">Delete</button></td></tr>`).join('') || '<tr><td colspan="7" class="m">No transactions match this view.</td></tr>'}</table></div></div>`;
  const act = fn => async ev => { try { await fn(ev); await load(); } catch (x) { alert(x.message); } };
  bindNav();
  $('#imp').onclick = () => $('#file').click();
  $('#tpl').onclick = () => { location.href = '/api/import/template'; };
  $('#file').onchange = act(async ev => { const f = ev.target.files[0]; ev.target.value = ''; if (!f || !confirm('Import all rows from this CSV into your account?')) return; const fd = new FormData(); fd.append('file', f); const r = await api('/import', 'POST', fd); alert(`Imported ${r.imported} transactions`); });
  $('#sp').onclick = act(() => {
    const ovr = (id, cur, auto, isO) => { const v = $(id).value; return v === cur ? (isO ? cur : null) : (!v || v === auto ? null : v); };
    return api('/settings', 'PUT', {period_start_override: ovr('#ps', S.period_start, S.auto_period_start, S.start_is_override),
      period_end_override: ovr('#ns', S.period_end_date, S.auto_period_end_date, S.end_is_override), tz: S.tz, currency: S.currency}); });
  if ($('#auto')) $('#auto').onclick = act(() => api('/settings', 'PUT', {period_start_override: null, period_end_override: null, tz: S.tz, currency: S.currency}));
  $('#sc').onchange = ev => { scope = ev.target.value; load().catch(x => alert(x.message)); };
  if ($('#rf')) { $('#rf').onchange = ev => { rf = ev.target.value; load().catch(x => alert(x.message)); }; $('#rt').onchange = ev => { rt = ev.target.value; load().catch(x => alert(x.message)); }; }
  $('#ff').onsubmit = ev => {
    ev.preventDefault();
    fInc = $('#fi').value; fExp = $('#fe').value; fDesc = $('#fd').value; fMin = $('#fmn').value; fMax = $('#fmx').value;
    view();
  };
  $('#fclr').onclick = () => { fInc = fExp = fDesc = fMin = fMax = ''; view(); };
  document.querySelectorAll('[data-addcat]').forEach(b => b.onclick = act(() => { const k = b.dataset.addcat, i = $(k === 'income' ? '#nci' : '#nce'); return api('/categories', 'POST', {kind: k, name: i.value}); }));
  document.querySelectorAll('[data-delcat]').forEach(b => b.onclick = act(() => confirm('Delete this category?') && api('/categories/' + b.dataset.delcat, 'DELETE')));
  document.querySelectorAll('[data-dt]').forEach(b => b.onclick = act(() => confirm('Delete this transaction?') && api('/transactions/' + b.dataset.dt, 'DELETE')));
  document.querySelectorAll('[data-ed]').forEach(b => b.onclick = () => { editing = +b.dataset.ed; view(); scrollTo(0, 0); });
  if ($('#cx')) $('#cx').onclick = () => { editing = null; view(); };
  $('#sv').onclick = async () => {
    const body = {date: $('#d').value, income_cat_id: +$('#ic').value || null, expense_cat_id: +$('#ec').value || null, description: $('#ds').value, amount: $('#am').value};
    try { await (editing ? api('/transactions/' + editing, 'PUT', body) : api('/transactions', 'POST', body)); editing = null; await load(); } catch (x) { $('#te').textContent = x.message; }
  };
}

const start = () => load().catch(e => {
  if (/^Session missing/.test(e.message)) return;
  root.innerHTML = `<div id="auth" class="card"><h2>Could not load your data</h2><p class="err">${esc(e.message)}</p><button id="lo2">Sign out</button></div>`;
  $('#lo2').onclick = async () => { await api('/auth/logout', 'POST'); authView(); };
});
api('/auth/me').then(u => { me = u.email; start(); }, () => authView());