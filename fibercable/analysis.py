"""Pós-processamento: separa sentidos, alinha eventos entre medidas e calcula o relatório.

Modelo:
  * cada ficheiro .sor dá um ou dois "segmentos" (fibra, comprimento de onda, sentido);
  * em bouclage, o ficheiro da fibra A contém A em OE, o ponto de bouclage e B em EO;
  * todos os eventos são postos em distância relativa à origem (O = 0 m) e agrupados
    em "pontos" (connector O, BPEs, connector E) por proximidade;
  * quando um ponto existe noutras medidas mas o OTDR não o marcou numa delas, a perda é
    calculada na curva (LSA) e as perdas dos eventos vizinhos e as pentes são recalculadas;
  * o utilizador pode acrescentar pontos à mão (clique na curva ou sugestões do detetor).
"""
import re
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field

from .lsa import Curve

DEFAULT_THRESHOLDS = {
    'splice': 0.15,          # épissure (dB, média OE/EO)
    'connector': 0.5,        # connecteur (dB)
    'reflectance': -55.0,    # réflectance máxima (dB)
    'slope': {1310: 0.35, 1550: 0.24, 1625: 0.25},  # dB/km
    'diff_1550_1310': 0.5,   # écart 1550-1310 (dB)
}


@dataclass
class Segment:
    fiber: int
    wl: int
    direction: str            # 'OE' ou 'EO'
    file: str
    length: float             # comprimento O -> E (m)
    o_raw: float = 0.0        # posição da origem O na curva (m)
    sign: int = 1             # raw = o_raw + sign * pos
    events: list = field(default_factory=list)   # dicts com pos relativa a O
    trace: object = None
    curve: object = None
    min_pos: float = -1.0     # abaixo disto a medida não chega (fibra parceira partida)

    def raw(self, pos):
        return self.o_raw + self.sign * pos

    def rel(self, raw):
        return (raw - self.o_raw) * self.sign

    @property
    def label(self):
        return f'F{self.fiber} {self.wl} {self.direction}'


def _norm_wl(wl):
    for nominal in (1310, 1383, 1490, 1550, 1625, 1650):
        if abs(wl - nominal) < 20:
            return nominal
    return int(round(wl))


def _ev(e, pos):
    refl = e.reflectance_db if e.reflectance_db < 0 else None
    if refl is not None and e.refl_precise is not None and abs(e.refl_precise - refl) < 0.01:
        refl = round(e.refl_precise, 2)  # o FiberCable mostra a réflectance a 0,01 dB a partir do valor exato
    return {'pos': pos, 'loss': e.loss_db, 'refl': refl,
            'slope': e.slope_db_km, 'reflective': e.reflective, 'raw_pos': e.distance_m,
            'markers': e.markers, 'num': e.number, 'calc': False, 'recalc': False,
            'loss_p': e.loss_precise, 'slope_after_p': e.slope_after_precise,
            'computed': bool(getattr(e, 'computed', False))}


def detect_loop(t, include_end=False, kind=None, gap=None):
    """Índices (A, B) do bouclage, ou None se a medida não parecer em bouclage.

    A == B: bouclage direto (um só evento a meio). A < B: bouclage com bobine; A é o connecteur E
    da fibra medida e B o connecteur E da fibra parceira, simétricos em torno do meio.
    O bouclage é o centro de simetria: O e as caixas de épissura atravessadas à ida aparecem em espelho
    à volta. Entre os candidatos perto do meio fica o que tem mais eventos em espelho, para um evento
    falso perto do meio (ou na bobine de fim) não passar por bouclage.
    gap: (mín, máx) em m entre A e B, quando o tipo de bouclage é conhecido (jarretière ou bobine).
    """
    evs = [e for e in t.events if include_end or not e.is_end]
    if len(evs) < 3:
        return None
    xs = [e.distance_m for e in evs]
    first, last = xs[0], xs[-1]
    mid = (first + last) / 2
    span = last - first

    def mirrors(c, skip):
        """Eventos antes de c com espelho depois de c; None se O (ou a maioria) não tiver espelho."""
        tol = 0.005 * (c - first) + 10
        after = [x for i, x in enumerate(xs) if x > c + 1 and i not in skip]
        if not any(abs(2 * c - first - y) <= tol for y in after):
            return None
        before = [x for i, x in enumerate(xs) if first + 1 < x < c - 1 and i not in skip]
        m = sum(1 for x in before if any(abs(2 * c - x - y) <= tol for y in after))
        # num bouclage verdadeiro a maioria das caixas tem espelho; um ou dois por acaso não chegam
        return m if not before or m >= 0.4 * len(before) else None

    # regra base: evento a meio (bouclage direto) ou dois eventos simétricos em torno do meio (bobine)
    base = None
    tol = 0.03 * span + 10
    idx = min(range(len(evs)), key=lambda i: abs(xs[i] - mid))
    if kind != 'bobine' and 0 < idx < len(evs) - 1 and abs(xs[idx] - mid) < tol:
        base = (idx, idx)
    elif kind != 'direct':
        sym_tol = 0.005 * span + 10
        for a in range(1, len(evs) - 2):
            if gap and not gap[0] <= xs[a + 1] - xs[a] <= gap[1]:
                continue
            if xs[a] < mid < xs[a + 1] and abs((xs[a] + xs[a + 1]) / 2 - mid) < sym_tol:
                base = (a, a + 1)
                break
    # com O' na lista, o centro de simetria com mais espelhos ganha (um evento falso perto do meio ou na
    # bobine de fim desloca o meio, não o espelho)
    cands = []
    for i in range(1, len(evs) - 1):
        if kind != 'bobine' and abs(xs[i] - mid) < max(tol, 0.4 * span):
            m = mirrors(xs[i], {i})
            if m is not None:
                cands.append((m, (i, i) == base, (i, i)))
        if kind != 'direct' and i < len(evs) - 2 and abs((xs[i] + xs[i + 1]) / 2 - mid) < 0.4 * span and \
                (not gap or gap[0] <= xs[i + 1] - xs[i] <= gap[1]):
            m = mirrors((xs[i] + xs[i + 1]) / 2, {i, i + 1})
            if m is not None:
                cands.append((m, (i, i + 1) == base, (i, i + 1)))
    if cands:
        best = max(cands)
        if base is None or best[0] > max([c[0] for c in cands if c[2] == base], default=-1):
            return best[2]
    if base is None and gap:
        # tipo de bouclage dado pelo utilizador: o par (A, B) com a distância certa cujo O' = B + (A - O)
        # deixa uma bobine de fim parecida com a de início
        end = t.events[-1].distance_m
        pairs = []
        for a in range(1, len(evs) - 1):
            if kind == 'direct' or not gap[0] <= xs[a + 1] - xs[a] <= gap[1]:
                continue
            o2 = xs[a + 1] + xs[a] - first
            tol_o2 = 0.005 * (xs[a] - first) + 15
            if o2 > end + tol_o2:
                continue
            if not include_end and abs(end - o2) <= tol_o2:
                return None  # o OTDR parou no O' (sem bobine de fim): trata-se como fim marcado no O'
            pairs.append((abs((end - o2) - first), (a, a + 1)))
        if pairs:
            return min(pairs)[1]
    return base


def _virtual_loop(t, evs):
    """Bouclage não marcado: procura o pico reflexivo perto do meio e calcula o evento na curva."""
    if len(evs) < 2:
        return None
    mid = (evs[0].distance_m + evs[-1].distance_m) / 2
    return _virtual_event(t, evs, mid, 0.05 * (evs[-1].distance_m - evs[0].distance_m) + 20)


def _virtual_event(t, evs, target, span):
    """Conector que o OTDR não marcou perto de `target`: pico reflexivo mais próximo, perda calculada na curva.
    Acrescenta o evento a t.events e a evs e devolve o seu índice em evs."""
    from .sor import Event
    c = _curve(t)
    if not c:
        return None
    peaks = [i * c.res for i in c.find_peaks(c.idx(target - span), c.idx(target + span))]
    x = min(peaks, key=lambda p: abs(p - target)) if peaks else target
    lo = max([e.distance_m for e in evs if e.distance_m < x - 1], default=None)
    hi = min([e.distance_m for e in evs if e.distance_m > x + 1], default=None)
    r = c.event_at(x, lo, hi, reflective=bool(peaks))
    if r['loss'] is None:
        return None
    e = Event(0, x, 0.0, r['loss'], r['refl'] or 0.0, ('1' if peaks else '0') + 'F9999', 'LS', computed=True)
    t.events.append(e)
    t.events.sort(key=lambda ev: ev.distance_m)
    evs.append(e)
    evs.sort(key=lambda ev: ev.distance_m)
    return evs.index(e)


def _bobine_geometry(traces, gap=None):
    """Comprimentos típicos de uma bouclage com bobine, por λ: fibra (O -> A) e bobine (A -> B)."""
    geo = {}
    for t in traces:
        k = detect_loop(t, kind='bobine', gap=gap)
        if not k:
            continue
        evs = [e for e in t.events if not e.is_end]
        geo.setdefault(_norm_wl(t.wavelength_nm), []).append(
            (evs[k[0]].distance_m - evs[0].distance_m, evs[k[1]].distance_m - evs[k[0]].distance_m))
    out = {}
    for wl, v in geo.items():
        lf = sorted(x[0] for x in v)[len(v) // 2]
        lb = sorted(x[1] for x in v)[len(v) // 2]
        out[wl] = (lf, lb)
    return out


def _infer_bobine(t, evs, lf, lb):
    """Bouclage com bobine num ficheiro onde não é simétrico (conector não marcado, fibra partida...).
    Usa a geometria típica do cabo a partir do connecteur O. Devolve (A, B, o2, partida_em_OE) com índices
    em evs (B None se a medida acaba antes) e o2 = posição do O' (virtual se a fibra parceira estiver partida)."""
    o = evs[0].distance_m
    end = t.events[-1].distance_m
    tol = 0.005 * lf + 15

    def find(target):
        cands = [i for i, e in enumerate(evs) if abs(e.distance_m - target) <= tol]
        if cands:
            return min(cands, key=lambda i: abs(evs[i].distance_m - target))
        if end < target - tol:
            return None
        return _virtual_event(t, evs, target, tol)

    a = find(o + lf)
    if a is None:
        return None, None, None, True  # a fibra medida acaba (partida) antes do connecteur E
    b = find(evs[a].distance_m + lb)
    if b is None:
        return a, None, None, False
    la = evs[a].distance_m - o
    o2 = [i for i, e in enumerate(evs) if i > b and abs(e.distance_m - (evs[b].distance_m + la)) <= tol]
    return a, b, (evs[o2[0]].distance_m if o2 else evs[b].distance_m + la), False


def guess_direction(t, majority_origin):
    name = t.filename.upper()
    if re.search(r'(^|[^A-Z])(EO|E-O|BA)([^A-Z]|$)', name):
        return 'EO'
    if re.search(r'(^|[^A-Z])(OE|O-E|AB)([^A-Z]|$)', name):
        return 'OE'
    if majority_origin and t.origin and t.origin != majority_origin:
        return 'EO'
    return 'OE'


def _curve(t):
    if not hasattr(t, '_curve'):
        t._curve = Curve(t.trace, t.resolution_m, t.pulse_ns, t.backscatter_db) if t.trace and t.resolution_m else None
    return t._curve


def _ensure_events(t, warnings):
    """Ficheiro sem eventos (só curva): deteta-os na curva."""
    if t.events or not _curve(t):
        return
    from .sor import Event
    c = _curve(t)
    peaks = c.find_peaks(0, c.n - 5)
    if not peaks:
        return
    start = peaks[0]
    # fim de fibra: último pico com queda para o ruído a seguir
    found = c.find_steps(start + 2, peaks[-1] - 2, [start, peaks[-1]])
    evs = [(start * c.res, None, None)] + found + [(peaks[-1] * c.res, 0.0, None)]
    for n, (x, loss, refl) in enumerate(evs, 1):
        code = ('1' if refl else '0') + ('E' if n == len(evs) else 'F') + '9999'
        t.events.append(Event(n, x, 0.0, loss or 0.0, refl or 0.0, code, 'LS', computed=True))
    warnings.append(f'{t.filename}: sem eventos gravados, {len(evs)} eventos detetados na curva.')


def _mirror_score(traces, kinds):
    """Num bouclage, as caixas de épissura que a fibra medida atravessa à ida voltam a aparecer, em espelho,
    na fibra parceira à volta. Fração mediana dos eventos com espelho; perto de 0 quer dizer que o evento
    a meio da medida é só mais uma caixa e a medida é direta."""
    import bisect
    pool = sorted(e.distance_m - t.events[0].distance_m for t in traces if t.events for e in t.events if not e.is_end)
    if not pool:
        return 0.0

    def count(x, tol):
        return bisect.bisect_right(pool, x + tol) - bisect.bisect_left(pool, x - tol)
    scores = []
    for t, k in zip(traces, kinds):
        if not k:
            continue
        evs = [e for e in t.events if not e.is_end]
        o = evs[0].distance_m
        mid = (evs[k[0]].distance_m + evs[k[1]].distance_m) / 2 - o
        inner = [e.distance_m - o for e in evs[1:k[0]] + evs[k[1] + 1:]]
        tol = 0.002 * mid + 5
        # só eventos que se repetem noutras medidas (caixas); um evento falso solto não tem espelho
        inner = [x for x in inner if x > 30 and count(x, tol) >= 3]
        if inner:
            scores.append(sum(count(2 * mid - x, tol) > 0 for x in inner) / len(inner))
    if not scores:
        return 1.0  # sem eventos intermédios não há como desmentir o bouclage
    scores.sort()
    return scores[len(scores) // 2]


def build_segments(traces, mode='auto', pairs=None):
    """Transforma os traços em segmentos por fibra/sentido."""
    warnings = []
    for t in traces:
        _ensure_events(t, warnings)
    fibers = sorted({t.fiber_number for t in traces if t.fiber_number is not None})
    # tipo de bouclage decidido pela maioria dos ficheiros: num ficheiro onde falta o evento de
    # bouclage, duas épissures simétricas não podem passar por uma bobine
    # tipo de bouclage escolhido pelo utilizador, com bobines de início e de fim parecidas: jarretière de
    # ~10 cm (A e B são um só evento, a meio da curva toda) ou bobine de 2 km entre A e B
    forced = mode in ('loop_jar', 'loop_bob')
    jar = mode == 'loop_jar'
    gap = (1000.0, 3500.0) if mode == 'loop_bob' else None

    jar_centre = {}

    def jar_loop(t):
        # jarretière de ~10 cm: A e B são um só evento, no centro de simetria da curva (perto de fim/2).
        # O centro vem dos pares de eventos espelhados (caixas atravessadas na ida e na volta), assim
        # as caixas logo antes e depois da jarretière não passam por ela, mesmo que o OTDR não a marque.
        evs = [e for e in t.events if not e.is_end]
        end = t.events[-1].distance_m if t.events else 0
        w = 0.05 * end
        xs = [e.distance_m for e in evs]
        cands = {x for x in xs if abs(x - end / 2) <= w}
        cands |= {(a + b) / 2 for i, a in enumerate(xs) for b in xs[i + 1:] if abs((a + b) / 2 - end / 2) <= w}

        def score(c):
            tol = 0.005 * c + 10
            return sum(1 for x in xs if x < c - tol and any(abs(y - (2 * c - x)) <= tol for y in xs))
        best = max(sorted(cands, key=lambda c: abs(c - end / 2)), key=score, default=None)
        if best is not None and score(best) >= 2:
            tol = 0.005 * best + 10
            near = [i for i in range(1, len(evs)) if abs(xs[i] - best) <= min(tol, 30)]
            if near:
                i = min(near, key=lambda i: abs(xs[i] - best))
                return i, i
            jar_centre[id(t)] = best
            return None  # o OTDR não marcou a jarretière: evento calculado na curva no centro de simetria
        k = detect_loop(t, kind='direct')
        if k and abs(xs[k[0]] - end / 2) <= w:
            return k
        near = [i for i in range(1, len(evs)) if abs(xs[i] - end / 2) <= w]
        if near:
            i = min(near, key=lambda i: abs(xs[i] - end / 2))
            return i, i
        return k
    def fits(k, t):
        if not k or not gap:
            return bool(k)
        if k[0] == k[1]:
            return False
        evs = [e for e in t.events if not e.is_end]
        return gap[0] <= evs[k[1]].distance_m - evs[k[0]].distance_m <= gap[1]
    # primeiro a deteção pela simetria; o tipo escolhido só decide quando ela falha ou não bate com ele
    # (numa jarretière, as duas caixas logo antes e depois dela não podem passar por A e B)
    kinds = []
    for t in traces:
        if jar:
            kinds.append(jar_loop(t))
            continue
        k = detect_loop(t)
        if gap and not fits(k, t):
            k = detect_loop(t, kind='bobine', gap=gap)
        kinds.append(k)
    kind_of = {id(t): k for t, k in zip(traces, kinds)}
    n_direct = sum(1 for k in kinds if k and k[0] == k[1])
    n_bob = sum(1 for k in kinds if k and k[0] != k[1])
    loop_kind = 'bobine' if mode == 'loop_bob' or (n_bob > n_direct and not jar) else 'direct'
    geo = _bobine_geometry(traces, gap) if loop_kind == 'bobine' else {}
    if mode in ('loop_jar', 'loop_bob'):
        mode = 'loop'
    if mode == 'auto':
        loops = [k is not None for k in kinds]
        mode = 'loop' if loops and sum(loops) * 2 >= len(loops) and _mirror_score(traces, kinds) >= 0.3 else 'direct'
    if pairs is None:
        pairs = {}
        for a, b in zip(fibers[0::2], fibers[1::2]):
            pairs[a] = b
            pairs[b] = a
    first_of_pair = {f: min(f, pairs.get(f, f)) for f in fibers}

    segments = []
    for t in traces:
        if t.fiber_number is None:
            warnings.append(f'{t.filename}: número de fibra desconhecido, indique-o na lista de ficheiros.')
            continue
        if not t.events:
            warnings.append(f'{t.filename}: nenhum evento encontrado, ficheiro ignorado.')
            continue
        wl = _norm_wl(t.wavelength_nm)
        if mode == 'loop':
            o2_virtual = None
            evs = [e for e in t.events if not e.is_end]
            loop_idx = kind_of[id(t)] if forced else detect_loop(t, kind=loop_kind)
            if loop_idx is None and id(t) not in jar_centre and \
                    detect_loop(t, include_end=True, kind=loop_kind, gap=gap) is not None:
                # o OTDR parou no connecteur O' (fim de fibra marcado nele): usa-o e calcula a perda na curva
                loop_idx = detect_loop(t, include_end=True, kind=loop_kind, gap=gap)
                end = t.events[-1]
                c = _curve(t)
                end.loss_db = 0.0
                if c:
                    r = c.event_at(end.distance_m, evs[-1].distance_m if evs else None, None, reflective=True)
                    # sem bobine de fim não há curva depois do connecteur: a perda não é mensurável
                    if r['loss'] is not None and abs(r['loss']) < 3:
                        end.loss_db = r['loss']
                        end.computed = True
                    else:
                        end.loss_db = None
                end.code = end.code[0] + 'F' + end.code[2:]
                evs = [e for e in t.events]
                warnings.append(f"{t.filename}: o OTDR marcou o fim no connecteur O'" +
                                (", perda calculada na curva." if end.computed else ", sem bobine depois dele a perda não é mensurável."))
            fa = t.fiber_number
            fb = pairs.get(fa)
            g = geo.get(wl)
            if g and loop_kind == 'bobine':
                tol = 0.005 * g[0] + 15
                ok = loop_idx is not None and abs(evs[loop_idx[0]].distance_m - evs[0].distance_m - g[0]) <= tol \
                    and abs(evs[loop_idx[1]].distance_m - evs[loop_idx[0]].distance_m - g[1]) <= tol
                if ok:
                    # O' = evento a um comprimento de fibra depois de B (o OTDR pode marcar eventos na bobine de fim)
                    a, b = loop_idx
                    la_m = evs[a].distance_m - evs[0].distance_m
                    o2c = [e for e in evs[b + 1:] if abs(e.distance_m - (evs[b].distance_m + la_m)) <= tol]
                    if o2c and o2c[0] is not evs[-1]:
                        evs = [e for e in evs if e.distance_m <= o2c[0].distance_m]
                elif not evs:
                    warnings.append(f'{t.filename}: só o fim de fibra foi marcado, ficheiro ignorado.')
                    continue
                else:
                    a, b, o2_pos, broken = _infer_bobine(t, evs, *g)
                    if broken or b is None:
                        o = evs[0]
                        last = a if a is not None else len(evs) - 1
                        stop = evs[a].distance_m if a is not None else t.events[-1].distance_m
                        seg_a = Segment(fa, wl, 'OE', t.filename, stop - o.distance_m, o.distance_m, 1,
                                        trace=t, curve=_curve(t))
                        for e in evs[:last + 1]:
                            ev = _ev(e, e.distance_m - o.distance_m)
                            ev['calc'] = e.computed
                            seg_a.events.append(ev)
                        segments.append(seg_a)
                        warnings.append(f'{t.filename}: ' + ('a fibra acaba (partida?) a '
                                        f'{t.events[-1].distance_m - o.distance_m:.0f} m de O, só o início é medido.'
                                        if broken else 'a medida acaba antes da fibra parceira, só o sentido OE é usado.'))
                        continue
                    loop_idx = (a, b)
                    if abs(evs[-1].distance_m - o2_pos) > 1:
                        warnings.append(f'{t.filename}: a fibra parceira acaba (partida?) antes do connecteur O\', '
                                        'posições EO calculadas com o comprimento das outras fibras.')
                    evs = [e for e in evs if e.distance_m <= o2_pos + 1]
                    o2_virtual = o2_pos
            if loop_idx is None:
                end = t.events[-1].distance_m
                li = (_virtual_event(t, evs, jar_centre[id(t)], 30) if id(t) in jar_centre else
                      _virtual_event(t, evs, end / 2, 0.05 * end)) if jar else _virtual_loop(t, evs)
                loop_idx = None if li is None else (li, li)
                if li is None:
                    warnings.append(f'{t.filename}: ponto de bouclage não encontrado, ficheiro ignorado.')
                    continue
                warnings.append(f'{t.filename}: o OTDR não marcou o bouclage, evento calculado na curva.')
            if fb is None:
                warnings.append(f'{t.filename}: fibra {fa} sem parceira de bouclage, só o sentido OE é usado.')
            la, lb = loop_idx
            shared = la == lb  # bouclage direto: um só evento para as duas fibras
            o, loop_a, loop_b = evs[0], evs[la], evs[lb]
            if o2_virtual is None:
                # O' é o espelho de O; eventos depois dele (bobine de fim, fantasmas) não são da fibra
                want = loop_b.distance_m + (loop_a.distance_m - o.distance_m)
                tol_o2 = 0.005 * (loop_a.distance_m - o.distance_m) + 15
                o2c = [e for e in evs[lb + 1:] if abs(e.distance_m - want) <= tol_o2]
                o2e = min(o2c, key=lambda e: abs(e.distance_m - want)) if o2c else None
                if o2e is None and forced and want < t.events[-1].distance_m - tol_o2:
                    # tipo de bouclage dado: há bobine de fim, por isso O' existe mesmo sem o OTDR o marcar
                    i2 = _virtual_event(t, evs, want, tol_o2)
                    o2e = evs[i2] if i2 is not None else None
                    if o2e is not None:
                        warnings.append(f"{t.filename}: o OTDR não marcou o connecteur O', calculado na curva.")
                if o2e is not None:
                    evs = [e for e in evs if e.distance_m <= o2e.distance_m]
            o2_pos = o2_virtual if o2_virtual is not None else evs[-1].distance_m
            seg_a = Segment(fa, wl, 'OE', t.filename, loop_a.distance_m - o.distance_m, o.distance_m, 1,
                            trace=t, curve=_curve(t))
            for i, e in enumerate(evs[:la + 1]):
                ev = _ev(e, e.distance_m - o.distance_m)
                ev['calc'] = e.computed
                if shared and i == la and first_of_pair[fa] != fa:
                    ev.update(loss=None, refl=None)  # bouclage atribuído à primeira fibra do par
                seg_a.events.append(ev)
            segments.append(seg_a)
            if fb is None:
                continue
            seg_b = Segment(fb, wl, 'EO', t.filename, o2_pos - loop_b.distance_m, o2_pos, -1,
                            trace=t, curve=_curve(t))
            for i in range(lb, len(evs)):
                ev = _ev(evs[i], o2_pos - evs[i].distance_m)
                ev['calc'] = evs[i].computed
                if shared and i == lb and first_of_pair[fb] != fb:
                    ev.update(loss=None, refl=None)
                seg_b.events.append(ev)
            seg_b.events.reverse()
            if o2_virtual is not None and t.events[-1].distance_m < o2_pos - 1:
                seg_b.min_pos = o2_pos - t.events[-1].distance_m + 10
            segments.append(seg_b)
        else:
            origins = [x.origin for x in traces if x.origin]
            majority = max(set(origins), key=origins.count) if origins else ''
            d = guess_direction(t, majority)
            evs = list(t.events)
            o, end = evs[0], evs[-1]
            length = end.distance_m - o.distance_m
            if d == 'OE':
                seg = Segment(t.fiber_number, wl, d, t.filename, length, o.distance_m, 1, trace=t, curve=_curve(t))
            else:
                seg = Segment(t.fiber_number, wl, d, t.filename, length, end.distance_m, -1, trace=t, curve=_curve(t))
            for e in evs:
                seg.events.append(_ev(e, seg.rel(e.distance_m)))
            seg.events.sort(key=lambda ev: ev['pos'])
            segments.append(seg)
    return mode, segments, warnings


def cluster_points(segments, tol, manual=()):
    """Agrupa as posições de todos os eventos (e dos pontos manuais) em pontos comuns.

    Começa pelo sítio onde há mais eventos juntos e vai descendo, para que eventos soltos entre dois
    pontos não os colem num só grupo comprido."""
    items = sorted((ev['pos'], i) for i, s in enumerate(segments) for ev in s.events)
    pos = [p for p, _ in items]
    alive = [True] * len(pos)
    out = []
    while True:
        best, best_n, j, n = None, 0, 0, 0
        for i in range(len(pos)):  # janela [pos[i], pos[i] + tol] com mais eventos ainda livres
            if i and alive[i - 1]:
                n -= 1
            while j < len(pos) and pos[j] <= pos[i] + tol:
                n += alive[j]
                j += 1
            if alive[i] and n > best_n:
                best, best_n = i, n
        if best is None:
            break
        win = sorted(pos[k] for k in range(best, len(pos)) if alive[k] and pos[k] <= pos[best] + tol)
        mid = win[len(win) // 2]
        members = [k for k in range(len(pos)) if alive[k] and abs(pos[k] - mid) <= tol]
        for k in members:
            alive[k] = False
        vals = [pos[k] for k in members]
        out.append({'center': sum(vals) / len(vals), 'count': len({items[k][1] for k in members}),
                    'manual': False})
    for m in manual:
        m = float(m)
        near = [c for c in out if abs(c['center'] - m) <= tol]
        if near:
            min(near, key=lambda c: abs(c['center'] - m))['manual'] = True
        else:
            out.append({'center': m, 'count': 0, 'manual': True})
    return sorted(out, key=lambda c: c['center'])


def confirm_points(segments, clusters, tol, max_pairs=64):
    """Separa os pontos do cabo dos eventos falsos (ruído, fantasmas, curvaturas de uma só fibra).

    Uma épissure ou conector está na mesma distância em todas as fibras do cabo. Por isso um ponto fica
    se o OTDR o marcou em (quase) todas as medidas, ou se a curva mostra um degrau nesse sítio na grande
    maioria das fibras. Um evento marcado só em algumas fibras e sem degrau nas outras não é um ponto do
    cabo. Devolve a lista de clusters recusados."""
    n_seg = len(segments)
    cand = [i for i, c in enumerate(clusters) if not c['manual']]
    if len(cand) <= 2 or n_seg < 2:
        return []
    lengths = sorted(s.length for s in segments)
    length = lengths[len(lengths) // 2]
    # conectores O e E: os pontos mais perto do início e do comprimento da fibra; nada fica para lá deles
    first = min(cand, key=lambda k: abs(clusters[k]['center']))
    last = min(cand, key=lambda k: abs(clusters[k]['center'] - length))
    rejected = [clusters[k] for k in cand if clusters[k]['center'] < clusters[first]['center'] or
                clusters[k]['center'] > clusters[last]['center']]
    cand = [k for k in cand if clusters[first]['center'] <= clusters[k]['center'] <= clusters[last]['center']]
    centers = [c['center'] for c in clusters]
    assign(segments, centers, tol)
    otdr = {k: sum(1 for s in segments if k in s.by_point and not s.by_point[k]['computed']) for k in cand}
    # num lote pequeno, metade das medidas são uma ou duas fibras: só "todas" dispensa a prova na curva
    sure = 0.5 * n_seg if n_seg >= 20 else n_seg
    doubt = [k for k in cand if k not in (first, last) and otdr[k] < sure]
    if n_seg >= 20:  # num lote grande, um evento visto numa só medida nem se avalia
        rejected += [clusters[k] for k in doubt if clusters[k]['count'] <= 1]
        doubt = [k for k in doubt if clusters[k]['count'] > 1]
    by_fiber = {}
    for s in segments:
        by_fiber.setdefault(s.fiber, []).append(s)
    # amostra de fibras/λ espalhada pelo lote (o cálculo na curva é o mais lento)
    pairs = sorted({(s.fiber, s.wl) for s in segments})
    if len(pairs) > max_pairs:
        step = len(pairs) / max_pairs
        pairs = [pairs[int(i * step)] for i in range(max_pairs)]
    pairs = set(pairs)
    sel = [s for s in segments if (s.fiber, s.wl) in pairs and s.curve]
    est = {id(s): [_estimate_pos(s, k, by_fiber[s.fiber], centers[k]) for k in range(len(centers))] for s in sel}
    accepted = set(cand) - set(doubt)
    # dos mais vistos para os menos vistos. As janelas LSA acabam só nos pontos já aceites (e nunca nos
    # eventos duvidosos), para o degrau de uma épissure vizinha não contar aqui e um evento falso ao lado
    # não encurtar a medida
    for k in sorted(doubt, key=lambda k: -clusters[k]['count']):
        by_pair = {}
        for s in sel:
            pos = est[id(s)][k]
            if pos < max(-1, s.min_pos) or pos > s.length + 1:
                continue
            raw = s.raw(pos)
            near = [s.raw(est[id(s)][a]) for a in accepted]
            near = [x for x in near if abs(x - raw) > 0.5]
            lo = max([x for x in near if x < raw], default=None)
            hi = min([x for x in near if x > raw], default=None)
            loss = s.curve.event_at(raw, lo, hi)['loss']
            ev = s.by_point.get(k)
            if loss is None and ev and not ev['computed']:
                loss = ev['loss']
            if loss is not None:
                by_pair.setdefault((s.fiber, s.wl), []).append(loss)
        per = [sum(v) / len(v) for v in by_pair.values()]
        if len(per) < 3:  # a curva não deixa medir: decide o OTDR
            (accepted.add(k) if otdr[k] >= 0.5 * n_seg else rejected.append(clusters[k]))
            continue
        # com os dois sentidos, a média OE/EO de uma épissure é positiva (os gainers anulam-se); num só
        # sentido um gainer também é uma épissure, por isso conta o tamanho do degrau e não o sinal
        both = sum(len(v) > 1 for v in by_pair.values()) * 2 >= len(by_pair)
        vals = sorted(per) if both else sorted(abs(v) for v in per)
        median = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
        share = sum(v >= (0 if both else 0.005) for v in vals) / len(vals)
        clusters[k]['evidence'] = {'median': round(median, 4), 'share': round(share, 2), 'n': len(per)}
        if median >= 0.01 and share >= 0.75:
            accepted.add(k)
        else:
            rejected.append(clusters[k])
    for s in segments:
        s.by_point = {}
    return rejected


def assign(segments, centers, tol):
    """Para cada segmento, associa cada evento ao ponto mais próximo (no máximo um por ponto)."""
    for s in segments:
        s.by_point = {}
        if not centers:
            continue
        for ev in s.events:
            k = min(range(len(centers)), key=lambda i: abs(centers[i] - ev['pos']))
            if abs(centers[k] - ev['pos']) > tol * 2:
                continue
            prev = s.by_point.get(k)
            if prev is None or abs(centers[k] - ev['pos']) < abs(centers[k] - prev['pos']):
                s.by_point[k] = ev


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _val_p(cell, d):
    v = cell.get(d)
    p = cell.get(d + '_p')
    return p if v is not None and p is not None and abs(p - v) < 0.001 else v


def _budget(points, sections, key):
    """Bilan de liaison: média dos bilans OE e EO; cada um soma as perdas dos eventos nesse sentido
    e pente x comprimento de cada troço medido nesse sentido (fórmula que reproduz o FiberCable)."""
    totals = []
    for d in ('OE', 'EO'):
        ev = sum(_val_p(p['data'][key], d) or 0 for p in points)
        att = 0.0
        for k, s in enumerate(sections):
            a, b = points[k]['data'][key].get(d + '_raw'), points[k + 1]['data'][key].get(d + '_raw')
            length = abs(b - a) if a is not None and b is not None else s['len_m']
            att += (_val_p(s['data'][key], d) or 0) * length / 1000
        totals.append(ev + att)
    return sum(totals) / 2


def _avg_precise(cell):
    """Média OE/EO; usa os valores com todas as casas quando o ficheiro os tem (como o FiberCable)."""
    vals = []
    for d in ('OE', 'EO'):
        if cell.get(d) is None:
            continue
        p = cell.get(d + '_p')
        vals.append(p if p is not None and abs(p - cell[d]) < 0.001 else cell[d])
    return _mean(vals)


def r3(v):
    """Arredonda a 3 casas, metade para cima (como o relatório de origem)."""
    if v is None:
        return None
    return float(Decimal(repr(v)).quantize(Decimal('0.001'), rounding=ROUND_HALF_UP))


def _estimate_pos(s, k, segments, center):
    """Posição esperada do ponto k nesta medida: mesma fibra noutro λ, depois no outro sentido."""
    same = [o for o in segments if o is not s and o.fiber == s.fiber and k in o.by_point and not o.by_point[k]['calc']]
    for pref in (lambda o: o.direction == s.direction, lambda o: o.wl == s.wl, lambda o: True):
        c = [o.by_point[k]['pos'] for o in same if pref(o)]
        if c:
            return sum(c) / len(c)
    return center


def _neighbours(s, raw, exclude=None):
    """Eventos (reais e calculados) da mesma curva imediatamente antes e depois de raw."""
    xs = sorted({e.distance_m for e in s.trace.events} |
                {ev['raw_pos'] for ev in s.events} |
                {ev['raw_pos'] for ev in s.by_point.values()})
    xs = [x for x in xs if abs(x - raw) > 0.5 and x != exclude]
    lower = max([x for x in xs if x < raw], default=None)
    upper = min([x for x in xs if x > raw], default=None)
    return lower, upper


def fill_missing(segments, centers, manual_flags, only=None):
    """Calcula na curva os eventos que o OTDR não marcou e recalcula as perdas vizinhas."""
    for s in segments:
        c = s.curve
        if not c:
            continue
        added = []
        for k, center in enumerate(centers):
            if k in s.by_point or (only is not None and k not in only):
                continue
            pos = _estimate_pos(s, k, segments, center)
            if pos < max(-1, s.min_pos) or pos > s.length + 1:
                continue
            raw = s.raw(pos)
            lo, hi = _neighbours(s, raw)
            ev = c.event_at(raw, lo, hi)
            if ev['loss'] is None:
                continue
            s.by_point[k] = {'pos': pos, 'loss': ev['loss'], 'refl': ev['refl'], 'slope': None,
                             'reflective': ev['reflective'], 'raw_pos': raw, 'markers': None,
                             'num': None, 'calc': True, 'recalc': False, 'manual': manual_flags[k]}
            added.append(raw)
        if not added:
            continue
        # eventos do OTDR ao lado de um evento acrescentado: janelas LSA passam a acabar nele
        for k, ev in s.by_point.items():
            if ev['calc'] or ev['loss'] is None or not ev['markers']:
                continue
            lo, hi = _neighbours(s, ev['raw_pos'])
            if lo not in added and hi not in added:
                continue
            new = c.event_at(ev['raw_pos'], lo, hi, markers=ev['markers'], reflective=ev['reflective'])
            if new['loss'] is not None and abs(new['loss'] - ev['loss']) >= 0.0005:
                ev['otdr_loss'] = ev['loss']
                ev['loss'] = new['loss']
                ev['recalc'] = True


def section_slope(s, k, centers):
    """Pente do troço k -> k+1 nesta medida (dB/km): (valor do OTDR, valor com todas as casas)."""
    a, b = s.by_point.get(k), s.by_point.get(k + 1)
    if a and b and not a['calc'] and not b['calc']:
        lo, hi = sorted([a['raw_pos'], b['raw_pos']])
        between = [e for e in s.trace.events if lo + 0.5 < e.distance_m < hi - 0.5]
        if not between:
            ev = b if s.direction == 'OE' else a  # OTDR guarda a pente no evento do fim do troço
            first = a if s.direction == 'OE' else b  # e, no bloco privado, no evento do início
            if ev['slope']:  # 0 = o OTDR não calculou (troço curto): calcula-se na curva
                return ev['slope'], first.get('slope_after_p')
    c = s.curve
    if not c:
        return None, None
    if min(centers[k], centers[k + 1]) < s.min_pos or max(centers[k], centers[k + 1]) > s.length + 30:
        return None, None  # troço fora do que a medida alcança
    ra = a['raw_pos'] if a else s.raw(centers[k])
    rb = b['raw_pos'] if b else s.raw(centers[k + 1])
    lo, hi = sorted([ra, rb])
    lo_ev = a if ra == lo else b
    refl = bool(lo_ev and lo_ev['reflective'])
    i0 = c.idx(lo) + c.gap_after(refl)
    i1 = c.idx(hi) - 2
    if i1 - i0 < 5:
        return None, None
    v = c.slope_db_km(i0, i1)
    return (None, None) if v is None else (round(v, 3), v)


def _near(dct, center, tol):
    """Valor de um dicionário indexado pela posição do ponto (em metros, tolerante)."""
    best = None
    for key, v in (dct or {}).items():
        try:
            d = abs(float(key) - center)
        except ValueError:
            continue
        if d <= tol and (best is None or d < best[0]):
            best = (d, v)
    return None if best is None else best[1]


def suggestions(segments, centers, tol, threshold):
    """Eventos que a curva mostra mas que nenhuma medida (nem o utilizador) marcou."""
    raw_found = []
    for s in segments:
        c = s.curve
        if not c:
            continue
        lo, hi = sorted([s.raw(0), s.raw(s.length)])
        known = [c.idx(e.distance_m) for e in s.trace.events] + [c.idx(ev['raw_pos']) for ev in s.by_point.values()]
        for x, loss, refl in c.find_steps(c.idx(lo) + 2, c.idx(hi) - 2, known, threshold):
            pos = s.rel(x)
            if any(abs(pos - ct) <= tol for ct in centers):
                continue
            raw_found.append({'pos': pos, 'seg': s.label, 'loss': loss, 'refl': refl})
    raw_found.sort(key=lambda d: d['pos'])
    groups = []
    for d in raw_found:
        if groups and d['pos'] - groups[-1][-1]['pos'] <= tol:
            groups[-1].append(d)
        else:
            groups.append([d])
    out = []
    for g in groups:
        losses = [d['loss'] for d in g if d['loss'] is not None]
        big = any(abs(v) >= 2 * threshold for v in losses) or any(d['refl'] for d in g)
        same_sign = len(losses) >= 3 and (sum(v > 0 for v in losses) >= 0.8 * len(losses) or
                                         sum(v < 0 for v in losses) >= 0.8 * len(losses))
        if big or same_sign:  # um degrau isolado perto do limiar é quase sempre ruído
            out.append({'pos': round(sum(d['pos'] for d in g) / len(g), 1),
                        'found_in': [f"{d['seg']}: {d['loss']:+.3f} dB" for d in g]})
    return out


def analyse(traces, mode='auto', tol=20.0, thresholds=None, point_names=None, excluded=None, overrides=None,
            manual_points=None, auto_fill=True, detect_threshold=0.05, hide_rare=True):
    """Análise completa.

    point_names / overrides / excluded são indexados pela posição do ponto (m desde a origem), para
    continuarem válidos quando se acrescentam ou retiram pontos:
      point_names: {'186': 'BPE OPEX2126'}
      overrides: {'186|7|1310|OE': 0.05}   (None apaga o valor)
      excluded: [2300.5, ...]              (pontos fantasma a ignorar)
      manual_points: [2300.0, ...]          (pontos acrescentados à mão)
    """
    overrides = overrides or {}
    th = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        th.update({k: v for k, v in thresholds.items() if v is not None})
    mode, segments, warnings = build_segments(traces, mode)
    if not segments:
        return {'error': 'Nenhuma medida utilizável.', 'warnings': warnings}

    manual = [float(m) for m in (manual_points or [])]
    clusters = cluster_points(segments, tol, manual)
    excl = [float(x) for x in (excluded or [])]
    # um ponto acrescentado à mão prevalece sobre um "Ignorar" anterior no mesmo sítio
    removed = [c for c in clusters if not c['manual'] and any(abs(c['center'] - x) <= 1.0 for x in excl)]
    # eventos que não são pontos do cabo ficam escondidos como os ignorados, e "Repor" volta a mostrá-los
    rare = []
    if hide_rare:
        kept = [c for c in clusters if c not in removed]
        rare = confirm_points(segments, kept, tol)
        removed += rare
    clusters = [c for c in clusters if c not in removed]
    centers = [c['center'] for c in clusters]
    assign(segments, centers, tol)
    flags = [c['manual'] for c in clusters]
    # sem preenchimento automático, só os pontos acrescentados à mão são calculados na curva
    fill_missing(segments, centers, flags, None if auto_fill else {k for k, m in enumerate(flags) if m})

    fibers = sorted({s.fiber for s in segments})
    wls = sorted({s.wl for s in segments})
    ref_fiber = fibers[0]
    seg_of = {(s.fiber, s.wl, s.direction): s for s in segments}
    npts = len(centers)

    # distâncias cumulativas: média das medidas da fibra de referência
    cum = []
    for k in range(npts):
        vals = [s.by_point[k]['pos'] for s in segments if s.fiber == ref_fiber and k in s.by_point]
        cum.append(_mean(vals) if vals else centers[k])
    if cum:
        cum = [c - cum[0] for c in cum]
        cum[0] = 0.0

    tr0 = traces[0]
    default_names = ['Connecteur O'] + [f'Epissure N° {i}' for i in range(1, npts - 1)] + ['Connecteur E']
    names = []
    for k in range(npts):
        n = _near(point_names, centers[k], tol) if isinstance(point_names, dict) else None
        names.append(n or default_names[k])
    kinds = ['connector' if i in (0, npts - 1) else 'splice' for i in range(npts)]

    # perdas por ponto
    points = []
    for k in range(npts):
        p = {'index': k, 'key': round(centers[k], 1), 'name': names[k], 'kind': kinds[k], 'cum_m': cum[k],
             'len_m': cum[k] - cum[k - 1] if k else 0.0, 'center': centers[k], 'manual': clusters[k]['manual'],
             'hits': sum(1 for s in segments if k in s.by_point and not s.by_point[k]['calc']),
             'calc': sum(1 for s in segments if k in s.by_point and s.by_point[k]['calc']), 'data': {}}
        for f in fibers:
            for wl in wls:
                cell = {}
                for d in ('OE', 'EO'):
                    s = seg_of.get((f, wl, d))
                    ev = s.by_point.get(k) if s else None
                    cell[d] = ev['loss'] if ev else None
                    cell[d + '_refl'] = ev['refl'] if ev else None
                    cell[d + '_missing'] = bool(s) and ev is None
                    cell[d + '_calc'] = bool(ev and ev['calc'])
                    cell[d + '_recalc'] = bool(ev and ev['recalc'])
                    if ev and ev['recalc']:
                        cell[d + '_otdr'] = ev.get('otdr_loss')
                    cell[d + '_raw'] = ev['raw_pos'] if ev else None
                    cell[d + '_has'] = ev is not None
                    cell[d + '_p'] = ev.get('loss_p') if ev and not ev['calc'] and not ev['recalc'] else None
                    for key, v in overrides.items():
                        parts = str(key).split('|')
                        if len(parts) == 4 and parts[1] == str(f) and parts[2] == str(wl) and parts[3] == d:
                            try:
                                if abs(float(parts[0]) - centers[k]) <= 1.0:
                                    cell[d] = None if v in (None, '') else float(v)
                                    cell[d + '_edited'] = True
                                    cell[d + '_p'] = None
                            except ValueError:
                                pass
                # evento presente mas sem valor mensurável num sentido: sem média (como o FiberCable)
                if any(cell[d + '_has'] and cell[d] is None for d in ('OE', 'EO')):
                    cell['avg'] = None
                else:
                    cell['avg'] = r3(_avg_precise(cell))
                p['data'][f'{f}|{wl}'] = cell
        points.append(p)

    # pentes por troço
    sections = []
    for k in range(npts - 1):
        sec = {'index': k + 1, 'from': names[k], 'to': names[k + 1], 'len_m': cum[k + 1] - cum[k], 'data': {}}
        for f in fibers:
            for wl in wls:
                cell = {}
                for d in ('OE', 'EO'):
                    s = seg_of.get((f, wl, d))
                    cell[d], cell[d + '_p'] = section_slope(s, k, centers) if s else (None, None)
                cell['avg'] = r3(_avg_precise(cell))
                sec['data'][f'{f}|{wl}'] = cell
        sections.append(sec)

    # bilan, médias, hors normes
    fiber_summary = {}
    out = {'splices': [], 'slopes': [], 'refl': [], 'budget': [], 'diff': []}
    for f in fibers:
        for wl in wls:
            key = f'{f}|{wl}'
            sl = [(s['data'][key]['avg'], s['len_m']) for s in sections]
            budget = _budget(points, sections, key)
            splice_avgs = [p['data'][key]['avg'] for p in points if p['kind'] == 'splice' and p['data'][key]['avg'] is not None]
            fiber_summary[key] = {
                'budget': budget,
                'splice_mean': _mean(splice_avgs),
                'splice_max': max(splice_avgs) if splice_avgs else None,
                'slope_mean': _mean([a for a, _ in sl]),
            }
            slope_th = th['slope'].get(wl) if isinstance(th['slope'], dict) else th['slope']
            for p in points:
                c = p['data'][key]
                limit = th['connector'] if p['kind'] == 'connector' else th['splice']
                c['out'] = c['avg'] is not None and c['avg'] > limit
                for d in ('OE', 'EO'):
                    c[d + '_out'] = c[d] is not None and c[d] > limit
                if c['out']:
                    out['splices'].append({'point': p['name'], 'fiber': f, 'value': c['avg'], 'wl': wl,
                                           '_k': (0, wl, p['index'], f)})
                for d in ('OE', 'EO'):
                    r = c[d + '_refl']
                    if p['kind'] == 'splice':
                        # uma épissure não deve refletir: qualquer réflectance é hors norme (como o FiberCable)
                        c[d + '_refl_out'] = r is not None
                        if r is not None:
                            out['splices'].append({'point': f"Réflectance N° {p['index']}", 'fiber': f, 'value': r,
                                                   'wl': wl, '_k': (1, wl, p['index'], f)})
                        continue
                    c[d + '_refl_out'] = r is not None and r > th['reflectance']
                    if c[d + '_refl_out']:
                        out['refl'].append({'point': f"{p['name']} ({d})", 'fiber': f, 'value': r, 'wl': wl,
                                            '_k': (wl, p['index'], d != 'OE', f)})
            for s in sections:
                c = s['data'][key]
                lim = slope_th
                c['out'] = lim is not None and c['avg'] is not None and c['avg'] > lim
                for d in ('OE', 'EO'):
                    c[d + '_out'] = lim is not None and c[d] is not None and c[d] > lim
                if c['out']:
                    out['slopes'].append({'section': s['index'], 'fiber': f, 'value': c['avg'], 'wl': wl,
                                          '_k': (wl, s['index'], f)})
        if 1310 in wls and 1550 in wls:
            for p in points:
                a = p['data'][f'{f}|1310']['avg']; b = p['data'][f'{f}|1550']['avg']
                d = r3(b - a) if a is not None and b is not None else None
                p['data'][f'{f}|diff'] = d
                if d is not None and d > th['diff_1550_1310']:
                    out['diff'].append({'point': p['name'], 'fiber': f, 'value': d})

    # mesma ordem que o FiberCable: comprimento de onda, ponto, (sentido), fibra
    for k in ('splices', 'slopes', 'refl'):
        out[k].sort(key=lambda o: o['_k'])
        for o in out[k]:
            o.pop('_k')

    info = {
        'cable': tr0.cable_id, 'origin': tr0.origin, 'end': tr0.end, 'operator': tr0.operator,
        'otdr': tr0.otdr, 'otdr_sn': tr0.otdr_sn, 'module': tr0.module, 'module_sn': tr0.module_sn,
        'supplier': tr0.supplier, 'software': tr0.software, 'timestamp': tr0.timestamp,
        'per_wl': {}
    }
    for wl in wls:
        t = next(s.trace for s in segments if s.wl == wl)
        info['per_wl'][wl] = {'wavelength': t.wavelength_nm, 'ior': t.ior, 'pulse_ns': t.pulse_ns,
                              'backscatter': t.backscatter_db, 'averages': t.averages}

    traces_out = []
    for t in traces:
        step = max(1, len(t.trace) // 4000)
        calc = [{'pos': ev['raw_pos'], 'loss': ev['loss'], 'point': points[k]['name']}
                for s in segments if s.trace is t for k, ev in s.by_point.items() if ev['calc']]
        traces_out.append({'file': t.filename, 'fiber': t.fiber_number, 'wl': _norm_wl(t.wavelength_nm),
                           'res': t.resolution_m * step, 'y': [round(v, 3) for v in t.trace[::step]],
                           'events': [{'pos': e.distance_m, 'loss': e.loss_db, 'refl': e.reflectance_db,
                                       'code': e.code} for e in t.events],
                           'calc': calc})

    sugg = suggestions(segments, centers, tol, detect_threshold) if detect_threshold else []

    return {
        'mode': mode, 'tol': tol, 'fibers': fibers, 'wls': wls, 'points': points, 'sections': sections,
        'removed_points': [{'center': c['center'], 'hits': c['count'], 'rare': c in rare}
                           for c in sorted(removed, key=lambda c: c['center'])],
        'suggestions': sugg, 'summary': fiber_summary, 'out': out, 'thresholds': th,
        'info': info, 'warnings': warnings, 'traces': traces_out,
        'segments': [{'fiber': s.fiber, 'wl': s.wl, 'dir': s.direction, 'file': s.file,
                      'length': s.length, 'n_events': len(s.events), 'o_raw': s.o_raw, 'sign': s.sign,
                      'lo': min(s.raw(0), s.raw(s.length)), 'hi': max(s.raw(0), s.raw(s.length)),
                      'calc': [points[k]['name'] for k in range(npts) if k in s.by_point and s.by_point[k]['calc']],
                      'missing': [points[k]['name'] for k in range(npts) if k not in s.by_point]}
                     for s in segments],
    }
