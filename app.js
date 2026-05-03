const fmt = new Intl.NumberFormat('en-US', { maximumFractionDigits: 9 });
async function json(path){ const r = await fetch(path); if(!r.ok) throw new Error(path); return r.json(); }
function statusText(s){ return (s || 'unknown').replaceAll('_',' '); }
function legRow(name, leg){
  return `<tr><td><strong>${leg.label || name}</strong></td><td><code>${leg.token_account}</code></td><td><code>${leg.mint}</code></td><td>${fmt.format(leg.amount_token)} token</td><td>${fmt.format(leg.deposit_sol_equivalent)} SOL</td></tr>`;
}
async function main(){
  const [baseline, latest, incidentData] = await Promise.all([json('data/baseline.json'), json('data/latest.json'), json('data/incidents.json')]);
  document.getElementById('pilotPubkey').textContent = baseline.pilot_pubkey;
  document.getElementById('solscanLink').href = baseline.solscan_url;
  document.getElementById('baselineSol').textContent = `${fmt.format(baseline.deposit_baseline_sol)} SOL`;
  document.getElementById('nativeBuffer').textContent = `${fmt.format(baseline.legs.native_sol)} SOL`;
  document.getElementById('latestStatus').textContent = statusText(latest.status);
  document.getElementById('capturedAt').textContent = `Captured ${baseline.captured_at.slice(0,10)}`;
  document.getElementById('lastUpdated').textContent = latest.last_updated;
  document.getElementById('legsBody').innerHTML = legRow('jitosol', baseline.legs.jitosol) + legRow('inf', baseline.legs.inf);
  document.getElementById('tripwires').innerHTML = Object.entries(latest.tripwires).map(([id,t]) => `<article class="tripwire"><strong>${id}</strong><div>${t.label}</div><span class="status">${statusText(t.status)}</span><p>${t.note}</p></article>`).join('');
  const incidents = incidentData.incidents || [];
  document.getElementById('incidentCount').textContent = incidents.length ? `${incidents.length} published` : 'No incidents';
  document.getElementById('incidents').innerHTML = incidents.length ? incidents.map(i => `<article><strong>${i.date}</strong><p>${i.summary}</p></article>`).join('') : `<div class="empty">No incidents published.</div>`;
}
main().catch(err => { document.getElementById('latestStatus').textContent = 'data load error'; console.error(err); });
