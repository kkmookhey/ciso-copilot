import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { isSignedIn, startSignIn } from "../lib/cognito";

export function SignIn() {
  const nav = useNavigate();

  useEffect(() => {
    if (isSignedIn()) nav("/", { replace: true });
  }, [nav]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 bg-gradient-to-b from-slate-50 to-slate-100">
      <div className="max-w-md w-full text-center">
        <div className="w-20 h-20 mx-auto mb-6 rounded-2xl bg-blue-500 text-white flex items-center justify-center text-3xl">
          ⛨
        </div>
        <h1 className="text-4xl font-bold tracking-tight">CISO Copilot</h1>
        <p className="text-slate-500 mt-2">Your cloud security, in one place.</p>

        <button
          onClick={startSignIn}
          className="mt-10 w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-xl px-6 py-4 transition"
        >
          Sign in with corporate account
        </button>

        <p className="mt-4 text-xs text-slate-500">
          Microsoft 365 or Google Workspace. Personal accounts not supported.
        </p>
      </div>
    </div>
  );
}
