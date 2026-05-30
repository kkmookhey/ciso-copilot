import { useEffect, useState, useCallback } from "react";
import { api, type ConnectorRow } from "./api";

export function useConnectors() {
  const [connectors, setConnectors] = useState<ConnectorRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.listConnectors()
      .then(r => setConnectors(r.connectors))
      .catch(e => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  return { connectors, error, reload };
}
