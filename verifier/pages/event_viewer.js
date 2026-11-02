const EV = JSON.parse(document.getElementById('events').textContent);
const $ = id => document.getElementById(id);
const COLS = {freeze:'#ff4d4f', judge_veto:'#b48cff', planner_refusal:'#4da3ff', release:'#3ddc84',
  operator_hold:'#b48cff', override_stamp:'#2ec4b6'};
const LCOL = {true_positive:'#3ddc84', yes:'#3ddc84', false_alarm:'#ff4d4f', no:'#ff4d4f', unclear:'#ffb020'};
let cur = null;
function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function list(){
  $('list').innerHTML = EV.map(e => `<a class="li" href="#${encodeURIComponent(e.id)}" data-id="${esc(e.id)}">` +
    `<span class="dot" style="background:${e.label ? (LCOL[e.label.label] || '#8b9096') : '#8b9096'}"></span>` +
    `${esc(e.id.split('#')[1])} ${esc(e.type)} <span class="mut">${esc(e.subtype)}</span></a>`).join('');
}
function lanes(e){
  const W = 640, H = 16, names = ['phase','cmd','1','2','3','4','5','6','7','8','hands'];
  const x = dt => 70 + (W - 80) * (dt + 3) / 6;
  let s = `<svg viewBox="0 0 ${W} ${names.length * H + 18}" width="100%">`;
  names.forEach((n, k) => { s += `<text x="0" y="${k * H + 12}">${n === 'cmd' ? 'command' : (/\d/.test(n) ? 'monitor ' + n : n)}</text>`; });
  e.steps.forEach((st, i) => {
    const nx = i + 1 < e.steps.length ? e.steps[i + 1].dt : st.dt + 0.067, w = Math.max(1, x(nx) - x(st.dt));
    s += `<rect x="${x(st.dt)}" y="1" width="${w}" height="12" fill="${st.phase && st.phase.startsWith('PLACE') ? '#2ec4b6' : '#4da3ff'}" opacity=".35"><title>${esc(st.phase)}</title></rect>`;
    if (st.cmd) s += `<rect x="${x(st.dt)}" y="${H + 1}" width="${w}" height="12" fill="#8b9096" opacity=".5"/>`;
    st.fired.forEach(m => { s += `<rect x="${x(st.dt)}" y="${(m + 1) * H + 1}" width="${w}" height="12" fill="#ff4d4f"/>`; });
    if (st.n_hands) s += `<rect x="${x(st.dt)}" y="${10 * H + 1}" width="${w}" height="12" fill="#ffb020" opacity="${Math.min(1, .4 * st.n_hands)}"/>`;
  });
  s += `<line id="cursor" x1="${x(0)}" x2="${x(0)}" y1="0" y2="${names.length * H}" stroke="#eef0f2" stroke-width="1.5"/>`;
  s += `<text x="${x(-3)}" y="${names.length * H + 14}">-3 s</text><text x="${x(0) - 6}" y="${names.length * H + 14}">0</text><text x="${x(3) - 24}" y="${names.length * H + 14}">+3 s</text></svg>`;
  return s;
}
function show(id){
  const e = EV.find(v => v.id === id) || EV[0]; if (!e) return; cur = e;
  document.querySelectorAll('.li').forEach(a => a.classList.toggle('on', a.dataset.id === e.id));
  const lc = e.label ? (LCOL[e.label.label] || '#8b9096') : '#8b9096';
  $('badge').style.borderLeftColor = COLS[e.type] || '#8b9096';
  $('btitle').textContent = `${e.type.replace('_', ' ').toUpperCase()} · ${e.subtype}`;
  $('bsub').textContent = e.detail;
  const lab = e.label ? `<b style="color:${lc}">${esc(e.label.label)}</b> · ${esc(e.label.review === 'one_by_one' ? 'the operator, one by one' : e.label.review === 'bulk' ? 'bulk: a proposal accepted without looking' : 'a model panel')}` +
    (e.label.note ? ` · note: “${esc(e.label.note)}”` : '') + ` · label row #${e.label.seq} <span class="mono">${esc(e.label.row_sha256.slice(0, 16))}</span>` : 'unreviewed (no label)';
  $('facts').innerHTML = `<div><dt>Session</dt><dd>${esc(e.session)} · ${esc(e.wall_iso)} · ${e.rel_s == null ? '-' : '+' + e.rel_s.toFixed(1) + ' s into the loop'}</dd></div>` +
    `<div><dt>Label</dt><dd>${lab}</dd></div>` +
    (e.latency_ms != null ? `<div><dt>Frame to hold</dt><dd>${e.latency_ms} ms</dd></div>` : '') +
    (e.judge ? `<div><dt>Judge #${e.judge.seq} (${esc(e.judge.phase)})</dt><dd>${esc(e.judge.recommend)}, p_unsafe ${esc(e.judge.p_unsafe)}, ${esc(e.judge.latency_s)} s, ${e.judge.unsafe_by_code ? 'UNSAFE' : 'not UNSAFE'} by the code; its words: “${esc(e.judge.reason)}”</dd></div>` : '') +
    `<div><dt>Chain (V1 row #${e.chain.seq})</dt><dd class="mono">before ${esc(e.chain.prev_sha256)}<br>this ${esc(e.chain.row_sha256)}<br>after ${esc(e.chain.next_sha256)}</dd></div>`;
  $('mnote').textContent = e.media.note;
  $('lanes').innerHTML = e.steps.length ? lanes(e) : '<p class="mut">No step rows within 3 s (the event precedes the control loop).</p>';
  $('rows').innerHTML = e.steps.map(s => ({dt: s.dt, html: `<tr data-dt="${s.dt}"><td>${s.dt >= 0 ? '+' : ''}${s.dt.toFixed(2)}</td><td>${esc(s.phase)}</td><td>${s.cmd ? 'sent' : '-'}</td><td>${s.fired.length ? 'm' + s.fired.join(',m') : '-'}</td><td>${s.n_hands == null ? '-' : s.n_hands}</td><td class="mono">${s.palms ? esc(JSON.stringify(s.palms)) : '-'}</td></tr>`}))
    .concat(e.rows.map(r => ({dt: r.dt, html: `<tr data-dt="${r.dt}" class="${r.this ? 'this' : 'other'}"><td>${r.dt >= 0 ? '+' : ''}${r.dt.toFixed(2)}</td><td colspan="5">${esc(r.text)}</td></tr>`})))
    .sort((a, b) => a.dt - b.dt).map(r => r.html).join('');
  $('scrub').value = 0; scrub(0);
}
function scrub(v){
  if (!cur) return; v = Number(v); $('tval').textContent = (v >= 0 ? '+' : '') + v.toFixed(2) + ' s';
  const c = $('cursor'); if (c) { const x = 70 + 560 * (v + 3) / 6; c.setAttribute('x1', x); c.setAttribute('x2', x); }
  const fr = cur.media.frames; let f = fr[0];
  if (cur.media.kind === 'clip' && fr.length) f = fr.reduce((a, b) => Math.abs(b.dt - v) < Math.abs(a.dt - v) ? b : a);
  $('img').innerHTML = f ? `<img alt="${esc(f.caption)}" src="${f.src}"><div class="mut">${esc(f.caption)}${fr.length > 1 && cur.media.kind !== 'clip' ? ' · ' + (fr.length - 1) + ' more below' : ''}</div>` + (cur.media.kind !== 'clip' ? fr.slice(1).map(g => `<img alt="${esc(g.caption)}" src="${g.src}"><div class="mut">${esc(g.caption)}</div>`).join('') : '') : '<p class="mut">No stored frame for this event.</p>';
  let best = null; document.querySelectorAll('#rows tr').forEach(tr => { tr.classList.remove('at'); if (best === null || Math.abs(tr.dataset.dt - v) < Math.abs(best.dataset.dt - v)) best = tr; });
  if (best) best.classList.add('at');
}
$('scrub').addEventListener('input', ev => scrub(ev.target.value));
document.addEventListener('keydown', ev => { if (ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') { const s = $('scrub'); s.value = Math.max(-3, Math.min(3, Number(s.value) + (ev.key === 'ArrowRight' ? 1 : -1) / 15)); scrub(s.value); } });
window.addEventListener('hashchange', () => show(decodeURIComponent(location.hash.slice(1))));
list(); show(decodeURIComponent(location.hash.slice(1)));
