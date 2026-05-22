import { useEffect, useRef, useState } from "react";
import { api, type ScanStatus } from "../lib/api";

const TERMINAL = new Set<ScanStatus["status"]>(["partial", "completed", "failed"]);

export interface UseScanStatus {
  scan:    ScanStatus | null;
  loading: boolean;
  error:   string | null;
}

/** Poll GET /v1/scans/{id} every `intervalMs` until the scan reaches a
 *  terminal status, then stop. Pass scanId=null to disable polling. */
export function useScanStatus(scanId: string | null, intervalMs = 4000): UseScanStatus {
  const [scan, setScan]       = useState<ScanStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(scanId != null);
  const [error, setError]     = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!scanId) { setScan(null); setLoading(false); setError(null); return; }
    let cancelled = false;
    setLoading(true);

    const tick = async () => {
      try {
        const s = await api.getScanStatus(scanId);
        if (cancelled) return;
        setScan(s); setError(null); setLoading(false);
        if (!TERMINAL.has(s.status)) {
          timer.current = window.setTimeout(tick, intervalMs);
        }
      } catch (e) {
        if (cancelled) return;
        setError((e as Error).message); setLoading(false);
        timer.current = window.setTimeout(tick, intervalMs * 2);
      }
    };
    tick();

    return () => {
      cancelled = true;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [scanId, intervalMs]);

  return { scan, loading, error };
}
