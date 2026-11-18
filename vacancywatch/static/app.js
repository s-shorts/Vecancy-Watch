/* VacancyWatch front-end helpers */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) {} },
  };
  const post = (url, body) => fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}),
  }).then(r => r.json());

  function toast(msg) {
    const t = document.createElement('div');
    t.className = 'toast'; t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 4500);
  }

  /* ---------- scan button + progress + browser notifications ---------- */
  let wasRunning = false, pollTimer = null;
  let lastNotif = parseInt(store.get('vw_last_notif') || '0', 10);

  function showBrowserNotifs(list) {
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    list.forEach(n => {
      const note = new Notification(n.title, { body: (n.body || '').split('\n')[0].slice(0, 160) });
      if (n.url) note.onclick = () => window.open(n.url, '_blank');
    });
  }

  function poll() {
    fetch('/api/status?since=' + lastNotif).then(r => r.json()).then(d => {
      const p = d.progress, btn = $('#scan-btn'), bar = $('#scan-bar'), box = $('#scan-progress'), txt = $('#scan-text');
      if (btn) {
        btn.disabled = p.running;
        if (p.running) {
          box.classList.remove('hidden');
          const pct = p.total ? Math.round(100 * p.done / p.total) : 3;
          bar.style.width = pct + '%';
          txt.textContent = `Checking ${p.done}/${p.total} · ${p.new} new`;
        } else {
          box.classList.add('hidden');
          txt.textContent = 'Last checked ' + d.last_scan;
        }
      }
      const badge = $('#unread-badge');
      if (badge) { badge.textContent = d.unread; badge.classList.toggle('hidden', !d.unread); }
      if (lastNotif === 0) { lastNotif = d.max_id; }
      else if (d.fresh && d.fresh.length) { showBrowserNotifs(d.fresh); lastNotif = d.max_id; }
      store.set('vw_last_notif', String(lastNotif));
      if (wasRunning && !p.running && document.body && !$('.wizard')) {
        toast(p.new ? `Check finished — ${p.new} new vacanc${p.new === 1 ? 'y' : 'ies'} found` : 'Check finished — nothing new');
        setTimeout(() => location.reload(), 1200);
      }
      wasRunning = p.running;
      clearTimeout(pollTimer);
      pollTimer = setTimeout(poll, p.running ? 2500 : 60000);
    }).catch(() => { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 60000); });
  }

  document.addEventListener('DOMContentLoaded', () => {
    const btn = $('#scan-btn');
    if (btn) {
      btn.addEventListener('click', () => {
        if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
        post('/api/scan').then(d => { if (d.started) { wasRunning = true; toast('Checking all institutions…'); } poll(); });
      });
      if ('Notification' in window && Notification.permission === 'default') {
        document.addEventListener('click', () => Notification.requestPermission(), { once: true });
      }
      poll();
    }

    /* vacancy toggles */
    $$('.vcard .tog').forEach(b => b.addEventListener('click', () => {
      const card = b.closest('.vcard'), field = b.dataset.field;
      post(`/api/vacancy/${card.dataset.id}/${field}`).then(d => {
        b.classList.toggle('on', !!d.value);
        const labels = { starred: ['Save', 'Saved'], applied: ['Applied?', 'Applied'], hidden: ['Hide', 'Hidden'] };
        b.querySelector('span').textContent = labels[field][d.value ? 1 : 0];
        if (field === 'hidden') card.classList.toggle('is-hidden', !!d.value);
        if (field === 'applied') card.classList.toggle('is-applied', !!d.value);
      });
    }));

    /* source toggles */
    $$('.src-toggle').forEach(cb => cb.addEventListener('change', () => {
      const tr = cb.closest('tr');
      post(`/api/sources/${tr.dataset.id}/toggle`).then(d => { cb.checked = !!d.enabled; tr.classList.toggle('off', !d.enabled); });
    }));
    $$('.src-scan').forEach(b => b.addEventListener('click', () => {
      const tr = b.closest('tr');
      post(`/api/sources/${tr.dataset.id}/scan`).then(d => {
        toast(d.started ? 'Checking this institution…' : 'A check is already running — try again in a moment');
        if (d.started) { wasRunning = true; poll(); }
      });
    }));
  });

  /* ---------- public helpers used by pages ---------- */
  window.VW = {
    initWizard() {
      const show = n => {
        $$('.wstep').forEach(s => s.classList.toggle('hidden', s.dataset.step !== String(n)));
        $$('.steps li').forEach(li => {
          const k = +li.dataset.step;
          li.classList.toggle('on', k === +n); li.classList.toggle('done', k < +n);
        });
        window.scrollTo({ top: 0, behavior: 'smooth' });
      };
      $$('[data-go]').forEach(b => b.addEventListener('click', () => {
        if (b.dataset.go === '3' && !$$('input[name=states]:checked').length) { toast('Please choose at least one state'); return; }
        show(b.dataset.go);
      }));
      $('#wizard').addEventListener('submit', e => {
        if (!$$('input[name=states]:checked').length) { e.preventDefault(); show(2); toast('Please choose at least one state'); }
        if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
      });
      VW.initTelegramDetect();
    },
    initStatePicker() {
      const boxes = $$('input[name=states]'), count = $('#state-count');
      const upd = () => { if (count) count.textContent = boxes.filter(b => b.checked).length + ' selected'; };
      boxes.forEach(b => b.addEventListener('change', upd)); upd();
      const f = $('#state-filter');
      if (f) f.addEventListener('input', () => {
        const q = f.value.toLowerCase();
        $$('.state').forEach(l => l.classList.toggle('hidden', !l.textContent.toLowerCase().includes(q)));
      });
      $$('[data-select]').forEach(b => b.addEventListener('click', () => {
        boxes.forEach(x => { if (!x.closest('.state').classList.contains('hidden')) x.checked = b.dataset.select === 'all'; });
        upd();
      }));
    },
    initTelegramDetect() {
      const btn = $('#tg-detect'); if (!btn) return;
      btn.addEventListener('click', () => {
        $('#tg-msg').textContent = 'Looking…';
        post('/api/telegram/detect', { token: $('#tg-token').value.trim() }).then(d => {
          if (d.chat_id) $('#tg-chat').value = d.chat_id;
          $('#tg-msg').textContent = d.chat_id ? 'Found your chat ID ✓' : d.message;
        });
      });
    },
    initChannelTests() {
      VW.initTelegramDetect();
      $$('[data-test]').forEach(b => b.addEventListener('click', () => {
        const form = b.closest('form'), data = {};
        ['telegram_token', 'telegram_chat_id', 'smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'email_to']
          .forEach(k => { const el = form.elements[k]; if (el && el.value) data[k] = el.value; });
        $('#test-msg').textContent = 'Sending…';
        post('/api/test/' + b.dataset.test, data).then(d => { $('#test-msg').textContent = (d.ok ? '✓ ' : '✗ ') + d.message; toast(d.message); });
      }));
    },
  };
})();
