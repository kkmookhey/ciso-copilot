import { useState } from "react";
import type { Connection } from "../lib/api";
import { ScanProgress } from "./ScanProgress";
import { useScanStatus } from "./useScanStatus";
import { AwsScanCardBody } from "./AwsScanCardBody";
import { AzureScanCardBody } from "./AzureScanCardBody";
import { GcpScanCardBody } from "./GcpScanCardBody";
import { EntraScanCardBody } from "./EntraScanCardBody";

interface Props {
  conn: Connection;
  onChanged: () => void;
}

export function ScanCard({ conn, onChanged }: Props) {
  const [runningScanId, setRunningScanId] = useState<string | null>(
    conn.latest_scan?.status === "running" ? conn.latest_scan.scan_id : null
  );

  const { scan: liveScan } = useScanStatus(runningScanId);

  const cloud = conn.cloud_type;
  const isNew = conn.latest_scan === null;
  const lastStatus = conn.latest_scan?.status;
  const accountLabel = conn.account_identifier ?? "(pending)";

  return (
    <div className={
      "border rounded-md bg-white shadow-sm " +
      (isNew ? "border-orange-400 ring-1 ring-orange-200" : "border-stone-200")
    }>
      <div className="flex items-center justify-between px-4 py-3 border-b border-stone-200">
        <div className="flex items-center gap-3">
          <span className="font-semibold uppercase text-xs text-stone-500">{cloud}</span>
          <span className="font-medium text-stone-800">{accountLabel}</span>
          {isNew && (
            <span className="text-xs px-2 py-0.5 bg-orange-100 text-orange-800 rounded">
              Never scanned
            </span>
          )}
          {lastStatus === "failed" && (
            <span className="text-xs px-2 py-0.5 bg-red-100 text-red-800 rounded">
              Last scan failed
            </span>
          )}
          {lastStatus === "partial" && (
            <span className="text-xs px-2 py-0.5 bg-amber-100 text-amber-800 rounded">
              Last scan partial
            </span>
          )}
        </div>
        {conn.latest_scan && (
          <span className="text-xs text-stone-500">
            Last scan: {conn.latest_scan.tier} · {conn.latest_scan.status}
          </span>
        )}
      </div>

      <div className="p-4">
        {runningScanId && liveScan ? (
          <ScanProgress
            scan={liveScan}
            onTerminal={() => { setRunningScanId(null); onChanged(); }}
          />
        ) : (
          <CardBody
            conn={conn}
            onScanStarted={(scanId) => { setRunningScanId(scanId); }}
            onChanged={onChanged}
          />
        )}
      </div>
    </div>
  );
}

function CardBody({ conn, onScanStarted, onChanged }: {
  conn: Connection;
  onScanStarted: (scanId: string) => void;
  onChanged: () => void;
}) {
  switch (conn.cloud_type) {
    case "aws":   return <AwsScanCardBody   conn={conn} onScanStarted={onScanStarted} />;
    case "azure": return <AzureScanCardBody conn={conn} onScanStarted={onScanStarted} onChanged={onChanged} />;
    case "gcp":   return <GcpScanCardBody   conn={conn} onScanStarted={onScanStarted} onChanged={onChanged} />;
    case "entra": return <EntraScanCardBody conn={conn} onScanStarted={onScanStarted} />;
    default:      return <div className="text-stone-500">Unknown cloud type: {conn.cloud_type}</div>;
  }
}
