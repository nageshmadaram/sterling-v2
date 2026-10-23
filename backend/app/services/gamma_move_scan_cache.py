"""Persist completed watch selections, never live engines or order state."""
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import logging

from app.engines.gamma_move import Candle, InstrumentRef, WatchContract, CONTRACT_VERSION
from app.services.gamma_move_history_cache import cache_root, _write

log = logging.getLogger(__name__)


def _fingerprint(cfg):
    return hashlib.sha256(json.dumps([CONTRACT_VERSION, cfg.as_dict()], sort_keys=True, allow_nan=False).encode()).hexdigest()


def _key(uid, at):
    scope = hashlib.sha256(str(uid).encode()).hexdigest()[:24]
    return cache_root() / 'slots' / scope / at.date().isoformat() / f'{at:%H%M}.json'


def save_slot(uid, cfg, at, watches, underlying, spots):
    names = {watch.underlying for watch in watches}
    item = {'version': 1, 'config_hash': _fingerprint(cfg), 'at': at.isoformat(),
            'watches': [asdict(watch) for watch in watches],
            'underlying': {name: [int(token), [asdict(bar) for bar in bars]]
                           for name, (token, bars) in underlying.items() if name in names},
            'spots': {name: float(spot) for name, spot in spots.items() if name in names}}
    if names - item['underlying'].keys() or names - item['spots'].keys():
        return  # incomplete input is not a completed selection artifact
    _write(_key(uid, at), item, at.date())


def load_slot(uid, cfg, at):
    try:
        path = _key(uid, at)
        if not path.is_file():
            return None
        raw = path.read_text()
        if not raw:
            return None
        item = json.loads(raw)
        if item.get('version') != 1 or item.get('config_hash') != _fingerprint(cfg) or item.get('at') != at.isoformat():
            return None
        watches = []
        for row in item['watches']:
            data = dict(row)
            data['instrument'] = InstrumentRef(**data['instrument'])
            if not data['instrument'].instrument_id or data['instrument'].lot_size <= 0:
                return None
            watches.append(WatchContract(**data))
        underlying = {name: (int(value[0]), [Candle(**bar) for bar in value[1]])
                      for name, value in item['underlying'].items()}
        spots = {name: float(spot) for name, spot in item['spots'].items()}
        if any(w.underlying not in underlying or w.underlying not in spots for w in watches):
            return None
        return watches, underlying, spots
    except Exception as exc:
        # This is a disposable acceleration artifact. Rebuild from broker facts.
        log.warning('Gamma selection cache unavailable: %s', type(exc).__name__)
        return None
