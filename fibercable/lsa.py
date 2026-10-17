"""Cálculos na curva OTDR: perda de evento pelo método LSA (duas retas), pente e detecção de eventos.

Com os ficheiros Viavi de referência, a perda LSA calculada com as janelas guardadas pelo OTDR
reproduz o valor do OTDR a ±0,003 dB.
"""
import bisect
import math

V_M_PER_NS = 0.2998 / 1.468 / 2  # meia velocidade na fibra: duração do impulso -> metros (aprox.)


class Curve:
    """Curva com somas acumuladas para ajustes de reta rápidos."""

    def __init__(self, y, res_m, pulse_ns=0, backscatter_db=0):
        self.y = y
        self.res = res_m
        self.n = len(y)
        self.pulse_ns = pulse_ns
        self.bs = backscatter_db
        sy = sxy = 0.0
        self.cy = [0.0]; self.cxy = [0.0]
        for i, v in enumerate(y):
            sy += v; sxy += i * v
            self.cy.append(sy); self.cxy.append(sxy)

    def idx(self, raw_m):
        return raw_m / self.res

    def pulse_pts(self):
        return max(1.0, self.pulse_ns * V_M_PER_NS / self.res) if self.res else 1.0

    def fit(self, i0, i1):
        """Reta por mínimos quadrados em [i0, i1] (índices inteiros). Devolve (declive, ordenada)."""
        i0 = max(0, int(i0)); i1 = min(self.n - 1, int(i1))
        n = i1 - i0 + 1
        if n < 3:
            return None
        sx = (i0 + i1) * n / 2.0
        sxx = (i1 * (i1 + 1) * (2 * i1 + 1) - (i0 - 1) * i0 * (2 * i0 - 1)) / 6.0
        sy = self.cy[i1 + 1] - self.cy[i0]
        sxy = self.cxy[i1 + 1] - self.cxy[i0]
        den = n * sxx - sx * sx
        if not den:
            return None
        k = (n * sxy - sx * sy) / den
        return k, (sy - k * sx) / n

    def slope_db_km(self, i0, i1):
        f = self.fit(i0, i1)
        return None if f is None else -f[0] / self.res * 1000

    def loss_lsa(self, ie, left, right):
        a = self.fit(*left); b = self.fit(*right)
        if a is None or b is None:
            return None
        return (a[0] * ie + a[1]) - (b[0] * ie + b[1])

    def guard_before(self):
        """Pontos a deixar antes de um evento: a subida do pico começa antes da posição marcada."""
        return int(round(self.pulse_pts())) + 3

    def gap_after(self, reflective):
        p = self.pulse_pts()
        return int(round(2 * p + (6 if reflective else 3)))

    def windows(self, ie, lower_idx=None, upper_idx=None, markers=None, reflective=False, max_len=400):
        """Janelas esquerda/direita para um evento no índice ie, limitadas pelos eventos vizinhos."""
        g_after = self.gap_after(reflective)
        if markers and markers[0] and markers[3]:
            l0, l1, r0, r1 = [m / self.res for m in markers[:4]]
        else:
            l0, l1 = ie - max_len, ie - 1
            r0, r1 = ie + g_after, ie + g_after + max_len
        if lower_idx is not None:
            l0 = max(l0, lower_idx + self.gap_after(False))
        if upper_idx is not None:
            r1 = min(r1, upper_idx - self.guard_before())
            if r1 - r0 < 5 and not (markers and markers[0] and markers[3]):
                # evento seguinte muito perto (ex.: épissure a 50 m do connecteur E): janela curta mais encostada
                r0 = min(r0, ie + int(round(2 * self.pulse_pts() + 1)))
                r1 = upper_idx - 2
        return (int(round(l0)), int(round(l1))), (int(round(r0)), int(round(r1)))

    def reflectance(self, ie, left, reflective_min_db=0.2):
        """Réflectance (dB) a partir da altura do pico acima da retrodifusão; None se não reflexivo."""
        a = self.fit(*left)
        if a is None:
            return None
        base = a[0] * ie + a[1]
        span = int(self.gap_after(True)) + 2
        i0 = max(0, int(ie) - 1); i1 = min(self.n, int(ie) + span)
        if i1 <= i0:
            return None
        h = max(self.y[i0:i1]) - base
        if h < reflective_min_db or not self.pulse_ns:
            return None
        # -0,31 dB: calibração com os ficheiros Viavi MTS 4000 (o pico amostrado e o BC do OTDR diferem)
        return self.bs + 10 * math.log10((10 ** (h / 5) - 1) * self.pulse_ns) - 0.31

    def event_at(self, raw_m, lower_raw=None, upper_raw=None, markers=None, reflective=None):
        """Calcula perda (e réflectance) num ponto da curva onde o OTDR não marcou evento."""
        ie = self.idx(raw_m)
        lo = self.idx(lower_raw) if lower_raw is not None else None
        hi = self.idx(upper_raw) if upper_raw is not None else None
        if reflective is None:
            reflective = self.reflectance(ie, (int(ie) - 40, int(ie) - 1)) is not None
        left, right = self.windows(ie, lo, hi, markers, reflective)
        loss = self.loss_lsa(ie, left, right)
        refl = self.reflectance(ie, left) if reflective else None
        return {'loss': None if loss is None else round(loss, 3), 'refl': None if refl is None else round(refl, 2),
                'reflective': reflective, 'left': left, 'right': right}

    def _steps_once(self, i_start, i_end, known_idx, win):
        """Candidatos a evento: degraus de perda acima do limiar, longe de eventos conhecidos.

        As janelas de cada ponto são cortadas nos eventos conhecidos (e na zona morta depois deles),
        para que um conector reflexivo próximo não pareça um degrau.
        """
        g = self.gap_after(False)
        g_ref = self.gap_after(True) + 4
        known = sorted(known_idx)
        prof = []
        for i in range(int(i_start), int(i_end)):
            j = bisect.bisect_right(known, i)
            lower = known[j - 1] if j else None
            upper = known[j] if j < len(known) else None
            l0 = i - win
            if lower is not None:
                if i - lower < g_ref + 8:
                    continue
                l0 = max(l0, lower + g_ref)
            r1 = i + g + win
            if upper is not None:
                r1 = min(r1, upper - self.guard_before())
            if i - l0 < 8 or r1 - (i + g) < 8:
                continue
            v = self.loss_lsa(i, (l0, i - 1), (i + g, r1))
            if v is not None:
                prof.append((i, v))
        return prof

    def find_steps(self, i_start, i_end, known_idx, threshold=0.05, win=30, max_events=20):
        """Procura eventos não marcados: retira o maior degrau, junta-o aos conhecidos e repete.

        O limiar efetivo nunca fica abaixo de 4x o ruído medido na própria curva.
        """
        key = (round(i_start), round(i_end), tuple(sorted(round(k, 1) for k in known_idx)), threshold, win)
        cache = self.__dict__.setdefault('_steps_cache', {})
        if key not in cache:
            cache[key] = self._find_steps(i_start, i_end, known_idx, threshold, win, max_events)
        return cache[key]

    def _find_steps(self, i_start, i_end, known_idx, threshold, win, max_events):
        known = list(known_idx)
        found = []
        excl = self.gap_after(True) + 4
        for i in self.find_peaks(i_start, i_end):
            if all(abs(i - k) > excl for k in known):
                found.append((i, None))
                known.append(i)
        noise = None
        for _ in range(max_events):
            prof = self._steps_once(i_start, i_end, known, win)
            if not prof:
                break
            if noise is None:
                vals = sorted(abs(v) for _, v in prof)
                noise = vals[len(vals) // 2] * 1.4826
            thr = max(threshold, 4 * noise)
            i, v = max(prof, key=lambda p: abs(p[1]))
            if abs(v) < thr:
                break
            found.append((i, v))
            known.append(i)
        found.sort()
        out = []
        for i, v in found:
            if v is None:  # pico reflexivo: perda calculada com janelas limitadas pelos vizinhos
                lo = max([k for k in known if k < i - 1] or [None], key=lambda x: -1 if x is None else x)
                hi = min([k for k in known if k > i + 1] or [None], key=lambda x: 1e12 if x is None else x)
                ev = self.event_at(i * self.res, None if lo is None else lo * self.res,
                                   None if hi is None else hi * self.res, reflective=True)
                out.append((i * self.res, ev['loss'], ev['refl']))
            else:
                out.append((i * self.res, round(v, 3), None))
        return out

    def find_peaks(self, i_start, i_end, min_db=0.25):
        """Picos reflexivos: subida rápida da curva acima da retrodifusão. Devolve o índice do início."""
        y = self.y
        out = []
        i = int(i_start) + 2
        end = min(int(i_end), self.n - 3)
        while i < end:
            if y[i + 2] - y[i - 1] > min_db:
                j = i - 1
                while j < i + 2 and y[j + 1] - y[i - 1] < 0.05:
                    j += 1
                out.append(j)
                i += self.gap_after(True) + 4
            else:
                i += 1
        return out
