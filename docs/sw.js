// Guarda a app no telemóvel para funcionar sem rede no terreno.
const CACHE = 'fibercable-7d82873c9a3b';
const FILES = ["./", "icon-180.png", "icon-192.png", "icon-512.png", "index.html", "manifest.webmanifest", "py_files.json", "pyodide/pyodide-lock.json", "pyodide/pyodide.asm.js", "pyodide/pyodide.asm.wasm", "pyodide/pyodide.js", "pyodide/python_stdlib.zip", "pyshim.js", "wheels/et_xmlfile-2.0.0-py3-none-any.zip", "wheels/openpyxl-3.1.5-py2.py3-none-any.zip", "worker.js"];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then(r => r || fetch(e.request)));
});
