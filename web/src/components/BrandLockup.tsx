import type { ReactNode } from "react";

/** Inline brand lockup: "Shasta by Transilience". Text-only. */
export function BrandLockup({ className = "" }: { className?: string }) {
  return <span className={className}>Shasta by Transilience</span>;
}

interface HeroLockupProps {
  /** The chapter heading (e.g. "Sign in.", "Welcome.", "Access pending."). */
  chapter: ReactNode;
  /** Optional content rendered below the chapter (forms, CTAs, copy). */
  children?: ReactNode;
}

/**
 * Pre-auth hero stack:
 *   Shasta by Transilience      ← wordmark, 26px
 *   The Full Stack Security OS  ← tagline, 19px
 *   <Chapter.>                  ← 56px, persimmon underline + period
 *   {children}
 *
 * Used on SignIn, Callback, PendingApproval, and any other pre-auth route.
 */
export function HeroLockup({ chapter, children }: HeroLockupProps) {
  return (
    <div className="max-w-xl w-full text-center">
      <div className="text-[26px] font-semibold text-slate-900 tracking-tight mb-3">
        Shasta by Transilience
      </div>
      <p className="text-[19px] text-slate-600 mb-9">
        The Full Stack Security OS
      </p>
      <h1 className="inline-block text-[56px] leading-tight font-bold tracking-tight text-slate-900 pb-2.5 border-b-4 border-blue-500 mb-9">
        {chapter}
      </h1>
      {children}
    </div>
  );
}
