import { useState } from 'react';
import { api } from '../../lib/api';

export function FeedbackButtons({ eventId }: { eventId: string }) {
  const [sent, setSent] = useState<'up' | 'down' | null>(null);
  async function send(sentiment: 'up' | 'down') {
    await api.postEventFeedback(eventId, { sentiment });
    setSent(sentiment);
  }
  if (sent) return <div className="text-xs text-stone-500">Thanks for the feedback.</div>;
  return (
    <div className="flex gap-2">
      <button onClick={() => send('up')}   className="px-2 py-1 text-xs border border-stone-300 rounded hover:bg-stone-50">👍 helpful</button>
      <button onClick={() => send('down')} className="px-2 py-1 text-xs border border-stone-300 rounded hover:bg-stone-50">👎 not useful</button>
    </div>
  );
}
