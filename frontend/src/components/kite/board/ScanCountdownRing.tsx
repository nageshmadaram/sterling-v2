import React, { useEffect, useState } from 'react';
import { ScanProgressRing } from './ScanProgressRing';

export interface ScanCountdownRingProps {
  generatedMs: number;
  nextScanMs: number;
  autoScan: boolean;
  scanning: boolean;
}

/** The one-second UI clock belongs to this tiny SVG—not to the whole
 * strategy sidebar, its four snapshot hooks, filters and signal board.
 * No network requests, trading state or scan cadence are changed.
 */
export const ScanCountdownRing = React.memo(function ScanCountdownRing({
  generatedMs, nextScanMs, autoScan, scanning,
}: ScanCountdownRingProps) {
  const [now, setNow] = useState(() => Date.now());
  const active = autoScan && !scanning && nextScanMs > generatedMs;
  useEffect(() => {
    if (!active) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active, generatedMs, nextScanMs]);
  const interval = nextScanMs - generatedMs;
  const fraction = active && interval > 0
    ? Math.min(1, Math.max(0, (now - generatedMs) / interval))
    : 0;
  return <ScanProgressRing fraction={fraction} scanning={scanning} />;
});
