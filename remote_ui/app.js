(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const KIND = { INPUT: 'حركة على الجهاز', TAMPER: 'محاولة تلاعب', APP_SWITCH: 'فتح تطبيق', BROWSER: 'تصفح' };

  let S = null;            // last state from the PC
  let loggedIn = false;
  let timer = null;
  let sirenTimer = null;
  let sirenOn = false;

  // ---------- helpers ----------
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, '');
      else if (v !== false && v != null) el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  }

  let toastT;
  function toast(msg, bad) {
    const t = $('#toast');
    t.textContent = msg;
    t.className = 'show' + (bad ? ' bad' : '');
    clearTimeout(toastT);
    toastT = setTimeout(() => { t.className = ''; }, 3200);
  }

  async function api(path, { method = 'GET', body, blob } = {}) {
    const res = await fetch(path, {
      method, credentials: 'same-origin',
      headers: { 'X-ARGUS': '1', ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401 && path !== '/api/login') { showLogin(); throw new Error('سجّل دخول تاني'); }
    if (!res.ok) {
      let data = {};
      try { data = await res.json(); } catch (_) { /* ignore */ }
      const e = new Error(data.error || 'حصل خطأ');
      e.status = res.status; e.data = data;
      throw e;
    }
    if (blob) return res.blob();
    return res.json();
  }

  const parse = (s) => new Date(s.replace(' ', 'T'));
  function dur(sec) {
    if (sec < 60) return 'أقل من دقيقة';
    const m = Math.floor(sec / 60);
    if (m < 60) return `${m} دقيقة`;
    const hr = Math.floor(m / 60);
    return m % 60 ? `${hr} ساعة و${m % 60} دقيقة` : `${hr} ساعة`;
  }

  // ---------- login ----------
  function showLogin() {
    loggedIn = false;
    clearInterval(timer);
    $('#app').hidden = true;
    $('#login').hidden = false;
    $('#pw').value = '';
  }

  $('#loginForm').addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const btn = $('#loginBtn'), err = $('#loginErr');
    err.textContent = ''; btn.disabled = true;
    try {
      await api('/api/login', { method: 'POST', body: { password: $('#pw').value } });
      await enterApp();
    } catch (e) {
      err.textContent = e.status === 429
        ? `محاولات كتير غلط. جرّب تاني بعد ${Math.ceil((e.data.wait || 300) / 60)} دقيقة.`
        : e.status === 401 ? 'الباسورد غلط.' : 'مش قادر أوصل للكمبيوتر.';
    } finally { btn.disabled = false; }
  });

  $('#logoutBtn').addEventListener('click', async () => {
    try { await api('/api/logout', { method: 'POST' }); } catch (_) { /* ignore */ }
    showLogin();
  });

  // ---------- main screen ----------
  const ACTIONS = [
    { id: 'lock', title: 'قفل الجهاز', hint: 'يقفل ويندوز فورًا' },
    { id: 'siren', title: 'صفارة', hint: 'تنبيه عالي من السماعة' },
    { id: 'screenshot', title: 'لقطة شاشة', hint: 'شوف إيه اللي على الشاشة' },
    { id: 'webcam', title: 'الكاميرا', hint: 'صورة من كاميرا الجهاز', needs: 'webcam' },
    { id: 'message', title: 'رسالة على الشاشة', hint: 'تظهر لمن يستخدم الجهاز' },
    { id: 'shutdown', title: 'إطفاء الجهاز', hint: 'يتطفي بعد ثواني', warn: true },
  ];

  function buildActions() {
    const box = $('#actions');
    box.replaceChildren();
    for (const a of ACTIONS) {
      if (a.needs && !(S && S[a.needs])) continue;
      box.append(h('button', { class: 'act' + (a.warn ? ' warn' : ''), type: 'button', 'data-act': a.id,
        onclick: () => runAction(a.id) }, h('b', null, a.title), h('span', null, a.hint)));
    }
  }

  function render() {
    if (!S) return;
    $('#hostName').textContent = S.host;
    const plate = $('#plate'), title = $('#stateTitle'), sub = $('#stateSub');
    const now = parse(S.now);
    const mine = S.armed && S.armed_at ? S.alerts.filter((a) => a.time >= S.armed_at) : [];
    let state;

    if (S.armed && mine.length) {
      state = 'alert';
      title.textContent = 'حد استخدم جهازك';
      sub.textContent = `${mine.length} تنبيه، آخرهم الساعة ${mine[mine.length - 1].time.slice(11)}`;
    } else if (S.armed) {
      state = 'armed';
      title.textContent = 'الجهاز محمي';
      sub.textContent = `الحماية شغالة من ${dur((now - parse(S.armed_at)) / 1000)}`;
    } else {
      state = 'off';
      title.textContent = 'الحماية مقفولة';
      sub.textContent = 'فعّلها قبل ما تسيب الجهاز.';
    }
    plate.dataset.state = state;

    const arm = $('#armBtn');
    arm.disabled = false;
    arm.className = 'btn arm ' + (S.armed ? 'off' : 'on');
    arm.textContent = S.armed ? 'أنا رجعت: اقفل الحماية' : 'فعّل الحماية';

    // activity list: newest first
    const list = $('#alerts');
    list.replaceChildren();
    const today = S.now.slice(0, 10);
    for (const a of [...S.alerts].reverse()) {
      const isNew = S.armed && S.armed_at && a.time >= S.armed_at;
      const t = a.time.startsWith(today) ? a.time.slice(11) : a.time.slice(5, 16);
      list.append(h('li', { class: isNew ? 'new' : 'old' },
        h('time', { datetime: a.time.replace(' ', 'T') }, t),
        h('span', { class: 'kind' }, KIND[a.kind] || a.kind),
        h('span', { class: 'detail', dir: 'auto' }, a.detail)));
    }
    $('#alertsEmpty').hidden = S.alerts.length > 0;
    $('#clearBtn').hidden = S.alerts.length === 0;

    const hasWebcam = !!S.webcam;
    if (!!$('#actions [data-act="webcam"]') !== hasWebcam || !$('#actions').children.length) buildActions();
    refreshPushStatus();
  }

  function offline() {
    const plate = $('#plate');
    plate.dataset.state = 'offline';
    $('#stateTitle').textContent = 'مش قادر أوصل للجهاز';
    $('#stateSub').textContent = 'ممكن يكون مقفول أو مفصول عن النت. بحاول تاني...';
    $('#armBtn').disabled = true;
  }

  async function poll() {
    if (document.hidden) return;
    try { S = await api('/api/state'); render(); }
    catch (e) { if (loggedIn && e.message !== 'سجّل دخول تاني') offline(); }
  }

  async function enterApp() {
    loggedIn = true;
    $('#login').hidden = true;
    $('#app').hidden = false;
    $('#iosHint').hidden = !(/iphone|ipad|ipod/i.test(navigator.userAgent) &&
      !(navigator.standalone || matchMedia('(display-mode: standalone)').matches));
    await poll();
    clearInterval(timer);
    timer = setInterval(poll, 3000);
    if (location.hash === '#screenshot') { history.replaceState(null, '', '/'); runAction('screenshot'); }
  }

  document.addEventListener('visibilitychange', () => { if (!document.hidden && loggedIn) poll(); });

  // ---------- actions ----------
  function confirmDialog(title, text, yes) {
    return new Promise((resolve) => {
      const dlg = $('#confirmDlg');
      $('#confirmTitle').textContent = title;
      $('#confirmText').textContent = text;
      $('#confirmYes').textContent = yes;
      dlg.addEventListener('close', () => resolve(dlg.returnValue === 'yes'), { once: true });
      dlg.showModal();
    });
  }

  function askMessage() {
    return new Promise((resolve) => {
      const dlg = $('#msgDlg');
      dlg.addEventListener('close', () => resolve(dlg.returnValue === 'send' ? $('#msgText').value : null), { once: true });
      dlg.showModal();
    });
  }

  async function showImage(path, note) {
    const blob = await api(path, { method: 'POST', body: {}, blob: true });
    const img = $('#shotImg');
    if (img.src.startsWith('blob:')) URL.revokeObjectURL(img.src);
    img.src = URL.createObjectURL(blob);
    $('#shotNote').textContent = note;
    $('#shotDlg').showModal();
  }

  async function runAction(id) {
    const btn = document.querySelector(`[data-act="${id}"]`);
    if (btn) btn.disabled = true;
    try {
      if (id === 'lock') { await api('/api/action/lock', { method: 'POST', body: {} }); toast('الجهاز اتقفل'); }
      else if (id === 'siren') {
        if (sirenOn) { await api('/api/action/siren_stop', { method: 'POST', body: {} }); setSiren(false); toast('الصفارة وقفت'); }
        else { await api('/api/action/siren', { method: 'POST', body: { seconds: 20 } }); setSiren(true); toast('الصفارة شغالة 20 ثانية'); }
      }
      else if (id === 'screenshot') await showImage('/api/action/screenshot', 'لو الجهاز مقفول ممكن اللقطة تطلع سودا.');
      else if (id === 'webcam') await showImage('/api/action/webcam', '');
      else if (id === 'message') {
        const text = await askMessage();
        if (text) { await api('/api/action/message', { method: 'POST', body: { text } }); toast('الرسالة ظهرت على الشاشة'); }
      }
      else if (id === 'shutdown') {
        if (await confirmDialog('تطفي الجهاز؟', 'هيتطفي بعد 5 ثواني، ومش هتقدر تشغله من الموبايل.', 'اطفيه')) {
          await api('/api/action/shutdown', { method: 'POST', body: { confirm: true } });
          toast('الجهاز بيتطفي');
        }
      }
    } catch (e) { toast(e.message, true); }
    finally { if (btn) btn.disabled = false; }
  }

  function setSiren(on) {
    sirenOn = on;
    clearTimeout(sirenTimer);
    const btn = document.querySelector('[data-act="siren"]');
    if (btn) {
      btn.classList.toggle('live', on);
      btn.querySelector('b').textContent = on ? 'وقف الصفارة' : 'صفارة';
    }
    if (on) sirenTimer = setTimeout(() => setSiren(false), 20000);
  }

  $('#armBtn').addEventListener('click', async () => {
    const arming = !S.armed;
    $('#armBtn').disabled = true;
    try {
      await api('/api/action/' + (arming ? 'arm' : 'disarm'), { method: 'POST', body: {} });
      toast(arming ? 'الحماية اتفعّلت. عندك 15 ثانية تسيب الجهاز.' : 'الحماية اتقفلت');
      await poll();
    } catch (e) { toast(e.message, true); $('#armBtn').disabled = false; }
  });

  $('#clearBtn').addEventListener('click', async () => {
    if (!(await confirmDialog('تمسح السجل؟', 'السجل هيتمسح من الجهاز نفسه كمان.', 'امسح'))) return;
    try { await api('/api/action/clear_alerts', { method: 'POST', body: {} }); await poll(); }
    catch (e) { toast(e.message, true); }
  });

  // ---------- push notifications ----------
  const u8 = (b64) => {
    const pad = '='.repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
  };
  const pushSupported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  async function currentSub() {
    if (!pushSupported()) return null;
    const reg = await navigator.serviceWorker.ready;
    return reg.pushManager.getSubscription();
  }

  async function refreshPushStatus() {
    const st = $('#pushStatus'), btn = $('#pushBtn'), test = $('#testBtn');
    if (!pushSupported()) {
      st.textContent = 'المتصفح ده مش بيدعم الإشعارات.';
      btn.hidden = true; test.hidden = true; return;
    }
    if (Notification.permission === 'denied') {
      st.textContent = 'الإشعارات متمنوعة. فعّلها من إعدادات الموبايل للتطبيق ده.';
      btn.hidden = true; test.hidden = true; return;
    }
    const sub = await currentSub().catch(() => null);
    btn.hidden = false;
    if (sub) {
      st.textContent = 'الإشعارات شغالة على الموبايل ده.';
      btn.textContent = 'اقفل الإشعارات'; btn.dataset.mode = 'off'; test.hidden = false;
    } else {
      st.textContent = 'من غير إشعارات مش هتعرف إلا لما تفتح التطبيق.';
      btn.textContent = 'فعّل الإشعارات'; btn.dataset.mode = 'on'; test.hidden = true;
    }
  }

  $('#pushBtn').addEventListener('click', async (ev) => {
    const btn = ev.currentTarget; btn.disabled = true;
    try {
      if (btn.dataset.mode === 'off') {
        const sub = await currentSub();
        if (sub) { await api('/api/push/unsubscribe', { method: 'POST', body: { endpoint: sub.endpoint } }); await sub.unsubscribe(); }
      } else {
        if ((await Notification.requestPermission()) !== 'granted') throw new Error('لازم توافق على الإشعارات.');
        const reg = await navigator.serviceWorker.ready;
        const { key } = await api('/api/push/key');
        const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: u8(key) });
        await api('/api/push/subscribe', { method: 'POST', body: { subscription: sub.toJSON() } });
        toast('الإشعارات اتفعّلت');
      }
    } catch (e) { toast(e.message, true); }
    finally { btn.disabled = false; refreshPushStatus(); }
  });

  $('#testBtn').addEventListener('click', async () => {
    try { await api('/api/push/test', { method: 'POST', body: {} }); toast('بعتّلك إشعار تجريبي'); }
    catch (e) { toast(e.message, true); }
  });

  // ---------- boot ----------
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (e) => {
      if (e.data && e.data.type === 'screenshot' && loggedIn) runAction('screenshot');
    });
  }

  (async () => {
    try { S = await api('/api/state'); await enterApp(); }
    catch (_) { if (!loggedIn) showLogin(); }
  })();
})();
