async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

function peopleFromTextarea(text) {
  if (!text || !String(text).trim()) return [];
  return String(text)
    .split(/\n+/)
    .map((l) => l.trim())
    .filter(Boolean);
}

document.getElementById('company-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  body.people = peopleFromTextarea(body.people || '');
  const out = document.getElementById('company-result');
  out.textContent = 'جاري الإضافة…';
  try {
    const data = await postJSON('/api/companies', body);
    out.textContent = JSON.stringify(data, null, 2);
    if (data.warning) out.textContent += '\n\n⚠️ ' + data.warning;
    if (data.monitoring_ready === false) {
      out.textContent += '\n\nأضف شخصيات للشركة حتى تعمل مراقبة التعيينات والترقيات.';
    }
  } catch (err) {
    out.textContent = String(err);
  }
});

document.getElementById('person-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  const out = document.getElementById('person-result');
  out.textContent = 'جاري الإضافة…';
  try {
    const data = await postJSON('/api/people', body);
    out.textContent = JSON.stringify(data, null, 2);
  } catch (err) {
    out.textContent = String(err);
  }
});
