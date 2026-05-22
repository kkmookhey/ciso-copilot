import { Link } from "react-router-dom";

/// Interim face of the Deep-tier entitlement gate (AWS uplift spec §11) —
/// later replaced by a payment gateway.
export function ContactDeepScan() {
  return (
    <div className="max-w-2xl">
      <h1 className="text-3xl font-bold tracking-tight">Deep Scan</h1>
      <p className="text-slate-600 mt-2">
        A Deep Scan runs the full posture review plus source-code and
        vulnerability analysis. It is a premium tier — talk to us to enable it
        for your account.
      </p>
      <div className="mt-8 rounded-2xl border border-slate-200 p-6">
        <h2 className="font-semibold text-lg">Get in touch</h2>
        <p className="text-sm text-slate-700 mt-2">
          Email us and we will turn on Deep Scans for your tenant.
        </p>
        <a
          href="mailto:hello@transilience.ai?subject=Deep%20Scan%20access"
          className="mt-4 inline-block bg-blue-600 hover:bg-blue-700 text-white font-medium px-5 py-2.5 rounded-lg"
        >
          Email us →
        </a>
      </div>
      <Link to="/connect" className="mt-6 inline-block text-sm text-blue-600 hover:underline">
        ← Back to connections
      </Link>
    </div>
  );
}
