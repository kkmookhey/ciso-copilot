import { useEffect, useState } from 'react';
import { api } from '../../lib/api';
import type { EventDetail } from '../../lib/api';
import { FeedbackButtons } from './FeedbackButtons';

export function DetailPane({ eventId, onClose }: { eventId: string; onClose: () => void }) {
  const [data, setData]   = useState<EventDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setData(null);
    setError(null);
    api.getEventDetail(eventId)
      .then(setData)
      .catch((e: Error) => setError(e.message || "Failed to load event detail"));
  }, [eventId]);
  if (error) {
    return (
      <aside className="w-96 border-l border-stone-200 bg-white p-4">
        <div className="flex justify-between items-start mb-3">
          <h3 className="font-medium text-stone-900">Failed to load</h3>
          <button onClick={onClose} className="text-stone-400 hover:text-stone-700">✕</button>
        </div>
        <div className="text-sm text-red-700">{error}</div>
      </aside>
    );
  }
  if (!data) return <div className="p-6 text-stone-500">Loading…</div>;
  const { event: e, related_findings } = data;
  return (
    <aside className="w-96 border-l border-stone-200 bg-white p-4 overflow-y-auto">
      <div className="flex justify-between items-start mb-3">
        <h3 className="font-medium text-stone-900">{e.title}</h3>
        <button onClick={onClose} className="text-stone-400 hover:text-stone-700">✕</button>
      </div>
      <div className="text-xs text-stone-500 mb-3">
        {e.source} · {new Date(e.fired_at).toLocaleString()} · {e.severity}
        {e.actor && <> · by <span className="font-mono">{e.actor.split('/').pop()}</span></>}
      </div>
      {e.ai_narrative ? (
        <>
          <div className="text-sm text-stone-800 mb-3">{e.ai_narrative}</div>
          {e.ai_anomaly_class && (
            <div className="text-xs mb-3">
              <span className="font-medium">Anomaly:</span> {e.ai_anomaly_class}
              {e.ai_anomaly_score !== null && ` (score ${e.ai_anomaly_score}/100)`}
            </div>
          )}
          {e.ai_next_steps && e.ai_next_steps.length > 0 && (
            <div className="mb-3">
              <div className="text-xs font-medium text-stone-700 mb-1">Suggested next steps</div>
              <ul className="text-sm space-y-1">
                {e.ai_next_steps.map((s, i) => (
                  <li key={i} className="text-stone-700">
                    {s.step}
                    {s.command && <pre className="text-xs bg-stone-100 p-1 mt-1 overflow-x-auto"><code>{s.command}</code></pre>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {e.ai_features && (
            <details className="text-xs text-stone-500 mb-3">
              <summary className="cursor-pointer">Why this fired (features)</summary>
              <pre className="bg-stone-50 p-2 mt-1 overflow-x-auto">{JSON.stringify(e.ai_features, null, 2)}</pre>
            </details>
          )}
        </>
      ) : (
        <div className="text-sm text-stone-500 italic mb-3">AI analysis in progress…</div>
      )}
      {related_findings.length > 0 && (
        <div className="mb-3">
          <div className="text-xs font-medium text-stone-700 mb-1">Related findings on this resource</div>
          <ul className="text-sm space-y-1">
            {related_findings.map(f => (
              <li key={f.check_id} className="text-stone-700">
                <span className="text-xs text-stone-500">[{f.severity}]</span> {f.title}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(() => {
        const matches = (e.ai_features as { ti_matches?: Array<{
          value: string; kind: string; source: string; confidence: number | null; tags: string[];
        }> } | null)?.ti_matches;
        if (!matches || matches.length === 0) return null;
        return (
          <div className="mb-3">
            <div className="text-xs font-medium text-stone-700 mb-1">Threat intel</div>
            <ul className="text-xs space-y-1">
              {matches.map((m, i) => (
                <li key={`${m.value}-${m.source}-${i}`}
                    className="flex flex-wrap items-center gap-1 text-stone-700">
                  <span className="font-mono text-stone-900">{m.value}</span>
                  <span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-900 border border-amber-200">{m.source}</span>
                  {m.tags.map(t => (
                    <span key={t} className="px-1.5 py-0.5 rounded bg-stone-100 text-stone-600 border border-stone-200">{t}</span>
                  ))}
                  {m.confidence !== null && (
                    <span className="text-stone-500">conf {m.confidence}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        );
      })()}
      <div className="mt-4 pt-3 border-t border-stone-200">
        <FeedbackButtons eventId={e.event_id} />
      </div>
      {e.ai_model_version && (
        <div className="text-xs text-stone-400 mt-3">AI: {e.ai_model_version}</div>
      )}
    </aside>
  );
}
