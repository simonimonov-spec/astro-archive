"""Whole-series checks over time: focus drift, passing cloud, entering obstruction, shake runs.

Rows of one group (object/telescope/filter/night/geometry) are ordered by
``meta.observed`` (else ``frameIndex``) and scanned for slow events that single
frames cannot show. Nothing here excludes or deletes frames; results are
reports (``events``) and per-frame problems with ``scope='series'``.

Cloud baselines are local in time: the median of the nearest ``seriesWindow`` frames on each side that
are not themselves cloud candidates, so long clouds and slow background drift (moonrise) do not hide or
fake an event. Obstruction works on each edge strip's star share smoothed with a sliding median; the strip
must hold at least ``MIN_STRIP_STARS`` (8) stars at the start of the drop.

Score for an event is ``0.6 + 0.4 * min(1, excess / 0.5)`` (``high`` at >= 0.85). Each frame of an event gets
its own problem scored the same way from its own measure; a frame of the event that is under the threshold by
itself is ``low`` (shown in the event range, not counted as a problem). Series are split by FITS ``PIERSIDE``,
and an edge strip whose stars move to the opposite strip (or whose loss leaves the total unchanged) is a turned
field, not an obstruction.
"""
from __future__ import annotations

import datetime
import math
import statistics

try:  # loaded as a plugin package, or as a top-level module in tests
    from .problems import problem
except ImportError:
    from problems import problem

DEFAULTS = dict(seriesWindow=7, seriesMinFrames=5, focusRatio=1.25, focusRun=3, focusRoundness=0.8,
                cloudRun=2, cloudTransparency=0.8, cloudStarRatio=0.7, cloudBackgroundRatio=1.1,
                obstructionRun=3, obstructionFinal=0.5, shakeRun=2)
STRIP = 0.2             # edge strip = 20% of the frame size
MIN_STRIP_STARS = 8     # minimum star count in the strip at the start of the drop
SIDES = (('left', 'отляво'), ('right', 'отдясно'), ('top', 'отгоре'), ('bottom', 'отдолу'))
OPPOSITE = dict(left='right', right='left', top='bottom', bottom='top')


def grouping(row):
    """Replica of ``pipeline.grouping`` (that module uses relative imports and cannot be imported here)."""
    m = row['meta']
    h = m.get('headers', {})
    return tuple(str(m.get(k, '')) for k in ('object', 'telescope', 'filter', 'night')) + (
        row.get('width'), row.get('height'), str(h.get('INSTRUME', '')), str(h.get('GAIN', '')),
        str(h.get('XBINNING', 1)), str(h.get('YBINNING', 1)))


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _time(row):
    text = (row.get('meta') or {}).get('observed')
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.datetime.fromisoformat(text.strip().replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _index(row):
    v = _num(row.get('frameIndex'))
    return int(v) if v is not None else None


def _order(rows):
    """Rows sorted by time, else by frameIndex, then name; None when neither exists for all rows."""
    if all(_time(r) is not None for r in rows):
        def key(r):
            return (_time(r), _index(r) if _index(r) is not None else 0, str(r.get('name', '')))
    elif all(_index(r) is not None for r in rows):
        def key(r):
            return (_index(r), str(r.get('name', '')))
    else:
        return None
    return sorted(rows, key=key)


def _stamp(row):
    return (row.get('meta') or {}).get('observed') or ('№%s' % row.get('frameIndex'))


def _clock(row):
    t = _time(row)
    return t.strftime('%H:%M') if t else None


def _frame_no(row, pos):
    i = _index(row)
    return i if i is not None else pos + 1


def _range_text(seq, a, b):
    """'30–42 (23:41–23:58)' for sequence positions a..b."""
    first, last = _frame_no(seq[a], a), _frame_no(seq[b], b)
    text = '%d–%d' % (first, last) if first != last else str(first)
    t1, t2 = _clock(seq[a]), _clock(seq[b])
    if t1 and t2:
        text += ' (%s–%s)' % (t1, t2) if t1 != t2 else ' (%s)' % t1
    return text


def _score(excess):
    return 0.6 + 0.4 * min(1.0, max(0.0, excess) / 0.5)


def _sliding_median(values, window):
    """Centred median over the finite values in the window (window shrinks at the edges)."""
    half = window // 2
    out = []
    for i in range(len(values)):
        part = [v for v in values[max(0, i - half):i + half + 1] if v is not None]
        out.append(statistics.median(part) if part else None)
    return out


def _runs(flags, minimum):
    out, i, n = [], 0, len(flags)
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            if j - i + 1 >= minimum:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _own(excess, below=1.0):
    """A frame's own score: the event shape at/above its threshold; under it (excess < 0) a value below 0.6
    that falls to 0 at ``-below`` (such a frame is listed in the event as ``low``, not counted as a problem)."""
    if excess is None:
        return 0.0
    return _score(excess) if excess >= 0 else 0.6 * max(0.0, 1 + excess / below)


def _severity(score):
    return 'high' if score >= 0.85 else 'medium' if score >= 0.6 else 'low'


def _event(kind, seq, a, b, score, message, measures, frame_scores):
    """Event report plus one series problem per frame, each scored by that frame's own measure."""
    ids = [r['id'] for r in seq[a:b + 1]]
    per = [problem(kind, round(min(1.0, max(0.0, s)), 4), _severity(s), message, dict(measures), scope='series')
           for s in frame_scores]
    return dict(type=kind, start=_stamp(seq[a]), end=_stamp(seq[b]), range=_range_text(seq, a, b), ids=ids,
                message=message, measures=measures), per


def _focus(seq, o):
    fwhm = [_num(r.get('fwhm')) for r in seq]
    fwhm = [v if v and v > 0 else None for v in fwhm]
    valid = sorted(v for v in fwhm if v is not None)
    if len(valid) < 3:
        return []
    best = statistics.median(valid[:max(1, len(valid) // 4)])
    smooth = _sliding_median(fwhm, int(o['seriesWindow']))
    flags = []
    for r, s in zip(seq, smooth):
        rd = _num(r.get('roundness'))
        flags.append(s is not None and s / best >= o['focusRatio'] and (rd is None or rd >= o['focusRoundness']))
    out = []
    for a, b in _runs(flags, int(o['focusRun'])):
        peak = max(smooth[i] for i in range(a, b + 1)) / best
        score = _score(peak / o['focusRatio'] - 1)
        measures = dict(peakFwhmRatio=round(peak, 3), best=round(best, 3))
        if a == 0:
            measures['startFwhmRatio'] = round(smooth[0] / best, 3)
            msg = 'Вероятно лош фокус в началото или по-добър seeing по-късно: FWHM е с %d%% над най-добрия в нощта (кадри %s)' % (
                round((smooth[0] / best - 1) * 100), _range_text(seq, a, b))
        else:
            since = _clock(seq[a])
            msg = 'FWHM расте с %d%% от %s; вероятен разфокус или влошен seeing' % (
                round((peak - 1) * 100), since if since else 'кадър %d' % _frame_no(seq[a], a))
        own = [_own(smooth[i] / best / o['focusRatio'] - 1) for i in range(a, b + 1)]
        out.append(_event('focus', seq, a, b, score, msg, measures, own))
    return out


def _quantile(values, q):
    v = sorted(x for x in values if x is not None)
    if not v:
        return None
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]


def _wide(values, window, q):
    half = window // 2
    return [_quantile(values[max(0, i - half):i + half + 1], q) for i in range(len(values))]


def _local_baseline(values, masked, need):
    """Median of the nearest `need` unmasked values on each side of every frame (one side at series ends)."""
    n = len(values)
    out = []
    for i in range(n):
        picked = []
        for step in (-1, 1):
            k, got = i + step, 0
            while 0 <= k < n and got < need:
                if not masked[k] and values[k] is not None:
                    picked.append(values[k])
                    got += 1
                k += step
        out.append(statistics.median(picked) if picked else None)
    return out


def _ratio(a, b):
    return a / b if a is not None and b else None


def _cloud_gaps(flags, trans, bg, st, masked, need, o):
    """Frames next to a flagged block (gap between runs, head, tail) that are still at cloud level when
    compared with clear frames outside the whole block. Only for frames without a usable transparency.
    A cloud filling most of a short series can otherwise serve as its own baseline."""
    n = len(flags)
    found, i = [], 0
    while i < n:
        if flags[i]:
            i += 1
            continue
        a = i
        while i + 1 < n and not flags[i + 1]:
            i += 1
        b = i
        i += 1
        if not ((a > 0 and flags[a - 1]) or (b + 1 < n and flags[b + 1])):
            continue
        lo, hi = a, b
        while lo > 0 and flags[lo - 1]:
            lo -= 1
        while hi + 1 < n and flags[hi + 1]:
            hi += 1
        outside = [k for k in range(n) if not (lo <= k <= hi) and not masked[k]]
        left, right = [k for k in outside if k < lo][-need:], [k for k in outside if k > hi][:need]
        ref = left + right
        sb = [st[k] for k in ref if st[k] is not None]
        bb = [bg[k] for k in ref if bg[k] is not None]
        if not sb or not bb:
            continue
        st_ref, bg_ref = statistics.median(sb), statistics.median(bb)
        for k in range(a, b + 1):
            if (trans[k] is None and st[k] is not None and bg[k] is not None and st_ref and bg_ref
                    and st[k] / st_ref <= o['cloudStarRatio'] and bg[k] / bg_ref >= o['cloudBackgroundRatio']):
                found.append(k)
    return found


def _cloud(seq, o):
    n, w = len(seq), int(o['seriesWindow'])
    bg = [_num(r.get('background')) for r in seq]
    st = [_num(r.get('stars')) for r in seq]
    trans = []
    for r in seq:
        t = _num(r.get('transparency'))
        trans.append(t if t is not None and bool(r.get('reliable', True)) else None)
    low_t = [t is not None and t <= o['cloudTransparency'] for t in trans]
    wide = max(3 * w, n // 2)
    wide_bg, wide_st = _wide(bg, wide, 0.25), _wide(st, wide, 0.75)
    masked = [low_t[i] or ((_ratio(bg[i], wide_bg[i]) or 0) >= o['cloudBackgroundRatio']
                           and (_ratio(st[i], wide_st[i]) or 9) <= o['cloudStarRatio']) for i in range(n)]
    for _ in range(5):  # iterate until the mask is stable
        bg_base, st_base = _local_baseline(bg, masked, w), _local_baseline(st, masked, w)
        bg_ratio = [_ratio(bg[i], bg_base[i]) for i in range(n)]
        st_ratio = [_ratio(st[i], st_base[i]) for i in range(n)]
        flags, depth = [], []
        for i in range(n):
            if trans[i] is not None:
                low, d = low_t[i], (o['cloudTransparency'] - trans[i]) / o['cloudTransparency']
            else:
                low = (st_ratio[i] is not None and st_ratio[i] <= o['cloudStarRatio']
                       and bg_ratio[i] is not None and bg_ratio[i] >= o['cloudBackgroundRatio'])
                d = (o['cloudStarRatio'] - st_ratio[i]) / o['cloudStarRatio'] if st_ratio[i] is not None else 0.0
            flags.append(bool(low))
            depth.append(d)
        grown = [m or f for m, f in zip(masked, flags)]
        for i in _cloud_gaps(flags, trans, bg, st, grown, w, o):
            grown[i] = True
        if grown == masked:
            break
        masked = grown
    out = []
    for a, b in _runs(flags, int(o['cloudRun'])):
        ts = [t for t in trans[a:b + 1] if t is not None]
        bgs = [x for x in bg_ratio[a:b + 1] if x is not None]
        sts = [x for x in st_ratio[a:b + 1] if x is not None]
        measures = dict(minTransparency=min(ts) if ts else None,
                        maxBackgroundRatio=round(max(bgs), 3) if bgs else None,
                        minStarRatio=round(min(sts), 3) if sts else None)
        what = []
        if ts:
            what.append('прозрачност %.2f' % min(ts))
        elif sts:
            what.append('звезди ×%.2f' % min(sts))
        if bgs:
            what.append('фон ×%.2f' % max(bgs))
        msg = 'Облак преминава през кадри %s' % _range_text(seq, a, b)
        if what:
            msg += ', ' + ', '.join(what)
        own = [_own(depth[i]) for i in range(a, b + 1)]
        out.append(_event('cloud', seq, a, b, _score(max(depth[a:b + 1])), msg, measures, own))
    return out


def _strip_counts(row):
    """Star counts in each edge strip and in total, or None without usable star positions."""
    w, h = _num(row.get('width')), _num(row.get('height'))
    stars = row.get('starList') or []
    if not w or not h or not stars:
        return None
    count = dict(left=0, right=0, top=0, bottom=0)
    n = 0
    for s in stars:
        x, y = _num(s.get('x')), _num(s.get('y'))
        if x is None or y is None:
            continue
        n += 1
        count['left'] += x < STRIP * w
        count['right'] += x > (1 - STRIP) * w
        count['top'] += y < STRIP * h
        count['bottom'] += y > (1 - STRIP) * h
    return (count, n) if n else None


def _median_at(values, positions):
    part = [values[q] for q in positions if values[q] is not None]
    return statistics.median(part) if part else None


def _obstruction(seq, o):
    """An edge strip whose star share falls and stays down. Rejected as a turned field (meridian flip) when
    the opposite strip gains at least 60% of the lost stars, or when the frame's total star count does not
    fall by at least 5%; both compared over the same frames (pre-drop window vs the last frames).
    The total is the full detected count (``stars``): ``starList`` is capped (brightest first) and keeps its
    length when stars vanish. Strip counts come from that list, so their changes compare as shares of it."""
    info = [_strip_counts(r) for r in seq]
    n, w, run, final = len(seq), int(o['seriesWindow']), int(o['obstructionRun']), o['obstructionFinal']
    total = [_num(r.get('stars')) if _num(r.get('stars')) else (i[1] if i else None) for r, i in zip(seq, info)]
    out = []
    for side, word in SIDES:
        raw = [i[0][side] / i[1] if i else None for i in info]
        cnt = [i[0][side] if i else None for i in info]
        opp = [i[0][OPPOSITE[side]] if i else None for i in info]
        sm = _sliding_median(raw, w)
        for p in range(0, n - run):
            if sm[p] is None:
                continue
            seen = [(v, q) for q, v in enumerate(sm[:p + 1]) if v is not None]
            pre, peak_at = max(seen)
            before = range(max(0, peak_at - w + 1), peak_at + 1)
            counts = [cnt[q] for q in before if cnt[q] is not None]
            if pre <= 0 or not counts or statistics.median(counts) < MIN_STRIP_STARS:
                continue
            rest = [v for v in sm[p + run:] if v is not None]
            if not rest or any(v > final * pre for v in rest):
                continue
            after = [q for q in range(p + run, n) if sm[q] is not None][-w:]
            tot_pre, tot_last = _median_at(total, before), _median_at(total, after)
            lost = (_median_at(cnt, before) or 0) - (_median_at(cnt, after) or 0)
            gained = (_median_at(opp, after) or 0) - (_median_at(opp, before) or 0)
            if not tot_pre or tot_last is None or tot_last > 0.95 * tot_pre or gained >= 0.6 * lost:
                break  # stars moved across the frame or did not disappear: not something entering the field
            start = max(q for q in range(p + run + 1) if sm[q] is not None and sm[q] >= 0.9 * pre)
            last = statistics.median(rest[-w:])
            measures = dict(side=side, startShare=round(pre, 3), finalShare=round(last, 3))
            msg = 'Препятствие навлиза %s от кадър %d: звездите в ивицата падат от %d%% на %d%% и не се връщат' % (
                word, _frame_no(seq[start], start), round(pre * 100), round(last * 100))
            own = []
            for q in range(start, n):
                share = raw[q] if raw[q] is not None else sm[q]
                own.append(_own((1 - share / pre) - final, final) if share is not None else 0.0)
            out.append(_event('obstruction', seq, start, n - 1, _score((1 - last / pre) - final), msg, measures, own))
            break
    return out


def _shake(seq, o):
    found = [[p for p in r.get('frameProblems') or [] if p.get('type') == 'elongation'] for r in seq]
    out = []
    for a, b in _runs([bool(f) for f in found], int(o['shakeRun'])):
        scores = [_num(p.get('score')) or 0.6 for f in found[a:b + 1] for p in f]
        mean = sum(scores) / len(scores)
        measures = dict(frames=b - a + 1, meanScore=round(mean, 3))
        msg = 'Разклащане в кадри %s: издължени звезди в %d поредни кадъра' % (_range_text(seq, a, b), b - a + 1)
        own = [max(_num(p.get('score')) or 0.6 for p in f) for f in found[a:b + 1]]
        out.append(_event('elongation', seq, a, b, _score((mean - 0.6) / 0.4 * 0.5), msg, measures, own))
    return out


def analyze(rows, options=None):
    o = dict(DEFAULTS)
    o.update(options or {})
    groups, skipped = {}, 0
    for r in rows or []:
        try:
            key = grouping(r)
            side = ((r.get('meta') or {}).get('headers') or {}).get('PIERSIDE')
            if side not in (None, ''):  # a meridian flip turns the field: each pier side is its own series
                key += ('PIERSIDE=' + str(side).strip().upper(),)
        except (KeyError, AttributeError, TypeError):
            skipped += 1
            continue
        groups.setdefault(key, []).append(r)
    events, per_frame, notes = [], {}, []
    if skipped:
        notes.append('Пропуснати %d кадъра без метаданни за групиране.' % skipped)
    for key, members in groups.items():
        seq = _order(members) if len(members) >= o['seriesMinFrames'] else None
        if seq is None:
            notes.append('Недостатъчно данни за обща проверка на серията (%d кадъра, група %s).' % (
                len(members), ' / '.join(str(k) for k in key[:4] if k != '')))
            continue
        for check in (_focus, _cloud, _obstruction, _shake):
            for ev, probs in check(seq, o):
                events.append(ev)
                for fid, prob in zip(ev['ids'], probs):
                    per_frame.setdefault(fid, []).append(prob)
    return dict(events=events, perFrame=per_frame, notes=notes)
