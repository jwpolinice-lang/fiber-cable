"""Ponte para a versão para telemóvel: o worker JavaScript chama estas funções no Pyodide."""
import json
import os

from . import service
from .analysis import DEFAULT_THRESHOLDS

TRACES = []
JOB = 'local'


def _dump(obj):
    return json.dumps(obj, default=str)


def defaults():
    return 200, _dump(DEFAULT_THRESHOLDS)


def upload(folder, cfg_json):
    """Lê os ficheiros escritos pelo worker em `folder` (prefixo 'n_' para manter nomes repetidos)."""
    global TRACES
    items = []
    for f in sorted(os.listdir(folder)):
        with open(os.path.join(folder, f), 'rb') as fh:
            items.append((f.split('_', 1)[1], fh.read()))
        os.remove(os.path.join(folder, f))
    traces, errors = service.read_uploads(items)
    if not traces:
        return 400, _dump({'error': 'Nenhum ficheiro .sor válido.', 'errors': errors})
    TRACES = traces
    cfg, th = service.parse_config(json.loads(cfg_json or '{}'))
    res = service.run(TRACES, cfg, th)
    res.update(job=JOB, errors=errors, files=service.files_info(TRACES))
    return 200, _dump(res)


def reanalyse(cfg_json):
    if not TRACES:
        return 404, _dump({'error': 'Volte a carregar os ficheiros.'})
    cfg, th = service.parse_config(json.loads(cfg_json or '{}'))
    res = service.run(TRACES, cfg, th)
    res.update(job=JOB, files=service.files_info(TRACES))
    return 200, _dump(res)


def export(cfg_json):
    if not TRACES:
        return 404, None, None
    cfg, th = service.parse_config(json.loads(cfg_json or '{}'))
    data, name = service.export(TRACES, cfg, th)
    return 200, data, name
