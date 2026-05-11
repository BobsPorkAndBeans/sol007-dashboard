const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 9 });
const fmt4 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 });
async function json(path){ const r = await fetch(path); if(!r.ok) throw new Error(path); return r.json(); }
async function text(path){ const r = await fetch(path); if(!r.ok) throw new Error(path); return r.text(); }
function statusText(s){ return (s || 'unknown').replaceAll('_',' '); }
function legRow(name, leg){
  return `<tr><td><strong>${leg.label || name}</strong></td><td><code>${leg.token_account}</code></td><td><code>${leg.mint}</code></td><td>${fmt.format(leg.amount_token)} token</td><td>${fmt.format(leg.deposit_sol_equivalent)} SOL</td></tr>`;
}
function returnLegRow(name, leg){
  return `<tr><td><strong>${leg.label || name}</strong></td><td>${fmt.format(leg.amount_token)}</td><td>${fmt4.format(leg.baseline_price_sol_per_token)}</td><td>${fmt4.format(leg.current_price_sol_per_token)}</td><td>${fmt4.format(leg.yield_sol)} SOL</td></tr>`;
}
function sparkline(points){
  if(points.length < 3) return 'Need 3+ snapshots for sparkline.';
  const values = points.map(p => Number(p.yield_sol_total || 0));
  const min = Math.min(...values), max = Math.max(...values);
  const ticks = '▁▂▃▄▅▆▇█';
  return values.map(v => ticks[Math.round(((v - min) / ((max - min) || 1)) * (ticks.length - 1))]).join('');
}
async function loadReturns(){
  const returns = await json('data/returns.json');
  let history = [];
  try {
    const body = await text('data/returns_history.jsonl');
    history = body.trim().split('\n').filter(Boolean).map(line => JSON.parse(line));
  } catch(err) { console.warn('returns history unavailable', err); }
  document.getElementById('yieldTotal').textContent = `${fmt4.format(returns.yield_sol_total)} SOL`;
  document.getElementById('yieldPct').textContent = `${fmt4.format(returns.yield_pct_total)}%`;
  document.getElementById('yieldApy').textContent = `${fmt4.format(returns.annualized_apy)}%`;
  document.getElementById('yieldDays').textContent = fmt4.format(returns.days_elapsed);
  document.getElementById('returnsUpdated').textContent = `Snapshot ${returns.snapshot_at}`;
  document.getElementById('returnsBody').innerHTML = returnLegRow('jitosol', returns.legs.jitosol) + returnLegRow('inf', returns.legs.inf);
  document.getElementById('returnsSparkline').textContent = sparkline(history);
}
async function main(){
  const [baseline, latest, incidentData] = await Promise.all([json('data/baseline.json'), json('data/latest.json'), json('data/incidents.json')]);
  document.getElementById('pilotPubkey').textContent = baseline.pilot_pubkey;
  document.getElementById('solscanLink').href = baseline.solscan_url;
  document.getElementById('baselineSol').textContent = `${fmt.format(baseline.deposit_baseline_sol)} SOL`;
  document.getElementById('nativeBuffer').textContent = `${fmt.format(baseline.legs.native_sol)} SOL`;
  document.getElementById('latestStatus').textContent = statusText(latest.status || latest.tripwire_status);
  document.getElementById('capturedAt').textContent = `Captured ${baseline.captured_at.slice(0,10)}`;
  document.getElementById('lastUpdated').textContent = latest.last_updated || latest.updated_at;
  document.getElementById('legsBody').innerHTML = legRow('jitosol', baseline.legs.jitosol) + legRow('inf', baseline.legs.inf);
  const tw = latest.tripwires || Object.fromEntries(Object.entries(latest.r_triggers || {}).map(([id,status]) => [id, {label:id, status, note:''}]));
  document.getElementById('tripwires').innerHTML = Object.entries(tw).map(([id,t]) => `<article class="tripwire"><strong>${id}</strong><div>${t.label || id}</div><span class="status">${statusText(t.status || t)}</span><p>${t.note || ''}</p></article>`).join('');
  const incidents = incidentData.incidents || (Array.isArray(incidentData) ? incidentData : []);
  document.getElementById('incidentCount').textContent = incidents.length ? `${incidents.length} published` : 'No incidents';
  document.getElementById('incidents').innerHTML = incidents.length ? incidents.map(i => `<article><strong>${i.date || i.ts || ''}</strong><p>${i.summary || JSON.stringify(i.breaches || i)}</p></article>`).join('') : `<div class="empty">No incidents published.</div>`;
  await loadReturns();
}
main().catch(err => { document.getElementById('latestStatus').textContent = 'data load error'; console.error(err); });

fetch('data/history.json').then(r=>r.json()).then(h=>{const el=document.getElementById('drift-chart'); if(el) el.textContent=h.map(x=>`${x.updated_at || x.timestamp}: ${x.drift_pct}%`).join('\n');});
