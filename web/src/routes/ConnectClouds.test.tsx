// @vitest-environment jsdom
import { render, screen, cleanup } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConnectionRow } from './ConnectClouds';
import type { Connection } from '../lib/api';

afterEach(() => cleanup());

function renderRow(conn: Connection) {
  return render(
    <BrowserRouter>
      <ul>
        <ConnectionRow conn={conn} actionMsg={undefined} onDelete={vi.fn()} />
      </ul>
    </BrowserRouter>,
  );
}

const baseEntraConn: Connection = {
  conn_id:            'c-1',
  cloud_type:         'entra',
  display_name:       'Acme Entra',
  status:             'active',
  account_identifier: 'tenant-abc',
  signals:            { pull_scan: true },
  last_scan_at:       '2026-05-23T19:40:59Z',
  created_at:         '2026-05-20T00:00:00Z',
  scope:              {},
  latest_scan:        null,
};

describe('ConnectionRow — licensing banner (S2.1)', () => {
  it('renders the banner on an Entra row when signin_premium_required is true', () => {
    renderRow({ ...baseEntraConn, scope: { signin_premium_required: true } });
    expect(screen.getByText(/Microsoft Entra ID P1 or P2/i)).toBeTruthy();
    expect(screen.getByText(/Learn more about Entra ID licensing/i)).toBeTruthy();
  });

  it('does NOT render the banner on an Entra row when the flag is absent', () => {
    renderRow({ ...baseEntraConn, scope: {} });
    expect(screen.queryByText(/Microsoft Entra ID P1 or P2/i)).toBeNull();
  });

  it('does NOT render the banner on a non-Entra row even if the flag is set', () => {
    renderRow({ ...baseEntraConn, cloud_type: 'aws',
                scope: { signin_premium_required: true } });
    expect(screen.queryByText(/Microsoft Entra ID P1 or P2/i)).toBeNull();
  });
});
