import type { AlertEvent } from '../../lib/api';

const SEV_CLASS: Record<AlertEvent['severity'], string> = {
  critical: 'bg-red-100 text-red-900 border-red-300',
  high:     'bg-orange-100 text-orange-900 border-orange-300',
  medium:   'bg-yellow-100 text-yellow-900 border-yellow-300',
  low:      'bg-stone-100 text-stone-700 border-stone-300',
  info:     'bg-stone-50  text-stone-600 border-stone-200',
};

export function Timeline({ events, onSelect }: { events: AlertEvent[]; onSelect: (id: string) => void }) {
  if (events.length === 0) {
    return <div className="text-stone-500 p-8 text-center">No drift events yet. They land here within 60 seconds of occurring in connected clouds.</div>;
  }
  return (
    <ul className="divide-y divide-stone-200">
      {events.map(e => (
        <li key={e.event_id}
            onClick={() => onSelect(e.event_id)}
            className="p-4 hover:bg-stone-50 cursor-pointer">
          <div className="flex items-start gap-3">
            <span className={`px-2 py-0.5 text-xs border rounded ${SEV_CLASS[e.severity]}`}>{e.severity}</span>
            <div className="flex-1 min-w-0">
              <div className="font-medium text-stone-900 truncate">{e.title}</div>
              <div className="text-sm text-stone-600 truncate">
                {e.resource_arn?.split('/').pop()} {e.actor && <>· by <span className="font-mono">{e.actor.split('/').pop()}</span></>}
              </div>
              {e.ai_narrative && (
                <div className="text-sm text-stone-700 mt-1 line-clamp-2">{e.ai_narrative}</div>
              )}
              {!e.ai_narrative && (
                <div className="text-xs text-stone-400 mt-1 italic">AI analysis pending…</div>
              )}
            </div>
            <div className="text-xs text-stone-500 whitespace-nowrap">{new Date(e.fired_at).toLocaleString()}</div>
            {e.ai_anomaly_class === 'suspicious' && (
              <span className="px-2 py-0.5 text-xs border border-red-400 text-red-700 rounded">suspicious</span>
            )}
            {e.ai_anomaly_class === 'unusual' && (
              <span className="px-2 py-0.5 text-xs border border-amber-400 text-amber-700 rounded">unusual</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
