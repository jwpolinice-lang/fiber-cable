"""Gera a pasta docs/ (versão para telemóvel): a app inteira em ficheiros estáticos, para alojar num site (https) e abrir no telemóvel.

Uso:  python build_telemovel.py
O cálculo corre no próprio browser (Pyodide), por isso o site só serve ficheiros e as medidas não saem do telemóvel.
"""
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / 'web_src'
OUT = ROOT / 'docs'  # o GitHub Pages publica esta pasta

HEAD = '''<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#1f4e79">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Fiber Cable">
<link rel="apple-touch-icon" href="icon-180.png">
<link rel="icon" href="icon-192.png">
'''


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / 'pyodide').mkdir(parents=True)
    (OUT / 'wheels').mkdir()

    html = (ROOT / 'static' / 'index.html').read_text(encoding='utf-8')
    html = html.replace('</title>', '</title>\n' + HEAD.rstrip(), 1)
    marker = '<script>\nconst $ ='
    assert marker in html, 'index.html mudou: não encontrei o script principal'
    html = html.replace(marker, '<script src="pyshim.js"></script>\n' + marker, 1)
    (OUT / 'index.html').write_text(html, encoding='utf-8')

    for f in ('worker.js', 'pyshim.js', 'manifest.webmanifest', 'icon-180.png', 'icon-192.png', 'icon-512.png'):
        shutil.copy(SRC / f, OUT / f)
    for f in (SRC / 'vendor' / 'pyodide').iterdir():
        if f.name != 'README.md':
            shutil.copy(f, OUT / 'pyodide' / f.name)
    wheels = sorted(f.name for f in (SRC / 'vendor' / 'wheels').iterdir())
    for w in wheels:
        shutil.copy(SRC / 'vendor' / 'wheels' / w, OUT / 'wheels' / w)

    files = {f'fibercable/{p.name}': p.read_text(encoding='utf-8')
             for p in sorted((ROOT / 'fibercable').glob('*.py'))}
    (OUT / 'py_files.json').write_text(json.dumps({'files': files, 'wheels': wheels}), encoding='utf-8')

    assets = sorted(str(p.relative_to(OUT)).replace('\\', '/') for p in OUT.rglob('*') if p.is_file())
    h = hashlib.sha256()
    for a in assets:
        h.update(a.encode()); h.update((OUT / a).read_bytes())
    sw = (SRC / 'sw.js').read_text(encoding='utf-8')
    sw = sw.replace('__VERSION__', h.hexdigest()[:12]).replace('__FILES__', json.dumps(['./'] + assets))
    (OUT / 'sw.js').write_text(sw, encoding='utf-8')
    (OUT / '.nojekyll').write_text('')
    size = sum((OUT / a).stat().st_size for a in assets) / 1e6
    print(f'docs/ gerada: {len(assets) + 1} ficheiros, {size:.1f} MB')


if __name__ == '__main__':
    main()
