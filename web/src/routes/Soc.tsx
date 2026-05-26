import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import type { AlertEvent } from '../lib/api';
import { Timeline } from '../components/soc/Timeline';
import { FilterChips } from '../components/soc/FilterChips';
import type { Filters } from '../components/soc/FilterChips';
import { DetailPane } from '../components/soc/DetailPane';

export default function Soc() {
  const [filters,  setFilters]  = useState<Filters>({ severity: ['critical', 'high'], source: [] });
  const [events,   setEvents]   = useState<AlertEvent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading,  setLoading]  = useState(true);

  useEffect(() => {
    setLoading(true);
    api.listEvents({
      kind: 'drift',
      severity: filters.severity.join(','),
      source: filters.source.length ? filters.source.join(',') : undefined,
      limit: 50,
    })
      .then(r => setEvents(r.events))
      .finally(() => setLoading(false));
  }, [filters]);

  return (
    <div className="flex h-full">
      <div className="flex-1 flex flex-col">
        <header className="p-4 border-b border-stone-200">
          <h1 className="text-xl font-medium text-stone-900">SOC</h1>
          <p className="text-sm text-stone-500">Live drift + alert feed from your connected clouds. AI-enriched.</p>
        </header>
        <FilterChips value={filters} onChange={setFilters} />
        <div className="flex-1 overflow-y-auto">
          {loading
            ? <div className="p-8 text-stone-500 text-center">Loading…</div>
            : <Timeline events={events} onSelect={setSelected} />}
        </div>
      </div>
      {selected && <DetailPane eventId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
