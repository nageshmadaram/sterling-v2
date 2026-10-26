"""Entry-only refresh authority. Last-known rows never renew a trade decision.

No database, network or position mutation occurs here. A queued canonical entry
rechecks this same in-memory authority at the transport boundary.
"""
from __future__ import annotations


class RefreshBoard:
    def __init__(self, prior, planned, *, mode="swing", timeframe=60, previous=None):
        self.prior = list(prior)
        self.planned = {(source, name, mode, timeframe) for source, name in planned}
        self.completed = {}
        self.observed = set()
        # Keep removed-contract tombstones across scans: an approval can still be
        # queued when another successful empty scan starts. Bounded by scanned
        # symbols/modes, not by the number of scan cycles or signal timestamps.
        self.facts = dict(previous.facts) if previous else {}
        for key, (stamp, status, group) in list(self.facts.items()):
            if group in self.planned:
                self.facts[key] = (stamp, "pending", group)
        for row in prior:
            group = self.group(row)
            if group in self.planned:
                self._remember(row, "pending")

    @staticmethod
    def group(row):
        return (row.source, row.underlying, row.horizon_mode, row.signal_timeframe_minutes)

    def _remember(self, row, status=None):
        group = self.group(row)
        facts = []
        for leg in row.legs:
            phase = status or leg.scan_refresh_state or row.scan_refresh_state
            if status is None and (not row.is_active or not leg.is_active):
                phase = phase or "replaced"
            stamp = int((leg.signal_timestamp_ms or leg.entry_timestamp_ms or row.timestamp_ms)
                        if row.source == "derivatives" else row.timestamp_ms)
            facts.append(((row.source, leg.option_symbol, row.horizon_mode, row.signal_timeframe_minutes), stamp, phase))
        facts.append(((row.source, "underlying:" + row.underlying, row.horizon_mode, row.signal_timeframe_minutes),
                      int(row.timestamp_ms), status or row.scan_refresh_state or (None if row.is_active else "replaced")))
        for key, stamp, phase in facts:
            if status is None and key in self.observed:
                prior_stamp, _, _ = self.facts[key]
                if prior_stamp > stamp:
                    continue
            self.facts[key] = (stamp, phase, group)
            if status is None:
                self.observed.add(key)

    def observe(self, row):
        self._remember(row)

    def finish(self, source, name, *, success):
        for group in self.planned:
            if group[:2] != (source, name):
                continue
            self.completed[group] = success
            for key, (stamp, status, fact_group) in list(self.facts.items()):
                if fact_group == group and status == "pending":
                    self.facts[key] = (stamp, "replaced" if success else "failed", group)

    def project(self, fresh):
        from .scanner import _compile_rows
        retained = []
        seen = {(self.group(r), leg.option_symbol) for r in fresh for leg in r.legs}
        fresh_groups = {self.group(r) for r in fresh}
        for old in self.prior:
            group = self.group(old)
            if group not in self.planned:
                retained.append(old)
                continue
            if self.completed.get(group) is True:
                continue
            if old.source == "spot" and group in fresh_groups:
                continue
            copy = old.model_copy(deep=True)
            copy.scan_refresh_state = "failed" if self.completed.get(group) is False else "pending"
            copy.legs = [leg for leg in copy.legs if (group, leg.option_symbol) not in seen]
            for leg in copy.legs:
                leg.scan_refresh_state = copy.scan_refresh_state
            if copy.legs or not old.legs:
                retained.append(copy)
        return _compile_rows(list(fresh) + retained)

    def block_reason(self, source, symbol, stamp, *, mode=None, timeframe=None, underlying=""):
        candidates = [(key, value) for key, value in self.facts.items()
                      if key[0] == source and key[1] == symbol
                      and (mode is None or key[2] == mode)
                      and (timeframe is None or key[3] == timeframe)]
        if not candidates and underlying:
            return self.block_reason(source, "underlying:" + underlying, stamp, mode=mode, timeframe=timeframe)
        if not candidates:
            return ""
        if any(value[0] == stamp and value[1] is None for _, value in candidates):
            return ""
        status = candidates[0][1][1] or "replaced"
        return "signal_refresh_" + status


def admission_blocker(request):
    if request is None or str(request.strategy_id).strip().lower() != "sterling-kite":
        return "", ""
    from .scanner import scanner
    current = scanner._users.get(request.uid)
    if current is None:
        return "", ""
    payload = request.payload or {}
    policy = payload.get("mode_policy") or {}
    mode = payload.get("horizon_mode", policy.get("mode", current.horizon_mode))
    timeframe = payload.get("signal_timeframe_minutes",
                            policy.get("signal_timeframe_minutes", current.timeframe_minutes))
    if mode != current.horizon_mode or timeframe != current.timeframe_minutes:
        return "signal_refresh_mode_changed", "SuperTrend's selected mode or interval changed before submission"
    parts = str(request.signal_id).split(":")
    if len(parts) != 3 or parts[0] not in {"spot", "derivatives", "confluence"}:
        # Legacy simulated manual requests carry an idempotency key rather than
        # signal evidence. They cannot bypass known refresh failures by omitting it.
        phases = [leg.scan_refresh_state or row.scan_refresh_state
                  for row in current.rows for leg in row.legs
                  if leg.option_symbol == request.symbol and row.horizon_mode == mode
                  and row.signal_timeframe_minutes == timeframe]
        if any(phases):
            return "signal_refresh_unverified", "Last-known SuperTrend plan is not verified for new entry"
        if current.refresh_board is not None:
            facts = [value for key, value in current.refresh_board.facts.items()
                     if key[1:] == (request.symbol, mode, timeframe)]
            if any(value[1] for value in facts):
                return "signal_refresh_unverified", "Last-known SuperTrend plan is not verified for new entry"
        return "", ""
    if current.refresh_board is None:
        return "", ""
    try:
        stamp = int(parts[1])
    except ValueError:
        return "invalid_signal_timestamp", "SuperTrend signal timestamp is invalid"
    code = current.refresh_board.block_reason(parts[0], request.symbol, stamp,
        mode=mode, timeframe=timeframe, underlying=payload.get("underlying", ""))
    return code, ("SuperTrend plan is last known, refreshing, failed or superseded; refresh before entering" if code else "")
