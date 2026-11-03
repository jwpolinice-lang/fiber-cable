"""Dossier FiberCable em texto (Dossier.txt): o mesmo formato que o FiberCable exporta, em UTF-16 com
tabulações, com as tabelas de atenuações, pentes, réflectances, comprimentos e ficheiros por fibra."""
import datetime

# partes sem dados da medida OTDR (PMD, CD, AP, LTS, inspeção): ficam iguais às do FiberCable
_MID = [
    'Mesures PMD',
    'Appareil de mesure\t',
    'Numéro de série\t',
    'Module\t',
    'N° de série du module\t',
    'Nombre de fibres\t0',
    'Longueur des fibres (km)\t',
    'Couplage\t\t',
    'Méthode de mesure\t\t',
    'Type de seuils\t\t\t',
    '\t\t\t\t',
    'Valeurs seuils\t\t\t\t',
    'Seuils actifs\t\t\t\t',
    'Référence\t',
    '',
    '',
    '',
    'Mesures CD',
    'Appareil de mesure\t',
    'Numéro de série\t',
    'Module\t',
    'N° de série du module\t',
    'Nombre de fibres\t0',
    "Formule d'approximation\t\t",
    "Longueur d'onde de référence\t",
    'Valeurs normalisées\t\t',
    "Longueur d'onde de départ (nm)\t",
    "Longueur d'onde d'arrivée (nm)\t",
    'Nombre de mesures\t',
    'Incrément (nm)\t',
    'Seuils\t\t\t',
    'Longueur (km)\t',
    '',
    '',
    'Mesures AP',
    'Appareil de mesure\t',
    'Numéro de série\t',
    'Module\t',
    'N° de série du module\t',
    'Nombre de fibres\t0',
    'Valeurs normalisées\t\t',
    "Longueur d'onde de départ (nm)\t",
    "Longueur d'onde d'arrivée (nm)\t",
    'Nombre de mesures\t',
    'Incrément (nm)\t',
    'Longueur (km)\t',
    '',
    '',
    '',
    '',
    '',
    'Mesures LTS',
    'Appareil de mesure\t',
    'Numéro de série\t',
    'Module\t',
    'N° de série du module\t',
    'Nombre de fibres\t',
    'Seuils actifs\t\t',
    'Comparaison lambdas (dB)\t',
    'Nombre de lambdas\t',
    "Longueur d'onde (nm)\t",
    "Perte d'insertion (dB)\t",
    'ORL max (dB)\t',
    'Atténuations :\t>0\t1',
    '',
    '',
    '',
    '',
    "Résultats de l'inspection",
    'Appareil de mesure\t',
    'Numéro de série\t',
    'Microcode\t',
    'Profil\t',
    'Nombre de fichiers\t0',
    'Nombre de zones\t',
    'Nom de la zone\t',
    'Diameter interne (µm)\t',
    'Diameter externe (µm)\t',
    '',
    '',
    '',
    '',
    '',
    '',
    '',
]

_TAIL = [
    'PMD',
    'ID Câble\tID Fibre\tDélai PMD (ps)\tCoeff PMD (ps/km½)\tDélai PMD 2 (ps/nm)\tPMD Coeff 2 (ps/nm.km)\tFichier\tDate\tCommentaire\tCommentaire\t',
    '',
    '',
    '',
    'CD',
    'ID Câble\tID Fibre\tLambda zéro (nm)\tPente zéro (ps/km.nm²)\t (nm)\t (nm)\t (nm)\t (nm)\t (nm)\t (nm)\t (m)\t (m)\t (m)\t (m)\t (m)\t (m)\t (ps/km)\t (ps/km)\t (ps/km)\t (ps/km)\t (ps/km)\t (ps/km)\t\t\t\t\t\tFichier\tDate\tCommentaire\t',
    '\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t\t',
    '',
    '',
    'AP',
    'ID Câble\tID Fibre\tFichier\tDate\tCommentaire\t',
    '\t\t\t\t\t',
    '',
    '',
    'LTS',
    'ID Câble\tID Fibre\tCouleur\tFichier\tLongueur (m)\t',
    '\t\t\t\t\t',
    '',
    '',
    'INSPECTION',
    '\t\t\t\t\t\t\t\t\tTest\t',
    '\t\t\t\t\t\t\t\t\tBON = 1\t',
]

_TAIL_STARTS = (('PMD', 3, 3, 4), ('CD', 8, 28, 2), ('AP', 13, 3, 2), ('LTS', 18, 6, 0), ('INSPECTION', 23, 11, 5))


def _n(v, dec=3):
    return '' if v is None else f'{v:.{dec}f}'.replace('.', ',')


def _paris(ts):
    """Hora de França (CET/CEST) sem depender da base de fusos horários."""
    utc = datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=ts)

    def last_sunday(month):
        d = datetime.datetime(utc.year, month, 31)
        return d - datetime.timedelta(days=(d.weekday() + 1) % 7)
    summer = last_sunday(3).replace(hour=1) <= utc < last_sunday(10).replace(hour=1)
    return utc + datetime.timedelta(hours=2 if summer else 1)


def build_dossier_txt(res, traces, meta=None):
    """Bytes do Dossier.txt (UTF-16 LE com BOM, linhas CRLF) a partir do resultado de analyse()."""
    meta = meta or {}
    info = res['info']
    th = res['thresholds']
    cable = meta.get('cable') or info['cable'] or ''
    points, sections = res['points'], res['sections']
    npts = len(points)
    wls = res['wls']
    rows = [(f, wl) for wl in wls for f in res['fibers']]
    per = info['per_wl']
    seg = {(s['fiber'], s['wl'], s['dir']): s for s in res['segments']}
    by_file = {t.filename: t for t in traces}

    def wlv(key, wl, dec, scale=1):
        v = (per.get(wl) or {}).get(key)
        return '' if v is None else (_n(v * scale, dec) if dec is not None else str(v))

    def th_wl(name, wl, fallback):
        if wl not in wls:
            return fallback
        v = th[name].get(wl) if isinstance(th[name], dict) else th[name]
        return fallback if v is None else _n(v, 2 if name == 'reflectance' else 3)

    def three(name, c1625):
        return '\t'.join(th_wl(name, wl, c1625 if wl == 1625 else '') for wl in (1310, 1550, 1625))

    head = [
        'Version tableau dossier\t15\tAPP FIBER CABLE\t\t\t\t\t\t\t\t\t',
        f"Origine\t{meta.get('origin') or info['origin'] or ''}",
        f"Extrémité\t{meta.get('end') or info['end'] or ''}",
        f"Appareil de mesure\t{info['otdr'] or ''}\t",
        f"Numéro de série\t{info['otdr_sn'] or ''}\t",
        'Fabricant du câble\t',
        f"Type de câble\t{meta.get('cable_type') or ''}",
        'Fabricant de la fibre\t',
        f"Type de fibre\t{meta.get('fiber_type') or ''}",
        f"Chef de projet\t{meta.get('manager') or ''}",
        f"Raccordement\t{meta.get('company') or ''}",
        f"Module\t{info['module'] or ''}\t",
        f"N° de série du module\t{info['module_sn'] or ''}\t",
        f"Impulsion (ns)\t{wlv('pulse_ns', wls[0], None)}\t\t{wlv('pulse_ns', 1310, None)}\t{wlv('pulse_ns', 1550, None)}\t",
        f"Indice de réfraction\t{wlv('ior', wls[0], 5)}\t\t{wlv('ior', 1310, 5)}\t{wlv('ior', 1550, 5)}\t",
        f"Nombre de fibres\t{len(res['fibers'])}",
        f"Nombre d'épissures\t{npts}",
        f"Nombre de longueurs\t{max(0, npts - 1)}",
        'Nombre de bobines amorce\t1\t0\t1\t\t2',
        'Détection des contraintes (dB)\t0,500',
        'Lambda\t1310 nm\t1550 nm\t1625 nm',
        "Longueur d'onde réelle (nm)\t" + '\t'.join(wlv('wavelength', wl, 0) for wl in (1310, 1550)) + '\t',
        'Coefficient de rétrodiffusion (dB)\t' + '\t'.join(wlv('backscatter', wl, 2) for wl in (1310, 1550)) + '\t',
        'Seuils connecteurs (dB)\t' + three('connector', '1,000'),
        'Seuils atténuations (dB)\t' + three('splice', '0,400'),
        'Seuils pentes (dB/km)\t' + three('slope', '0,300'),
        'Seuils moyenne connecteurs (dB)\t' + three('connector', '1,000'),
        'Seuils moyenne atténuations (dB)\t' + three('splice', '0,200'),
        'Seuils moyenne pentes (dB/km)\t' + three('slope', '0,280'),
        'Seuils réflectances (dB)\t' + three('reflectance', '-40,00'),
        'Seuils moyenne réflectances ()\t0\t0\t0',
        'Amorces supprimées\tOui\t1',
        'Connecteurs supprimés\tNon\t0\t\t\t\t\t\t\tPerte totale : Connecteurs inclus\t0\t0',
        'Sens\tO<->E\t0',
        'Bilan max (dB)\t9999\t9999\t9999',
        'Bilan min (dB)\t0\t0\t0',
        'ORL max (dB)\t0\t0\t0',
    ]
    lines = [None] + head + _MID   # a linha 1 (índice das tabelas) escreve-se no fim
    starts = {}

    def table(name, title, header, sub, data, width):
        lines.append(title)
        lines.append(header)
        lines.append(sub)
        starts[name] = (len(lines) + 1, len(data), width)
        lines.extend(data)
        lines.extend(['', ''])

    def ids(f, wl):
        return [cable, str(f), str(wl)]

    def row(vals):
        return '\t'.join(vals) + '\t'

    sens3 = 'Sens O-->E\tSens O<--E\tMoyenne\t'

    # atenuações: um trio (O->E, O<-E, média) por ponto, como no FiberCable
    data = []
    for f, wl in rows:
        v = []
        for p in points:
            c = p['data'].get(f'{f}|{wl}') or {}
            v += [_n(c.get('OE')), _n(c.get('EO')), _n(c.get('avg'))]
        data.append(row(ids(f, wl) + v))
    table('att', 'Atténuations (dB)',
          'ID Câble\tID Fibre\tLambda\t' + sens3 * (npts + 1) + 'Sens O-->E\tSens O<--E\tSens O-->E\tSens O<--E\t',
          '\t\t\t' + ''.join(f'{k}\t{k}\t{k}\t' for k in range(1, npts + 1))
          + 'Perte totale\t' * 3 + 'Connecteurs\t' * 4, data, 3)

    nsec = len(sections)
    sub3 = '\t\t\t' + ''.join(f'{k}\t{k}\t{k}\t' for k in range(1, nsec + 1))
    data = []
    for f, wl in rows:
        v = []
        for s in sections:
            c = s['data'].get(f'{f}|{wl}') or {}
            v += [_n(c.get('OE')), _n(c.get('EO')), _n(c.get('avg'))]
        data.append(row(ids(f, wl) + v))
    table('slope', 'Pentes (dB/km)', 'ID Câble\tID Fibre\tLambda\t' + sens3 * nsec, sub3, data, 3)

    data = []
    for f, wl in rows:
        v = []
        for p in points:
            c = p['data'].get(f'{f}|{wl}') or {}
            v += [_n(c.get('OE_refl'), 2), _n(c.get('EO_refl'), 2)]
        data.append(row(ids(f, wl) + v))
    table('refl', 'Réflectances (dB)', 'ID Câble\tID Fibre\tLambda\t' + 'Sens O-->E\tSens O<--E\t' * npts,
          '\t\t\t' + ''.join(f'{k}\t{k}\t' for k in range(1, npts + 1)), data, 2)

    # comprimentos de cada troço medidos em cada sentido (posições dos eventos na curva)
    data = []
    for f, wl in rows:
        v = []
        for k in range(nsec):
            ls = []
            for d in ('OE', 'EO'):
                s = seg.get((f, wl, d))
                if not s:
                    ls.append(None)
                    continue
                pos = []
                for p in (points[k], points[k + 1]):
                    c = p['data'].get(f'{f}|{wl}') or {}
                    raw = c.get(d + '_raw')
                    pos.append((raw - s['o_raw']) * s['sign'] if raw is not None else p['center'])
                ls.append(abs(pos[1] - pos[0]))
            got = [x for x in ls if x is not None]
            v += [_n(ls[0]), _n(ls[1]), _n(sum(got) / len(got) if got else None)]
        data.append(row(ids(f, wl) + v))
    table('len', 'Longueurs (m)', 'ID Câble\tID Fibre\tLambda\t' + sens3 * nsec, sub3, data, 3)

    # ficheiros: numa bouclage o par de ficheiros fica na primeira fibra, a segunda fica vazia
    data, listed = [], set()
    for f, wl in rows:
        files = [(seg.get((f, wl, d)) or {}).get('file') for d in ('OE', 'EO')]
        if res.get('mode') == 'loop' and files[0] in listed:
            files = [None, None]
        listed.update(x for x in files if x)
        dates = []
        for x in files:
            t = by_file.get(x)
            dates.append(_paris(t.timestamp).strftime('%d/%m/%Y %H:%M:%S') if t and t.timestamp else '')
        v = [x or '' for x in files] + ['', ''] + dates + ['', ''] + [''] * 4 + ['', '']
        data.append(row(ids(f, wl) + v))
    table('info', 'Informations mesures',
          'ID Câble\tID Fibre\tLambda\tFichier\t\t\t\tDate\t\t\t\tCommentaire\t\t\t\tORL\tORL\t',
          '\t\t\t' + 'O-->E\tO<--E\tO<-PM\tPM->E\t' * 3 + 'O->E\tO<-E\t', data, 4)

    t0 = len(lines) + 1
    lines.extend(_TAIL)
    idx = [starts['att'], starts['slope'], starts['refl'], starts['len']]
    idx += [(t0 + off, a, b) for _, off, a, b in _TAIL_STARTS[:4]]
    idx += [starts['info'], (t0 + _TAIL_STARTS[4][1], _TAIL_STARTS[4][2], _TAIL_STARTS[4][3])]
    lines[0] = ('Débuts tableaux (Lignes, Colonnes, Largeur) (Atténuation, Pente, Longueurs, PMD, CD) : \t'
                + '\t'.join(f'{a}\t{b}\t{c}' for a, b, c in idx))
    return '﻿'.encode('utf-16-le') + ('\r\n'.join(lines) + '\r\n').encode('utf-16-le')
