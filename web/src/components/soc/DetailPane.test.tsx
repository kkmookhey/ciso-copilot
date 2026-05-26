// @vitest-environment jsdom
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { DetailPane } from './DetailPane';
import { api } from '../../lib/api';

vi.mock('../../lib/api', () => ({
  api: { getEventDetail: vi.fn() },
}));

const baseEvent = {
  event_id: 'evt-1', kind: 'drift' as const, source: 'aws.cloudtrail',
  severity: 'high' as const, title: 'AuthorizeSecurityGroupIngress',
  description: null, resource_arn: 'sg-abc', actor: 'arn:aws:iam::1:user/x',
  fired_at: '2026-05-25T14:00:00Z', ingested_at: '2026-05-25T14:00:01Z',
  ai_narrative: 'Suspicious change.', ai_anomaly_class: 'suspicious' as const,
  ai_anomaly_score: 88, ai_next_steps: null, ai_model_version: 'claude-sonnet-4-6',
  mitre_technique: null, action: null, after_state: null, before_state: null,
};

describe('DetailPane TI badges', () => {
  beforeEach(() => vi.mocked(api.getEventDetail).mockReset());
  afterEach(() => cleanup());

  it('renders one badge per TI match with source + tags', async () => {
    vi.mocked(api.getEventDetail).mockResolvedValue({
      event: {
        ...baseEvent,
        ai_features: {
          ti_matches: [
            { value: '185.220.101.12', kind: 'ip', source: 'tor',           confidence: null, tags: ['tor_exit']  },
            { value: '185.220.101.12', kind: 'ip', source: 'abusech_feodo', confidence: 80,   tags: ['botnet_c2'] },
          ],
        },
      },
      related_findings: [],
    });
    render(<DetailPane eventId="evt-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/Threat intel/i)).toBeTruthy());
    expect(screen.getAllByText('tor').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('abusech_feodo').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/tor_exit/).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/botnet_c2/).length).toBeGreaterThanOrEqual(1);
    // Confidence rendered when present
    expect(screen.getByText(/conf 80/i)).toBeTruthy();
  });

  it('hides the TI block when ti_matches is empty or absent', async () => {
    vi.mocked(api.getEventDetail).mockResolvedValue({
      event: { ...baseEvent, ai_features: { ti_matches: [] } },
      related_findings: [],
    });
    render(<DetailPane eventId="evt-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.queryByText(/Threat intel/i)).toBeNull());
  });
});
