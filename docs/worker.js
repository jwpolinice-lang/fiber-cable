// Motor de cálculo da versão para telemóvel: o mesmo código Python da app, a correr no browser com o Pyodide.
importScripts('pyodide/pyodide.js');

const say = text => postMessage({ type: 'progress', text });

const ready = (async () => {
  say('A preparar o motor de cálculo...');
  const py = await loadPyodide({ indexURL: new URL('pyodide/', location.href).href });
  const bundle = await (await fetch('py_files.json')).json();
  for (const [path, src] of Object.entries(bundle.files)) {
    py.FS.mkdirTree('/app/' + path.split('/').slice(0, -1).join('/'));
    py.FS.writeFile('/app/' + path, src);
  }
  for (const w of bundle.wheels) {
    py.FS.writeFile('/tmp/' + w, new Uint8Array(await (await fetch('wheels/' + w)).arrayBuffer()));
  }
  py.globals.set('WHEELS', py.toPy(bundle.wheels));
  py.runPython(`
import sys, zipfile
for w in WHEELS:
    zipfile.ZipFile('/tmp/' + w).extractall('/app/site')
sys.path[:0] = ['/app', '/app/site']
import fibercable.webapi
`);
  py.FS.mkdirTree('/up');
  say('');
  return py;
})();

onmessage = async e => {
  const m = e.data;
  let out;
  try {
    const py = await ready;
    const api = py.pyimport('fibercable.webapi');
    let r;
    if (m.op === 'defaults') r = api.defaults();
    else if (m.op === 'upload') {
      say(`A calcular ${m.files.length} ficheiro(s)...`);
      m.files.forEach((f, i) => py.FS.writeFile(`/up/${String(i).padStart(5, '0')}_${f.name.replace(/[\\/]/g, '_')}`, new Uint8Array(f.buf)));
      r = api.upload('/up', m.cfg);
    } else if (m.op === 'analyse') { say('A recalcular...'); r = api.reanalyse(m.cfg); }
    else if (m.op === 'export') { say('A gerar o Excel...'); r = api.export(m.cfg); }
    const [status, body, name] = r.toJs();
    r.destroy();
    out = typeof body === 'string' || body == null ? { status, json: body ?? '{"error":"Volte a carregar os ficheiros."}' }
                                                     : { status, bin: body, name };
  } catch (err) {
    out = { status: 500, json: JSON.stringify({ error: 'Erro no cálculo: ' + String(err.message || err).split('\n').slice(-3).join(' ') }) };
  }
  say('');
  postMessage({ type: 'result', id: m.id, ...out }, out.bin ? [out.bin.buffer] : []);
};
