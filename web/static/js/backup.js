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

// ---- Push notifications ----
function urlB64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  return Uint8Array.from([...raw].map(c => c.charCodeAt(0)));
}
async function pushStatus() {
  const el = document.getElementById('push-status');
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) { if (el) el.textContent = 'غير مدعوم في هذا المتصفح'; return; }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    const st = await api('/api/push/state');
    if (el) el.textContent = (sub ? 'مُفعّلة على هذا الجهاز' : 'غير مُفعّلة') + ' · مشتركون: ' + (st.subscriptions || 0) + ' · إذن: ' + Notification.permission;
  } catch (e) { if (el) el.textContent = 'خطأ: ' + e; }
}
document.getElementById('btn-push-enable')?.addEventListener('click', async () => {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) { toast('المتصفح لا يدعم الإشعارات', 'error'); return; }
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') { toast('لم يُمنح إذن الإشعارات', 'error'); return; }
  const { publicKey } = await api('/api/push/vapid');
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(publicKey) });
  const r = await api('/api/push/subscribe', 'POST', sub.toJSON());
  toast(r.ok ? 'تم تفعيل الإشعارات ✅' : 'تعذّر التفعيل', r.ok ? '' : 'error');
  pushStatus();
});
document.getElementById('btn-push-test')?.addEventListener('click', async () => {
  const r = await api('/api/push/test', 'POST');
  toast(r.ok ? 'أُرسل إشعار تجريبي (' + r.sent + ')' : (r.skipped || r.error || 'لا مشتركين'), r.ok ? '' : 'error');
});
document.getElementById('btn-push-disable')?.addEventListener('click', async () => {
  const reg = await navigator.serviceWorker.ready;
  const sub = await reg.pushManager.getSubscription();
  if (sub) { await api('/api/push/unsubscribe', 'POST', { endpoint: sub.endpoint }); await sub.unsubscribe(); }
  toast('تم إيقاف الإشعارات على هذا الجهاز');
  pushStatus();
});
if (document.getElementById('push-status')) pushStatus();


// ---- Email (SMTP) settings ----
const MAIL_PRESETS = { gmail: { host: 'smtp.gmail.com', port: 465, security: 'ssl' }, outlook: { host: 'smtp.office365.com', port: 587, security: 'starttls' } };
document.getElementById('m-preset')?.addEventListener('change', (e) => {
  const p = MAIL_PRESETS[e.target.value]; if (!p) return;
  document.getElementById('m-host').value = p.host;
  document.getElementById('m-port').value = p.port;
  document.getElementById('m-security').value = p.security;
  toast('تم تعبئة إعدادات ' + e.target.value + ' — أكمل البريد وكلمة مرور التطبيق');
});
function mailBody() {
  const body = {
    enabled: document.getElementById('m-enabled').checked,
    host: document.getElementById('m-host').value.trim(),
    port: +document.getElementById('m-port').value,
    security: document.getElementById('m-security').value,
    username: document.getElementById('m-username').value.trim(),
    from_name: document.getElementById('m-from-name').value.trim(),
    from_addr: document.getElementById('m-from-addr').value.trim(),
    recipients: document.getElementById('m-recipients').value,
    attach_pdf: document.getElementById('m-pdf').checked,
    attach_docx: document.getElementById('m-docx').checked,
    on_weekly: document.getElementById('m-on-weekly').checked,
    on_any_report: document.getElementById('m-on-any').checked,
    frequency: document.getElementById('m-freq').value,
    hour: +document.getElementById('m-hour').value,
    day: document.getElementById('m-day').value,
    schedule_action: document.getElementById('m-action').value,
  };
  const pw = document.getElementById('m-password').value;   // أرسل السر فقط إن كان غير فارغ
  if (pw) body.password = pw;
  return body;
}
async function saveMail() {
  const d = await api('/api/email/settings', 'POST', mailBody());
  if (d.error) { show('mail-result', 'خطأ: ' + d.error); toast(d.error, 'error'); return null; }
  show('mail-result', 'تم الحفظ · التالي: ' + (d.next_due || '—'));
  document.getElementById('m-password').value = '';
  return d;
}
document.getElementById('btn-mail-save')?.addEventListener('click', async () => { if (await saveMail()) toast('حُفظت إعدادات البريد ✅'); });
document.getElementById('btn-mail-conn')?.addEventListener('click', async () => {
  if (!(await saveMail())) return;
  show('mail-result', 'جاري اختبار الاتصال…');
  const d = await api('/api/email/test-connection', 'POST');
  show('mail-result', d.ok ? '✅ ' + d.detail : '⚠️ ' + d.error);
  toast(d.ok ? 'الاتصال ناجح ✅' : 'فشل الاتصال', d.ok ? '' : 'error');
});
document.getElementById('btn-mail-test')?.addEventListener('click', async () => {
  if (!(await saveMail())) return;
  show('mail-result', 'جاري الإرسال…');
  const d = await api('/api/email/test', 'POST');
  show('mail-result', d.ok ? '✅ أُرسلت رسالة تجريبية إلى ' + d.to : '⚠️ ' + (d.error || d.skipped));
  toast(d.ok ? 'أُرسلت الرسالة التجريبية ✅' : 'تعذّر الإرسال', d.ok ? '' : 'error');
});
