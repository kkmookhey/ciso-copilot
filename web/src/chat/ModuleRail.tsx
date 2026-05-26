// web/src/chat/ModuleRail.tsx
import { NavLink } from "react-router-dom";
import { signOut } from "../lib/cognito";

const BASE_ITEMS: Array<{ to: string; label: string }> = [
  { to: "/",               label: "Chat" },
  { to: "/dashboard",      label: "Dashboard" },
  { to: "/findings",       label: "Findings" },
  { to: "/risks",          label: "Risk register" },
  { to: "/policies",       label: "Policies" },
  { to: "/questionnaires", label: "Questionnaires" },
  { to: "/trust",          label: "Trust center" },
  { to: "/ai/inventory",   label: "AI inventory" },
  { to: "/connect",        label: "Connect clouds" },
  { to: "/scan",           label: "Scan" },
  { to: "/soc",            label: "SOC" },
];

const ADMIN_ITEM = { to: "/admin", label: "Admin" };

interface ModuleRailProps {
  email: string;
  isAdmin?: boolean;
}

export function ModuleRail({ email, isAdmin }: ModuleRailProps) {
  const items = isAdmin ? [...BASE_ITEMS, ADMIN_ITEM] : BASE_ITEMS;

  return (
    <nav style={{ width: 200, background: "#3A342B", color: "#FAF8F3",
                  display: "flex", flexDirection: "column", padding: 0,
                  flexShrink: 0 }}>

      {/* Brand block — "Shasta by Transilience" + uppercase tagline */}
      <div style={{ padding: "18px 18px 16px",
                    borderBottom: "1px solid #4A4238" }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: "#FFFCF6",
                      letterSpacing: "-0.005em", marginBottom: 4 }}>
          Shasta by Transilience
        </div>
        <div style={{ fontSize: 10, textTransform: "uppercase",
                      letterSpacing: "0.12em", color: "#A89B89",
                      fontWeight: 600 }}>
          Full Stack Security OS
        </div>
      </div>

      <div style={{ padding: "8px 0", flex: 1 }}>
        {items.map((it) => (
          <NavLink key={it.to} to={it.to} end={it.to === "/"}
            style={({ isActive }) => ({
              display: "block",
              padding: "9px 18px", color: isActive ? "#FFFCF6" : "#A89B89",
              textDecoration: "none", fontSize: 14,
              borderLeft: isActive ? "3px solid #D85F3B" : "3px solid transparent",
            })}>
            {it.label}
          </NavLink>
        ))}
      </div>

      <div style={{ padding: "12px 18px", fontSize: 12,
                    color: "#7A7268", borderTop: "1px solid #4A4238" }}>
        <div style={{ marginBottom: 6 }}>{email}</div>
        <button
          onClick={signOut}
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer",
                   color: "#7A7268", fontSize: 12 }}
          onMouseEnter={(e) => { (e.target as HTMLButtonElement).style.color = "#A89B89"; }}
          onMouseLeave={(e) => { (e.target as HTMLButtonElement).style.color = "#7A7268"; }}
        >
          Sign out
        </button>
      </div>
    </nav>
  );
}
