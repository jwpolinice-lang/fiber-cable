"""Curvas tratadas: reescreve o .sor original com a tabela de eventos da análise (pontos do cabo,
posições alinhadas, perdas recalculadas e eventos acrescentados)."""
import binascii
import struct

from .sor import C_KM_PER_US, Reader, _read_map

# blocos privados com uma cópia da tabela de eventos do OTDR: deixariam de bater com a nova tabela
PRIVATE_EVENT_BLOCKS = ('JDSUEvenementsMTS',)


def _cstr(s):
    return s.encode('latin-1', 'replace') + b'\0'


def curve_events(t, res):
    """Eventos da curva t depois do tratamento, por ordem de distância no ficheiro."""
    key_pts = res['points']
    sections = res['sections']
    out = []
    for s in res['segments']:
        if s['file'] != t.filename:
            continue
        key = f"{s['fiber']}|{s['wl']}"
        d = s['dir']
        n = len(key_pts)
        for p in key_pts:
            c = p['data'].get(key) or {}
            if not c.get(d + '_has'):
                continue
            k = p['index']
            # pente do troço antes do evento, no sentido do ficheiro
            if s['sign'] > 0:
                sec = sections[k - 1] if k > 0 else None
            else:
                sec = sections[k] if k < n - 1 else None
            slope = sec['data'][key][d] if sec else None
            out.append({'pos': c[d + '_raw'], 'loss': c[d], 'refl': c.get(d + '_refl'),
                        'slope': slope, 'name': p['name'], 'fiber': s['fiber'], 'dir': d,
                        'calc': bool(c.get(d + '_calc')), 'recalc': bool(c.get(d + '_recalc')),
                        'edited': bool(c.get(d + '_edited'))})
    out.sort(key=lambda e: e['pos'])
    merged = []
    for e in out:
        # bouclage direto: o mesmo evento serve as duas fibras, fica o da primeira (como no dossier)
        if merged and abs(e['pos'] - merged[-1]['pos']) <= 1.0:
            if merged[-1]['loss'] is None:
                merged[-1].update(loss=e['loss'], refl=e['refl'])
            continue
        merged.append(e)
    end = next((e for e in reversed(t.events) if e.is_end), None)
    if end and (not merged or end.distance_m > merged[-1]['pos'] + 1.0):
        merged.append({'pos': end.distance_m, 'loss': end.loss_db, 'refl': end.reflectance_db or None,
                       'slope': None, 'name': 'Fin de fibre', 'fiber': None, 'dir': '', 'calc': False,
                       'recalc': False, 'edited': False, 'end': True, 'code': end.code})
    return merged


def _orig_event(t, pos):
    best = min(t.events, key=lambda e: abs(e.distance_m - pos), default=None)
    return best if best and abs(best.distance_m - pos) <= 1.0 else None


def _keyevents_block(data, blocks, t, evs):
    off, size, _ = blocks['KeyEvents']
    factor = 1e-4 * C_KM_PER_US / t.ior * 1000  # unidades de 100 ps -> metros
    r = Reader(data, off)
    if t.version == 2:
        r.cstr()
    # fim da tabela original: o resto do bloco (perda total, ORL...) fica como estava
    n = r.u16()
    for _ in range(n):
        r.p += 2 + 4 + 2 + 2 + 4 + 6 + 2
        if t.version == 2:
            r.p += 20
        r.cstr()
    trailer = data[r.p:off + size]

    body = bytearray()
    if t.version == 2:
        body += _cstr('KeyEvents')
    body += struct.pack('<H', len(evs))
    for i, e in enumerate(evs, 1):
        o = _orig_event(t, e['pos'])
        if e.get('end'):
            code = e['code']
        else:
            refl = e['refl'] is not None and e['refl'] < 0
            tag = 'A' if e['calc'] else ('M' if (e['recalc'] or e['edited']) else 'F')
            code = ('1' if refl else '0') + tag + (o.code[2:] if o and not e['calc'] else '9999')
        method = (o.method if o else 'LS')[:2].ljust(2)
        slope = e['slope'] if e['slope'] is not None else (o.slope_db_km if o else 0.0)
        loss = e['loss'] if e['loss'] is not None else 0.0
        refl = e['refl'] if e['refl'] is not None and e['refl'] < 0 else 0.0
        body += struct.pack('<HIhhi', i, int(round(e['pos'] / factor)), int(round(slope * 1000)),
                            int(round(loss * 1000)), int(round(refl * 1000)))
        body += code[:6].ljust(6, '9').encode('latin-1') + method.encode('latin-1')
        if t.version == 2:
            marks = o.markers if o and o.markers and not e['calc'] else [0.0] * 5
            body += struct.pack('<5i', *[int(round(m / factor)) for m in marks])
        name = e['name'] if not e.get('end') else (o.comment if o else '')
        body += _cstr(name)
    return bytes(body) + trailer


def processed_sor(data, t, res):
    """Bytes do .sor tratado para a curva t (lida de `data`)."""
    version, blocks = _read_map(data)
    evs = curve_events(t, res)
    order = sorted(blocks.items(), key=lambda kv: kv[1][0])
    new = []
    for name, (off, size, rev) in order:
        if name in PRIVATE_EVENT_BLOCKS:
            continue
        if name == 'KeyEvents':
            body = _keyevents_block(data, blocks, t, evs)
        else:
            body = data[off:off + size]
        new.append((name, rev, body))

    def map_block(sizes):
        m = bytearray()
        entries = b''.join(_cstr(n) + struct.pack('<HI', rv, sz) for (n, rv, _), sz in zip(new, sizes))
        rev = struct.unpack_from('<H', data, 4 if version == 2 else 0)[0]
        head = (b'Map\0' if version == 2 else b'')
        total = len(head) + 2 + 4 + 2 + len(entries)
        m += head + struct.pack('<HIH', rev, total, len(new) + 1) + entries
        return bytes(m)

    sizes = [len(b) for _, _, b in new]
    # o Cksum é o último bloco: CRC-16 CCITT de tudo o que vem antes do valor
    out = bytearray(map_block(sizes) + b''.join(b for _, _, b in new))
    if new and new[-1][0] == 'Cksum':
        crc = binascii.crc_hqx(bytes(out[:-2]), 0xFFFF)
        struct.pack_into('<H', out, len(out) - 2, crc)
    return bytes(out)

