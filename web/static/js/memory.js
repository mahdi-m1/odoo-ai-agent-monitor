async function api(url, method = 'GET', body) {
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return res.json();
}
function show(id, data) { const el = document.getElementById(id); el.style.display = 'block'; el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2); }

document.getElementById('btn-consolidate').addEventListener('click', async () => {
  show('mem-result', 'جاري الأرشفة والتلخيص…');
  show('mem-result', await api('/api/memory/consolidate', 'POST'));
  setTimeout(() => location.reload(), 1500);
});
document.getElementById('btn-refresh').addEventListener('click', () => location.reload());

document.getElementById('fact-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  const d = await api('/api/memory', 'POST', { ...body, kind: 'fact', importance: 1.0 });
  document.getElementById('fact-result').textContent = d.error ? 'خطأ: ' + d.error : 'حُفظت #' + d.id;
  if (!d.error) e.target.reset();
});

document.getElementById('search-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const q = encodeURIComponent(fd.get('q')); const kind = fd.get('kind');
  const d = await api('/api/memory/search?q=' + q + '&k=15' + (kind ? '&kind=' + kind : ''));
  const tb = document.querySelector('#search-table tbody'); tb.innerHTML = '';
  if (!d.results || !d.results.length) { tb.innerHTML = '<tr><td colspan="7">لا نتائج</td></tr>'; return; }
  d.results.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${r.score}</td><td>${r.kind}</td><td>${r.tier}</td><td>${(r.event_at || '').slice(0, 10)}</td><td>${r.entity || '—'}</td>
      <td>${r.text.slice(0, 220)}${r.url && r.url.startsWith('http') ? ` <a href="${r.url}" target="_blank">↗</a>` : ''}</td>
      <td><button class="ghost small" data-id="${r.id}" data-tier="${r.tier === 'archive' ? 'hot' : 'archive'}">${r.tier === 'archive' ? 'إحياء' : 'أرشفة'}</button></td>`;
    tb.appendChild(tr);
  });
});
document.querySelector('#search-table').addEventListener('click', async (e) => {
  const b = e.target.closest('button[data-id]'); if (!b) return;
  await api('/api/memory/' + b.dataset.id, 'PATCH', { tier: b.dataset.tier });
  b.textContent = '✓';
});

document.getElementById('btn-save-settings').addEventListener('click', async () => {
  const body = {
    qa_cache: document.getElementById('s-qa').checked, qa_threshold: +document.getElementById('s-th').value,
    qa_ttl_hours: +document.getElementById('s-ttl').value, recall_k: +document.getElementById('s-k').value,
    recall_max_chars: +document.getElementById('s-chars').value, hot_days: +document.getElementById('s-hot').value,
    archive_days: +document.getElementById('s-arch').value, consolidate_with_claude: document.getElementById('s-cc').checked,
    consolidate_model: document.getElementById('s-cm').value,
  };
  show('settings-result', await api('/api/memory/settings', 'POST', body));
});
