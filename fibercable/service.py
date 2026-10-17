"""Operações da app sem servidor: usadas pelo Flask (app.py) e pela versão para telemóvel (webapi.py)."""
import datetime
import io
import zipfile

from .analysis import analyse
from .report import build_workbook
from .sor import parse_sor


def read_uploads(items):
    """items: lista de (nome, bytes). Aceita .sor e .zip com .sor lá dentro. Devolve (traços, erros)."""
    traces, errors = [], []
    for name, data in items:
        if name.lower().endswith('.zip'):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    inner = [(n.rsplit('/', 1)[-1], z.read(n)) for n in z.namelist()
                             if n.lower().endswith('.sor') and not n.rsplit('/', 1)[-1].startswith('._')]
            except zipfile.BadZipFile:
                errors.append(f'{name}: zip inválido')
                continue
            if not inner:
                errors.append(f'{name}: o zip não tem ficheiros .sor')
            items_t, errs = read_uploads(inner)
            traces += items_t; errors += errs
            continue
        try:
            traces.append(parse_sor(data, filename=name))
        except Exception as e:  # ficheiro corrompido ou formato não suportado
            errors.append(f'{name}: {e}')
    traces.sort(key=lambda t: (t.fiber_number or 0, t.wavelength_nm, t.filename))
    return traces, errors


def parse_config(cfg):
    """Configuração enviada pela página -> (cfg, limiares)."""
    cfg = cfg or {}
    th = cfg.get('thresholds') or {}
    if 'slope' in th:
        th['slope'] = {int(k): float(v) for k, v in th['slope'].items() if v not in (None, '')}
    for k in ('splice', 'connector', 'reflectance', 'diff_1550_1310'):
        if th.get(k) not in (None, ''):
            th[k] = float(th[k])
        else:
            th.pop(k, None)
    return cfg, th


def run(traces, cfg, th):
    for t in traces:
        t.fiber_override = (cfg.get('fiber_numbers') or {}).get(t.filename)
        if t.fiber_override is not None:
            t.fiber_override = int(t.fiber_override)
    return analyse(traces, mode=cfg.get('mode', 'auto'), tol=float(cfg.get('tol') or 20),
                   thresholds=th, point_names=cfg.get('names'), excluded=cfg.get('excluded'),
                   overrides=cfg.get('overrides'), manual_points=cfg.get('manual'),
                   auto_fill=cfg.get('auto_fill', True),
                   hide_rare=not cfg.get('show_rare'),
                   detect_threshold=float(cfg.get('detect_threshold') or 0) if cfg.get('detect', True) else 0)


def files_info(traces):
    return [{'file': t.filename, 'fiber': t.fiber_number, 'wl': t.wavelength_nm} for t in traces]


def export(traces, cfg, th):
    """Devolve (bytes do xlsx, nome do ficheiro)."""
    res = run(traces, cfg, th)
    meta = {k: v for k, v in (cfg.get('meta') or {}).items() if v}
    if meta.get('date'):
        meta['date'] = datetime.date.fromisoformat(meta['date'])
    name = f"Dossier_{meta.get('cable') or res['info']['cable'] or 'cable'}.xlsx"
    return build_workbook(res, meta), name
