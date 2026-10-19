// Versão para telemóvel: as chamadas /api/... da página vão para o worker (Pyodide) em vez de um servidor.
(function () {
  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost')) {
    // versão nova no site: a app instalada descarrega-a sozinha e passa a usá-la sem reinstalar
    const hadController = !!navigator.serviceWorker.controller;
    navigator.serviceWorker.register('sw.js').then(reg => {
      document.addEventListener('visibilitychange', () => { if (!document.hidden) reg.update().catch(() => {}); });
    }).catch(() => {});
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      if (!hadController) return;
      const reset = document.getElementById('btnReset');
      if (!reset || reset.disabled) { location.reload(); return; }  // nada carregado: atualiza já
      // com ficheiros carregados não se perde o trabalho: um botão deixa atualizar quando der jeito
      if (document.getElementById('updBar')) return;
      const bar = document.createElement('div');
      bar.id = 'updBar';
      bar.style.cssText = 'position:fixed;bottom:12px;left:50%;transform:translateX(-50%);z-index:30;background:#1d2733;color:#fff;padding:8px 14px;border-radius:20px;font-size:14px;display:flex;gap:10px;align-items:center';
      bar.innerHTML = '<span>Nova versão da app disponível.</span><button class="small">Atualizar</button>';
      bar.querySelector('button').onclick = () => location.reload();
      document.body.appendChild(bar);
    });
  }
  const worker = new Worker('worker.js');
  const pending = {};
  let seq = 0;
  const engine = text => { const el = document.getElementById('engine'); if (el) el.textContent = text; };
  worker.onmessage = e => {
    const m = e.data;
    if (m.type === 'progress') { engine(m.text); return; }
    const done = pending[m.id]; delete pending[m.id]; done(m);
  };
  const call = (op, data = {}, transfer = []) => new Promise(res => {
    const id = ++seq; pending[id] = res; worker.postMessage({ id, op, ...data }, transfer);
  });
  const XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (url, opt = {}) => {
    if (typeof url !== 'string' || !url.startsWith('/api/')) return nativeFetch(url, opt);
    let m;
    if (url === '/api/defaults') m = await call('defaults');
    else if (url === '/api/upload') {
      const files = [];
      for (const f of opt.body.getAll('files')) files.push({ name: f.name, buf: await f.arrayBuffer() });
      m = await call('upload', { cfg: opt.body.get('config'), files }, files.map(f => f.buf));
    } else if (url.startsWith('/api/analyse/')) m = await call('analyse', { cfg: opt.body });
    else if (url.startsWith('/api/export/')) m = await call('export', { cfg: opt.body });
    else return new Response('{"error":"rota desconhecida"}', { status: 404 });
    return m.bin ? new Response(new Blob([m.bin], { type: XLSX }), { status: m.status, headers: { 'Content-Type': XLSX } })
                 : new Response(m.json, { status: m.status, headers: { 'Content-Type': 'application/json' } });
  };
})();
