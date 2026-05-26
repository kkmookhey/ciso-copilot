export type Filters = {
  severity: Array<'critical' | 'high' | 'medium' | 'low'>;
  source:   string[];
};

const ALL_SEVS: Filters['severity']  = ['critical', 'high', 'medium', 'low'];
const ALL_SOURCES                    = ['aws.config', 'aws.cloudtrail', 'aws.guardduty', 'aws.inspector2', 'aws.securityhub'];

export function FilterChips({ value, onChange }: { value: Filters; onChange: (f: Filters) => void }) {
  function toggle<T>(arr: T[], item: T): T[] {
    return arr.includes(item) ? arr.filter(x => x !== item) : [...arr, item];
  }
  return (
    <div className="flex flex-wrap gap-2 p-3 border-b border-stone-200">
      <span className="text-xs text-stone-500 self-center">Severity:</span>
      {ALL_SEVS.map(s => (
        <button key={s}
          onClick={() => onChange({ ...value, severity: toggle(value.severity, s) })}
          className={`px-2 py-0.5 text-xs border rounded ${value.severity.includes(s)
            ? 'bg-stone-900 text-white border-stone-900'
            : 'bg-white text-stone-700 border-stone-300'}`}>
          {s}
        </button>
      ))}
      <span className="text-xs text-stone-500 self-center ml-4">Source:</span>
      {ALL_SOURCES.map(s => (
        <button key={s}
          onClick={() => onChange({ ...value, source: toggle(value.source, s) })}
          className={`px-2 py-0.5 text-xs border rounded ${value.source.includes(s)
            ? 'bg-stone-900 text-white border-stone-900'
            : 'bg-white text-stone-700 border-stone-300'}`}>
          {s}
        </button>
      ))}
    </div>
  );
}
