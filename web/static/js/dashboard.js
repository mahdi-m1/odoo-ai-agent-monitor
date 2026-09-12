/* dashboard.js — إنشاء وعرض وأرشفة التقارير */

function mdToHtml(text) {
  const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  let t = esc(text);
  t = t.replace(/```([\s\S]*?)```/g, (m, c) => '<pre>' + c.trim() + '</pre>');
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>');
  t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/(^|[^"(])(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  const lines = t.split('\n'); let html = '', inUl = false, i = 0;
  const closeUl = () => { if (inUl) { html += '</ul>'; inUl = false; } };
  const cells = (ln) => ln.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const isRow = (ln) => /^\s*\|.*\|\s*$/.test(ln);
  const isSep = (ln) => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(ln) && ln.includes('-');
  while (i < lines.length) {
    const ln = lines[i]; let m;
    if (isRow(ln) && i + 1 < lines.length && isSep(lines[i + 1])) {
      closeUl();
      const header = cells(ln); i += 2; let rows = '';
      while (i < lines.length && isRow(lines[i])) { rows += '<tr>' + cells(lines[i]).map(c => '<td>' + c + '</td>').join('') + '</tr>'; i++; }
      html += '<table><thead><tr>' + header.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>' + rows + '</tbody></table>';
      continue;
    }
    if ((m = ln.match(/^(#{1,4})\s+(.*)/))) { closeUl(); const lvl = m[1].length + 1; html += `<h${lvl}>${m[2]}</h${lvl}>`; }
    else if (/^\s*[-•*]\s+/.test(ln)) { if (!inUl) { html += '<ul>'; inUl = true; } html += '<li>' + ln.replace(/^\s*[-•*]\s+/, '') + '</li>'; }
    else if (ln.trim()) { closeUl(); html += '<p>' + ln + '</p>'; }
    else closeUl();
    i++;
  }
  closeUl();
  return html;
}

const list = document.getElementById('reports-list');
const viewer = document.getElementById('report-viewer');

// ---- إنشاء تقرير (مهمة خلفية + شريط تقدّم) ----
const shownDone = new Set();
let tasksTimer = null;
function renderReportTasks(tasks) {
  const host = document.getElementById('report-tasks');
  const active = (tasks || []).filter(t => t.kind === 'report' && (t.status === 'running' || (Date.now() / 1000 - (t.finished || t.started)) < 20));
  host.innerHTML = '';
  active.forEach(t => {
    const pct = Math.round((t.progress || 0) * 100);
    const ico = t.status === 'running' ? '⏳' : t.status === 'done' ? '✅' : '⚠️';
    const div = document.createElement('div');
    div.className = 'task-card' + (t.status === 'error' ? ' err' : '');
    div.innerHTML = `<div class="task-head">${ico} ${t.title} — ${t.status === 'running' ? pct + '%' : t.status}</div>` +
      `<div class="task-bar"><div class="task-fill${t.status === 'error' ? ' bad-fill' : ''}" style="width:${pct}%"></div></div>` +
      `<div class="task-log">${(t.last_msg || '')}</div>`;
    host.appendChild(div);
    if ((t.status === 'done' || t.status === 'error') && !shownDone.has(t.id)) {
      shownDone.add(t.id);
      if (t.status === 'done') { toast('تم إنشاء التقرير ✅'); refreshReports(); }
      else toast('تعذّر إنشاء التقرير', 'error');
    }
  });
}
async function pollTasks() {
  try {
    const d = await api('/api/chat/tasks');
    const tasks = (d.tasks || []).filter(t => t.kind === 'report');
    await Promise.all(tasks.filter(t => t.status === 'running').map(async t => {
      try { const f = await api('/api/chat/task/' + t.id); t.last_msg = (f.log && f.log.length) ? f.log[f.log.length - 1].msg : ''; } catch (e) {}
    }));
    renderReportTasks(tasks);
    const running = tasks.some(t => t.status === 'running');
    if (running && !tasksTimer) tasksTimer = setInterval(pollTasks, 2500);
    if (!running && tasksTimer) { clearInterval(tasksTimer); tasksTimer = null; }
  } catch (e) {}
}
async function generate(focus) {
  const fd = new FormData(); fd.append('focus', focus || '');
  const res = await fetch('/api/report', { method: 'POST', body: fd });
  const d = await res.json();
  if (d.error) { toast(d.error, 'error'); return; }
  toast('بدأ إنشاء التقرير…'); pollTasks();
}
document.getElementById('report-form').addEventListener('submit', (e) => { e.preventDefault(); generate(document.getElementById('report-focus').value.trim()); });
document.getElementById('btn-weekly').addEventListener('click', () => generate(''));

// ---- عرض / تنزيل / أرشفة / حذف ----
list.addEventListener('click', async (e) => {
  const item = e.target.closest('.report-item'); if (!item) return;
  const name = item.dataset.name;
  if (e.target.classList.contains('btn-view')) {
    viewer.hidden = false; viewer.innerHTML = '<p class="muted">جاري التحميل…</p>';
    const r = await api('/api/reports/' + encodeURIComponent(name));
    viewer.innerHTML = r.error ? ('خطأ: ' + r.error) : mdToHtml(r.content || '');
    viewer.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else if (e.target.classList.contains('btn-arch')) {
    const archived = item.dataset.archived === '1';
    const r = await api('/api/reports/' + encodeURIComponent(name) + (archived ? '/unarchive' : '/archive'), 'POST');
    if (r.error) toast(r.error, 'error'); else { toast(archived ? 'أُلغيت الأرشفة' : 'تمت الأرشفة'); refreshReports(); }
  } else if (e.target.classList.contains('btn-del')) {
    if (!confirm('حذف التقرير «' + name + '» نهائياً؟')) return;
    const r = await api('/api/reports/' + encodeURIComponent(name), 'DELETE');
    if (r.ok) { toast('حُذف التقرير'); refreshReports(); }
  }
});

// ---- الفلاتر ----
let currentFilter = 'all';
document.getElementById('rep-filters').addEventListener('click', (e) => {
  const b = e.target.closest('.chip-btn'); if (!b) return;
  document.querySelectorAll('#rep-filters .chip-btn').forEach(x => x.classList.remove('active'));
  b.classList.add('active'); currentFilter = b.dataset.f; applyFilter();
});
function applyFilter() {
  document.querySelectorAll('.report-item').forEach(it => {
    const kind = it.dataset.kind, arch = it.dataset.archived === '1';
    let show = true;
    if (currentFilter === 'weekly') show = kind === 'weekly' && !arch;
    else if (currentFilter === 'custom') show = kind === 'custom' && !arch;
    else if (currentFilter === 'archived') show = arch;
    else show = !arch; // "all" = غير المؤرشفة
    it.style.display = show ? '' : 'none';
  });
}

async function refreshReports() {
  const d = await api('/api/reports');
  const reports = d.reports || [];
  document.getElementById('rep-count').textContent = '(' + (d.stats.total) + ')';
  const filters = document.querySelectorAll('#rep-filters .chip-btn');
  filters[0].textContent = 'الكل (' + d.stats.total + ')';
  filters[1].textContent = 'أسبوعية (' + d.stats.weekly + ')';
  filters[2].textContent = 'مخصّصة (' + d.stats.custom + ')';
  filters[3].textContent = 'المؤرشفة (' + d.stats.archived + ')';
  list.innerHTML = reports.length ? '' : '<p class="muted">لا توجد تقارير بعد.</p>';
  reports.forEach(r => {
    const div = document.createElement('div');
    div.className = 'report-item'; div.dataset.name = r.name; div.dataset.kind = r.kind; div.dataset.archived = r.archived ? '1' : '0';
    div.innerHTML =
      `<div class="report-main"><span class="badge ${r.kind === 'weekly' ? 'badge-weekly' : 'badge-custom'}">${r.kind === 'weekly' ? 'أسبوعي' : 'مخصّص'}</span>` +
      (r.archived ? '<span class="badge badge-arch">مؤرشف</span>' : '') +
      `<span class="report-title">${r.title}</span><span class="muted report-date">${(r.created_at || '').slice(0, 16).replace('T', ' ')}</span></div>` +
      `<div class="report-actions"><button type="button" class="ghost small btn-view">عرض</button>` +
      `<a class="ghost small" href="/api/reports/${encodeURIComponent(r.name)}/export?format=pdf">PDF</a>` +
      `<a class="ghost small" href="/api/reports/${encodeURIComponent(r.name)}/export?format=docx">Word</a>` +
      `<a class="ghost small" href="/api/reports/${encodeURIComponent(r.name)}/download">MD</a>` +
      `<button type="button" class="ghost small btn-arch">${r.archived ? 'إلغاء الأرشفة' : 'أرشفة'}</button>` +
      `<button type="button" class="ghost small danger btn-del">حذف</button></div>`;
    list.appendChild(div);
  });
  applyFilter();
}

applyFilter();
pollTasks();
