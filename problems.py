"""Problem types reported by the AI inspector."""
import math

TYPES = ('cloud', 'satellite', 'elongation', 'focus', 'flare', 'gradient', 'obstruction', 'quality')
ICONS = {'cloud': '☁', 'satellite': '⟋', 'elongation': '↔', 'focus': '◎', 'flare': '✦',
         'gradient': '▤', 'obstruction': '⌂', 'quality': '▼', 'light': '✦'}
LABELS = {'cloud': 'Облак', 'satellite': 'Следа', 'elongation': 'Издължени звезди / разклащане',
          'focus': 'Разфокус', 'flare': 'Отблясък / лъч', 'gradient': 'Неравномерен фон',
          'obstruction': 'Препятствие', 'quality': 'Под прага на AI индекса'}
# New type -> older type understood by hosts without the new vocabulary.
LEGACY = {'elongation': 'quality', 'focus': 'quality', 'flare': 'light', 'gradient': 'light'}
SEVERITIES = ('low', 'medium', 'high')


def problem(type, score, severity, message, measures, scope='frame'):
    if type not in TYPES:
        raise ValueError('Непознат вид проблем: ' + str(type))
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Score трябва да е 0–1.')
    if severity not in SEVERITIES:
        raise ValueError('Невалидна сериозност: ' + str(severity))
    return dict(type=type, score=float(score), severity=severity, message=message,
                measures=measures, scope=scope, origin='ai')
