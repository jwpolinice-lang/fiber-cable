"""Leitor de ficheiros .sor (Telcordia SR-4731 / Bellcore, versões 1 e 2)."""
import re
import struct
from dataclasses import dataclass, field

C_KM_PER_US = 299792.458 / 1e6  # velocidade da luz em km/us


class Reader:
    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def u16(self):
        v = struct.unpack_from('<H', self.d, self.p)[0]; self.p += 2; return v

    def i16(self):
        v = struct.unpack_from('<h', self.d, self.p)[0]; self.p += 2; return v

    def u32(self):
        v = struct.unpack_from('<I', self.d, self.p)[0]; self.p += 4; return v

    def i32(self):
        v = struct.unpack_from('<i', self.d, self.p)[0]; self.p += 4; return v

    def fixed(self, n):
        v = self.d[self.p:self.p + n].decode('latin-1'); self.p += n; return v

    def cstr(self):
        end = self.d.index(b'\0', self.p)
        v = self.d[self.p:end].decode('latin-1').strip(); self.p = end + 1; return v


@dataclass
class Event:
    number: int
    distance_m: float
    slope_db_km: float      # atenuação do troço ANTES do evento
    loss_db: float
    reflectance_db: float   # 0 quando não é reflexivo
    code: str
    method: str
    comment: str = ''
    markers: list = field(default_factory=list)  # janelas LSA do OTDR (m): [ini esq, fim esq, ini dir, fim dir, pico]
    computed: bool = False                        # evento calculado pela app (não marcado pelo OTDR)
    loss_precise: float = None                    # valores com todas as casas (bloco privado Viavi/JDSU)
    refl_precise: float = None
    slope_after_precise: float = None             # pente do troço a seguir ao evento

    @property
    def reflective(self):
        return len(self.code) > 0 and self.code[0] == '1'

    @property
    def is_end(self):
        return len(self.code) > 1 and self.code[1] == 'E'


@dataclass
class SorTrace:
    filename: str = ''
    version: int = 2
    cable_id: str = ''
    fiber_id: str = ''
    origin: str = ''
    end: str = ''
    cable_code: str = ''
    operator: str = ''
    comment: str = ''
    supplier: str = ''
    otdr: str = ''
    otdr_sn: str = ''
    module: str = ''
    module_sn: str = ''
    software: str = ''
    timestamp: int = 0
    wavelength_nm: float = 0
    pulse_ns: int = 0
    ior: float = 1.468
    backscatter_db: float = 0
    averages: int = 0
    range_km: float = 0
    resolution_m: float = 0
    events: list = field(default_factory=list)
    total_loss_db: float = 0
    fiber_length_m: float = 0
    orl_db: float = 0
    trace: list = field(default_factory=list)   # valores em dB (nível relativo)
    blocks: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    fiber_override: int = None

    @property
    def fiber_number(self):
        """Número da fibra: pelo nome do ficheiro (ex. OPEX2707-007-1310.sor -> 7)."""
        if self.fiber_override is not None:
            return self.fiber_override
        stem = re.sub(r'\.sor$', '', self.filename, flags=re.I)
        wl = int(round(self.wavelength_nm))
        groups = [g for g in re.findall(r'\d+', stem)
                  if not (len(g) == 4 and abs(int(g) - wl) < 30)]
        if groups:
            return int(groups[-1])
        digits = re.findall(r'\d+', self.fiber_id)
        return int(digits[-1]) if digits else None


def _read_map(data):
    r = Reader(data)
    version = 2
    if data[:4] == b'Map\0':
        r.p = 4
    else:
        version = 1
    rev = r.u16(); size = r.u32(); n = r.u16()
    blocks = {}
    offset = size
    for _ in range(n - 1):
        name = r.cstr(); brev = r.u16(); bsize = r.u32()
        blocks[name] = (offset, bsize, brev)
        offset += bsize
    return version, blocks


def parse_sor(data, filename=''):
    version, blocks = _read_map(data)
    t = SorTrace(filename=filename, version=version, blocks={k: v[1] for k, v in blocks.items()})

    def open_block(name):
        off, size, _ = blocks[name]
        r = Reader(data, off)
        if version == 2:
            r.cstr()  # o nome do bloco repete-se no início
        return r

    def safe(name, fn):
        if name not in blocks:
            return
        try:
            fn(open_block(name))
        except Exception as e:  # bloco truncado ou variante de fabricante
            t.warnings.append(f'bloco {name} ilegível ({e})')

    def gen(r):
        r.fixed(2)  # língua
        t.cable_id = r.cstr(); t.fiber_id = r.cstr()
        if version == 2:
            r.u16()  # tipo de fibra
        t.wavelength_nm = r.u16()
        t.origin = r.cstr(); t.end = r.cstr(); t.cable_code = r.cstr()
        r.fixed(2); r.i32()
        if version == 2:
            r.i32()
        t.operator = r.cstr(); t.comment = r.cstr()

    def sup(r):
        t.supplier = r.cstr(); t.otdr = r.cstr(); t.otdr_sn = r.cstr()
        t.module = r.cstr(); t.module_sn = r.cstr(); t.software = r.cstr()

    safe('GenParams', gen)
    safe('SupParams', sup)

    if 'FxdParams' not in blocks:
        raise ValueError('ficheiro sem bloco FxdParams (não parece um .sor Telcordia)')
    r = open_block('FxdParams')
    t.timestamp = r.u32()
    r.fixed(2)
    t.wavelength_nm = r.u16() / 10.0
    r.i32()
    if version == 2:
        r.i32()
    npw = r.u16()
    pulses = [r.u16() for _ in range(npw)]
    spacing = [r.u32() for _ in range(npw)]
    npts = [r.u32() for _ in range(npw)]
    t.ior = (r.u32() / 100000.0) or 1.468
    t.backscatter_db = -r.u16() / 10.0
    t.averages = r.u32()
    t.pulse_ns = pulses[0] if pulses else 0
    factor = 1e-4 * C_KM_PER_US / t.ior * 1000  # unidades de 100 ps -> metros
    if spacing and spacing[0]:
        t.resolution_m = spacing[0] * 1e-8 * C_KM_PER_US / t.ior * 1000
    try:
        r.u16()
        t.range_km = r.u32() * factor / 1000
    except struct.error:
        pass

    def keyev(r):
        n = r.u16()
        for _ in range(n):
            num = r.u16(); tt = r.u32(); slope = r.i16() / 1000.0
            loss = r.i16() / 1000.0; refl = r.i32() / 1000.0
            code = r.fixed(6); method = r.fixed(2)
            markers = []
            if version == 2:
                markers = [r.i32() * factor for _ in range(5)]
            comment = r.cstr()
            t.events.append(Event(num, tt * factor, slope, loss, refl, code, method, comment, markers))
        try:
            t.total_loss_db = r.i32() / 1000.0
            r.i32()
            t.fiber_length_m = r.u32() * factor
            t.orl_db = r.u16() / 1000.0
        except struct.error:
            pass

    def datapts(r):
        r.u32(); ntr = r.u16()
        if ntr:
            n2 = r.u32(); scale = r.u16() or 1000
            n2 = min(n2, (len(data) - r.p) // 2)
            raw = struct.unpack_from('<%dH' % n2, data, r.p)
            t.trace = [-v * scale / 1e6 for v in raw]

    safe('KeyEvents', keyev)
    safe('DataPts', datapts)
    if 'JDSUEvenementsMTS' in blocks and t.events:
        try:
            _jdsu_precise(t, data[blocks['JDSUEvenementsMTS'][0]:sum(blocks['JDSUEvenementsMTS'][:2])])
        except Exception:
            pass
    if not t.resolution_m and t.trace and t.range_km:
        t.resolution_m = t.range_km * 1000 / len(t.trace)
    return t


def _jdsu_precise(t, blk):
    """Bloco privado Viavi/JDSU: um registo por evento (big-endian) com perda, réflectance, posição (km)
    e pente com todas as casas decimais. O FiberCable calcula as médias a partir destes valores.
    A estrutura (passo e início) é descoberta comparando as posições com a tabela KeyEvents."""
    evs = t.events
    size = len(blk)
    bd = lambda i: struct.unpack_from('>d', blk, i)[0]
    for stride in (280,) + tuple(range(200, 400)):
        for base in range(0, max(0, size - stride * len(evs) - 64)):
            ok = True
            shift = None  # alguns ficheiros têm as posições contadas a partir de uma origem deslocada
            for k, e in enumerate(evs):
                i = base + stride * (k + 1)
                if i + 64 > size:
                    ok = False
                    break
                pos, loss = bd(i + 56), bd(i)
                if not (pos == pos and abs(pos) < 1e4):
                    ok = False
                    break
                if shift is None:
                    shift = e.distance_m - pos * 1000
                if not (abs(pos * 1000 + shift - e.distance_m) < 1 and (loss == -99999.0 or abs(loss - e.loss_db) < 0.001)):
                    ok = False
                    break
            if ok:
                for k, e in enumerate(evs):
                    i = base + stride * (k + 1)
                    loss, refl = bd(i), bd(i + 8)
                    e.loss_precise = None if loss == -99999.0 else loss
                    e.refl_precise = None if refl == -99999.0 else refl
                    if i + 148 <= size:
                        sl = bd(i + 140)
                        e.slope_after_precise = None if sl == -99999.0 else sl
                return True
    return False


def load(path):
    with open(path, 'rb') as f:
        return parse_sor(f.read(), filename=path.rsplit('/', 1)[-1])
