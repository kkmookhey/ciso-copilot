// web/src/chat/ModuleRail.tsx
import { NavLink } from "react-router-dom";

const ITEMS: Array<{ to: string; label: string }> = [
  { to: "/",               label: "Chat" },
  { to: "/dashboard",      label: "Dashboard" },
  { to: "/findings",       label: "Findings" },
  { to: "/risks",          label: "Risk register" },
  { to: "/policies",       label: "Policies" },
  { to: "/questionnaires", label: "Questionnaires" },
  { to: "/trust",          label: "Trust center" },
  { to: "/ai/inventory",   label: "AI inventory" },
  { to: "/connect",        label: "Connect" },
  { to: "/admin",          label: "Admin" },
];

export function ModuleRail({ email }: { email: string }) {
  return (
    <nav style={{ width: 200, background: "#3A342B", color: "#FAF8F3",
                  display: "flex", flexDirection: "column", padding: "16px 0" }}>
      {ITEMS.map((it) => (
        <NavLink key={it.to} to={it.to} end={it.to === "/"}
          style={({ isActive }) => ({
            padding: "9px 18px", color: isActive ? "#FFFCF6" : "#A89B89",
            textDecoration: "none", fontSize: 14,
            borderLeft: isActive ? "3px solid #D85F3B" : "3px solid transparent",
          })}>
          {it.label}
        </NavLink>
      ))}
      <div style={{ marginTop: "auto", padding: "12px 18px", fontSize: 12,
                    color: "#7A7268" }}>{email}</div>
    </nav>
  );
}
