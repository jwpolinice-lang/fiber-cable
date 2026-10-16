"""APP FIBER CABLE: app web local para pós-processamento de medidas OTDR (.sor).

Arranque:  python app.py   e abrir http://127.0.0.1:5000
"""
import io
import json
import uuid
import webbrowser
import threading

from flask import Flask, jsonify, request, send_file, send_from_directory

from fibercable import service
from fibercable.analysis import DEFAULT_THRESHOLDS

app = Flask(__name__, static_folder='static')
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024
JOBS = {}  # id -> lista de traços carregados (em memória)


def _config():
    cfg = request.get_json(silent=True) or {}
    if request.form.get('config'):
        cfg = json.loads(request.form['config'])
    return service.parse_config(cfg)


@app.get('/')
def index():
    return send_from_directory('static', 'index.html')


@app.get('/api/defaults')
def defaults():
    return jsonify(DEFAULT_THRESHOLDS)


@app.post('/api/upload')
def upload():
    traces, errors = service.read_uploads([(f.filename, f.read()) for f in request.files.getlist('files')])
    if not traces:
        return jsonify({'error': 'Nenhum ficheiro .sor válido.', 'errors': errors}), 400
    job = uuid.uuid4().hex
    JOBS[job] = traces
    cfg, th = _config()
    res = service.run(JOBS[job], cfg, th)
    res['job'] = job
    res['errors'] = errors
    res['files'] = service.files_info(traces)
    return jsonify(res)


@app.post('/api/analyse/<job>')
def reanalyse(job):
    if job not in JOBS:
        return jsonify({'error': 'Sessão expirada, volte a carregar os ficheiros.'}), 404
    cfg, th = _config()
    res = service.run(JOBS[job], cfg, th)
    res['job'] = job
    res['files'] = service.files_info(JOBS[job])
    return jsonify(res)


@app.post('/api/export/<job>')
def export(job):
    if job not in JOBS:
        return jsonify({'error': 'Sessão expirada, volte a carregar os ficheiros.'}), 404
    cfg, th = _config()
    data, name = service.export(JOBS[job], cfg, th)
    return send_file(io.BytesIO(data), as_attachment=True, download_name=name,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


if __name__ == '__main__':
    threading.Timer(1.0, lambda: webbrowser.open('http://127.0.0.1:5000')).start()
    app.run(host='127.0.0.1', port=5000, debug=False)
