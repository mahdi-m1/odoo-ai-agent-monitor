async function api(url, method = 'GET', body) {
  const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
  return res.json();
}
async function upload(url, form) { const res = await fetch(url, { method: 'POST', body: form }); return res.json(); }
function show(id, data) { const el = document.getElementById(id); el.style.display = 'block'; el.textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2); }
const drivePending = !!window.DRIVE_PENDING;

// ---- drive linking ----
document.getElementById('cred-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  show('drive-result', 'جاري الربط…');
  const d = await upload('/api/backup/drive/credentials', new FormData(e.target));
  show('drive-result', d.error ? 'خطأ: ' + d.error : (d.note || '') + '\n' + JSON.stringify(d, null, 2));
  if (d.device) pollLoop(); else if (!d.error) setTimeout(() => location.reload(), 1500);
});
async function pollLoop() {
  for (let i = 0; i < 120; i++) {
    await new Promise(r => setTimeout(r, 5000));
    const d = await api('/api/backup/drive/poll', 'POST');
    if (d.ok) { show('drive-result', '✅ تم الربط' + (d.email ? ' بحساب ' + d.email : '')); setTimeout(() => location.reload(), 1200); return; }
    if (d.state === 'expired') { show('drive-result', '⚠️ انتهت صلاحية الرمز — ارفع الملف مجدداً'); return; }
  }
}
document.getElementById('btn-poll').addEventListener('click', async () => {
  const d = await api('/api/backup/drive/poll', 'POST');
  show('drive-result', d.ok ? '✅ تم الربط' : 'الحالة: ' + d.state);
  if (d.ok) setTimeout(() => location.reload(), 1200);
});
document.getElementById('btn-drive-test').addEventListener('click', async () => show('drive-result', await api('/api/backup/drive/test', 'POST')));
document.getElementById('btn-unlink').addEventListener('click', async () => { if (confirm('فك ربط Google Drive؟')) { await api('/api/backup/drive/unlink', 'POST'); location.reload(); } });
if (drivePending) pollLoop();

// ---- settings ----
document.getElementById('btn-save-backup').addEventListener('click', async () => {
  const body = {
    frequency: document.getElementById('b-freq').value, hour: +document.getElementById('b-hour').value, day: document.getElementById('b-day').value,
    keep_local: +document.getElementById('b-kl').value, keep_remote: +document.getElementById('b-kr').value,
    include_reports: document.getElementById('b-reports').checked,
  };
  const pass = document.getElementById('b-pass').value; if (pass) body.passphrase = pass;
  const d = await api('/api/backup/settings', 'POST', body);
  show('settings-result', d.error ? 'خطأ: ' + d.error : 'تم الحفظ — التالية: ' + (d.next_due || '—'));
});

// ---- backups ----
document.getElementById('btn-backup-now').addEventListener('click', async () => {
  show('backup-result', 'جاري إنشاء النسخة…');
  show('backup-result', await api('/api/backup/now', 'POST'));
  setTimeout(() => location.reload(), 1500);
});
document.getElementById('upload-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const d = await upload('/api/backup/upload', new FormData(e.target));
  show('backup-result', d); if (!d.error) setTimeout(() => location.reload(), 1200);
});
async function inspectAndRestore(sel, doRestore) {
  const body = { ...sel, passphrase: document.getElementById('r-pass').value || null, restore_env: document.getElementById('r-env').checked };
  const info = await api('/api/backup/inspect', 'POST', body);
  if (info.error) { show('backup-result', 'خطأ: ' + info.error); return; }
  show('backup-result', info);
  if (!doRestore) return;
  const m = info.manifest || {};
  if (!confirm(`استرجاع ${info.name}؟\nأُنشئت: ${m.created_at || '?'} على ${m.host || '?'}\nسيتم استبدال الذاكرة والإعدادات الحالية (تُؤخذ نسخة أمان أولاً) ثم إعادة تشغيل الخدمات.`)) return;
  show('backup-result', 'جاري الاسترجاع…');
  const r = await api('/api/backup/restore', 'POST', { ...body, confirm: true });
  show('backup-result', r.error ? 'خطأ: ' + r.error : r);
  if (!r.error) setTimeout(() => location.reload(), 6000);
}
document.getElementById('local-table').addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-name]'); if (!tr) return;
  if (e.target.classList.contains('btn-inspect')) inspectAndRestore({ name: tr.dataset.name }, false);
  if (e.target.classList.contains('btn-restore')) inspectAndRestore({ name: tr.dataset.name }, true);
});
document.getElementById('drive-table').addEventListener('click', (e) => {
  const tr = e.target.closest('tr[data-id]'); if (!tr) return;
  if (e.target.classList.contains('btn-inspect')) inspectAndRestore({ drive_id: tr.dataset.id }, false);
  if (e.target.classList.contains('btn-restore')) inspectAndRestore({ drive_id: tr.dataset.id }, true);
});
(async () => {
  const d = await api('/api/backup/drive');
  const tb = document.querySelector('#drive-table tbody');
  if (!d.linked) return;
  if (d.error) { tb.innerHTML = `<tr><td colspan="4" class="bad">${d.error}</td></tr>`; return; }
  if (!d.backups || !d.backups.length) { tb.innerHTML = '<tr><td colspan="4" class="muted">لا توجد نسخ على Drive بعد.</td></tr>'; return; }
  tb.innerHTML = '';
  d.backups.forEach(f => {
    const tr = document.createElement('tr'); tr.dataset.id = f.id;
    tr.innerHTML = `<td>${f.name}</td><td>${Math.round((+f.size || 0) / 1024)} KB</td><td>${(f.createdTime || '').replace('T', ' ').slice(0, 19)}</td>
      <td><button class="ghost small btn-inspect">فحص</button> <button class="ghost small danger btn-restore">استرجاع</button></td>`;
    tb.appendChild(tr);
  });
})();
