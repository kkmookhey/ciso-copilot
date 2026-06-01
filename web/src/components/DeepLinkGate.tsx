import { useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api, type MeResponse } from "../lib/api";
import { isSignedIn } from "../lib/cognito";

/**
 * Wrapper for routes that must survive an unauthenticated browser tab.
 * Used by /risks/:finding_id, which is the destination of the
 * Slack-card "View details" button — an admin clicks it days after
 * the broadcast, opens it in a fresh browser, no Cognito session yet.
 *
 * If signed in: renders children.
 * If not signed in: navigates to /signin?after=<current-path> so
 * Cognito callback can bounce back post-auth.
 */
export function DeepLinkGate({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const loc = useLocation();

  useEffect(() => {
    if (!isSignedIn()) {
      setLoading(false);
      return;
    }
    api.me()
      .then((r) => {
        setMe(r);
        setLoading(false);
      })
      .catch(() => {
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="p-8 text-neutral-500">Loading…</div>;
  }

  if (!me) {
    const after = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/signin?after=${after}`} replace />;
  }

  return <>{children}</>;
}
