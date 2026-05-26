// @vitest-environment jsdom
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, test, expect, vi, afterEach } from 'vitest';
import Soc from './Soc';
import { api } from '../lib/api';

vi.mock('../lib/api', () => ({
  api: {
    listEvents: vi.fn(),
    getEventDetail: vi.fn(),
    postEventFeedback: vi.fn(),
  },
}));

afterEach(() => cleanup());

describe('Soc page', () => {
  test('renders empty state when no events', async () => {
    vi.mocked(api.listEvents).mockResolvedValue({ events: [], total: 0, limit: 50, offset: 0 });
    render(<MemoryRouter><Soc /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/No drift events yet/)).toBeTruthy());
  });

  test('renders timeline + AI narrative', async () => {
    vi.mocked(api.listEvents).mockResolvedValue({
      events: [{
        event_id: 'e1', kind: 'drift', source: 'aws.config', severity: 'high',
        title: 'AuthorizeSecurityGroupIngress',
        description: null,
        resource_arn: 'arn:aws:ec2:us-east-1:1:security-group/sg-abc',
        actor: 'arn:aws:iam::1:user/x', fired_at: '2026-05-25T18:42:10Z',
        ingested_at: '2026-05-25T18:42:12Z',
        ai_narrative: 'Public ingress added to SSH.',
        ai_anomaly_class: 'unusual',
        ai_anomaly_score: 70,
      }],
      total: 1, limit: 50, offset: 0,
    });
    render(<MemoryRouter><Soc /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText(/Public ingress added/)).toBeTruthy());
    expect(screen.getByText('unusual')).toBeTruthy();
  });
});
