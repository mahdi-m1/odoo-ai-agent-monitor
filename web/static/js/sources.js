async function api(url, method = 'GET', body) {
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return res.json();
}
function show(id, data) {
  const el = document.getElementById(id);
  el.style.display = 'block';
  el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2);
}

// ---- sources table ----
const table = document.getElementById('sources-table');
table?.addEventListener('change', async (e) => {
  const tr = e.target.closest('tr'); if (!tr) return;
  if (e.target.classList.contains('toggle')) {
    const d = await api('/api/sources/' + tr.dataset.id, 'PATCH', { enabled: e.target.checked });
    if (d.error) { alert(d.error); e.target.checked = !e.target.checked; }
  } else if (e.target.classList.contains('toggle-js')) {
    const d = await api('/api/sources/' + tr.dataset.id, 'PATCH', { render_js: e.target.checked });
    if (d.error) { alert(d.error); e.target.checked = !e.target.checked; }
  }
});
table?.addEventListener('click', async (e) => {
  const tr = e.target.closest('tr');
  if (!tr) return;
  const id = tr.dataset.id;
  if (e.target.classList.contains('btn-test')) {
    tr.querySelector('.status').textContent = '…';
    const d = await api('/api/sources/' + id + '/test', 'POST');
    tr.querySelector('.status').textContent = d.ok ? 'ok (' + d.items + ')' : 'error: ' + (d.error || '').slice(0, 60);
    show('sources-result', d);
  }
  if (e.target.classList.contains('btn-del')) {
    if (!confirm('حذف المصدر #' + id + '؟')) return;
    const d = await api('/api/sources/' + id, 'DELETE');
    if (d.ok) tr.remove();
  }
});
document.getElementById('btn-test-all')?.addEventListener('click', async () => {
  show('sources-result', 'جاري فحص كل المصادر…');
  const d = await api('/api/sources/test-all', 'POST');
  show('sources-result', d.results.map(r => '#' + r.id + ' ' + r.name + ': ' + (r.ok ? '✅ ' + r.items : '⚠️ ' + (r.error || '').slice(0, 100))).join('\n'));
  setTimeout(() => location.reload(), 1500);
});
document.getElementById('btn-reset-sources')?.addEventListener('click', async () => {
  if (!confirm('استعادة قائمة المصادر الافتراضية؟ سيتم حذف المصادر المضافة يدوياً.')) return;
  await api('/api/sources/reset', 'POST');
  location.reload();
});
document.getElementById('source-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = Object.fromEntries(new FormData(e.target).entries());
  body.render_js = e.target.querySelector('[name=render_js]')?.checked || false;
  const out = document.getElementById('source-add-result');
  out.textContent = 'جاري الإضافة والفحص…';
  const d = await api('/api/sources', 'POST', body);
  out.textContent = d.error ? 'خطأ: ' + d.error : JSON.stringify(d, null, 2);
  if (!d.error) setTimeout(() => location.reload(), 1200);
});

// ---- discover ----
document.getElementById('discover-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = new FormData(e.target).get('url');
  const box = document.getElementById('discover-result');
  box.innerHTML = '<p class="muted">جاري البحث عن خلاصات…</p>';
  const d = await api('/api/sources/discover', 'POST', { url });
  if (!d.feeds || !d.feeds.length) {
    box.innerHTML = '<p class="muted">لم أجد خلاصات RSS. ' + (d.page_ok ? 'يمكن إضافة الموقع كنوع <b>page</b>.' : 'الموقع لم يستجب.') + '</p>';
    if (d.page_ok) {
      const b = document.createElement('button'); b.className = 'ghost'; b.textContent = 'أضف كصفحة';
      b.onclick = async () => { const r = await api('/api/sources', 'POST', { name: d.title || d.site, url: d.site, type: 'page' }); r.error ? alert(r.error) : location.reload(); };
      box.appendChild(b);
    }
    return;
  }
  box.innerHTML = '';
  d.feeds.forEach(f => {
    const row = document.createElement('div'); row.className = 'row'; row.style.marginBottom = '.4rem';
    row.innerHTML = '<span class="url">' + f.url + '</span><span class="muted">' + f.items + ' عنصر · ' + (f.title || '') + '</span>';
    const b = document.createElement('button'); b.className = 'ghost small'; b.textContent = 'أضف';
    b.onclick = async () => { const r = await api('/api/sources', 'POST', { name: f.title || d.title || d.site, url: f.url, type: 'rss' }); r.error ? alert(r.error) : location.reload(); };
    row.appendChild(b); box.appendChild(row);
  });
});

// ---- social settings ----
function socialBody() {
  const body = {
    social_enabled: document.getElementById('soc-enabled').checked,
    public_fetch: document.getElementById('soc-fetch').checked,
    linkedin_enabled: document.getElementById('li-enabled').checked,
    platforms: [...document.querySelectorAll('.soc-plat:checked')].map(x => x.value),
  };
  const tok = document.getElementById('li-token').value.trim();
  if (tok) body.linkedin_token = tok;
  return body;
}
document.getElementById('btn-soc-save')?.addEventListener('click', async () => {
  const d = await api('/api/social', 'POST', socialBody());
  document.getElementById('social-result').textContent = 'تم الحفظ:\n' + JSON.stringify(d, null, 2);
});
document.getElementById('btn-li-verify')?.addEventListener('click', async () => {
  await api('/api/social', 'POST', socialBody());
  const d = await api('/api/social/linkedin/verify', 'POST');
  document.getElementById('social-result').textContent = d.ok ? '✅ LinkedIn متصل: ' + (d.name || '') + ' ' + (d.email || '') : '⚠️ ' + (d.error || JSON.stringify(d));
});

// ---- partner social links ----
document.querySelectorAll('.btn-links').forEach(btn => btn.addEventListener('click', async () => {
  const tr = btn.closest('tr');
  const links = tr.querySelector('.links').value.split(/[\n,]+/).map(s => s.trim()).filter(Boolean);
  const d = await api('/api/partners/' + tr.dataset.pid + '/social-links', 'PUT', { links });
  show('links-result', d);
}));
