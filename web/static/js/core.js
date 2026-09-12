/* core.js — دوال مشتركة + PWA (service worker، تثبيت، إشعارات، toast). يُحمّل في كل الصفحات. */
(function () {
  'use strict';

  // ---- HTTP helpers (مشتركة بين كل الصفحات) ----
  window.api = async function (url, method, body) {
    const opts = { method: method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    try { return await res.json(); } catch (e) { return { error: 'رد غير صالح', status: res.status }; }
  };
  window.postJSON = function (url, body) { return window.api(url, 'POST', body); };

  // ---- Toast (بديل alert) ----
  window.toast = function (msg, kind) {
    const host = document.getElementById('toast-host');
    if (!host) { console.log(msg); return; }
    const el = document.createElement('div');
    el.className = 'toast' + (kind ? ' ' + kind : '');
    el.textContent = msg;
    host.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 300); }, kind === 'error' ? 6000 : 3500);
  };

  // ---- Skeleton helper ----
  window.skeleton = function (n) {
    let h = '';
    for (let i = 0; i < (n || 3); i++) h += '<div class="skeleton skeleton-row"></div>';
    return h;
  };

  // ---- Service Worker + تدفّق تحديث ----
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/sw.js').then(function (reg) {
        reg.addEventListener('updatefound', function () {
          const nw = reg.installing;
          if (!nw) return;
          nw.addEventListener('statechange', function () {
            if (nw.state === 'installed' && navigator.serviceWorker.controller) {
              const t = document.createElement('div');
              t.className = 'toast show update';
              t.innerHTML = 'تحديث متاح · <button type="button" class="link-btn" id="sw-reload">إعادة تحميل</button>';
              (document.getElementById('toast-host') || document.body).appendChild(t);
              t.querySelector('#sw-reload').onclick = function () { nw.postMessage('skipWaiting'); };
            }
          });
        });
      }).catch(function () {});
      let refreshing = false;
      navigator.serviceWorker.addEventListener('controllerchange', function () {
        if (refreshing) return; refreshing = true; window.location.reload();
      });
    });
  }

  // ---- زر التثبيت (beforeinstallprompt) ----
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    deferredPrompt = e;
    const btn = document.getElementById('btn-install');
    if (btn) {
      btn.hidden = false;
      btn.onclick = async function () {
        btn.hidden = true;
        deferredPrompt.prompt();
        try { await deferredPrompt.userChoice; } catch (e) {}
        deferredPrompt = null;
      };
    }
  });
  window.addEventListener('appinstalled', function () {
    const btn = document.getElementById('btn-install'); if (btn) btn.hidden = true;
    window.toast('تم تثبيت التطبيق ✅');
  });
})();
