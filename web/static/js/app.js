async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

document.getElementById('company-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const body = Object.fromEntries(fd.entries());
  const out = document.getElementById('company-result');
  out.textContent = 'جاري الإضافة…';
  try {
    const data = await postJSON('/api/companies', body);
    out.textContent = JSON.stringify(data, null, 2);
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
