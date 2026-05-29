import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { SignIn } from "./routes/SignIn";
import { Callback } from "./routes/Callback";
import { PendingApproval } from "./routes/PendingApproval";
import { Dashboard } from "./routes/Dashboard";
import { ConnectClouds } from "./routes/ConnectClouds";
import { TopRisks } from "./routes/TopRisks";
import { Shell } from "./routes/Shell";
import { Admin } from "./routes/Admin";
import { Risks } from "./routes/Risks";
import { Policies } from "./routes/Policies";
import { Questionnaires } from "./routes/Questionnaires";
import { TrustAdmin }   from "./routes/TrustAdmin";
import { TrustPublic }  from "./routes/TrustPublic";
import { InstallCallback } from "./routes/InstallCallback";
import { RepoPicker } from "./routes/RepoPicker";
import { AIInventory } from "./routes/AIInventory";
import { AssetDetail } from "./routes/AssetDetail";
import AISummary from "./routes/AISummary";
import { ChatShell } from "./chat/Shell";
import { ContactDeepScan } from "./routes/ContactDeepScan";
import Scan from "./routes/Scan";
import Soc from "./routes/Soc";
import { Settings } from "./routes/Settings/Settings";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/signin"             element={<SignIn />} />
        <Route path="/callback"           element={<Callback />} />
        <Route path="/pending"            element={<PendingApproval />} />
        <Route path="/public/trust/:slug" element={<TrustPublic />} />

        {/* Chat surface — auth-gated inside ChatShell itself; no legacy sidebar */}
        <Route path="/" element={<ChatShell />} />

        {/* Legacy routes — auth-gated by Shell, which renders the sidebar chrome */}
        <Route element={<Shell />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/connect"   element={<ConnectClouds />} />
          <Route path="/scan"      element={<Scan />} />
          <Route path="/contact/deep-scan" element={<ContactDeepScan />} />
          <Route path="/findings"  element={<TopRisks />} />
          <Route path="/risks"     element={<Risks />} />
          <Route path="/policies"       element={<Policies />} />
          <Route path="/questionnaires" element={<Questionnaires />} />
          <Route path="/trust"          element={<TrustAdmin />} />
          <Route path="/admin"     element={<Admin />} />
          <Route path="/soc"           element={<Soc />} />
          <Route path="/ai"                  element={<AISummary />} />
          <Route path="/ai/install/callback" element={<InstallCallback />} />
          <Route path="/ai/connections/:id/repos" element={<RepoPicker />} />
          <Route path="/ai/inventory"             element={<AIInventory />} />
          <Route path="/ai/inventory/:asset_id"   element={<AssetDetail />} />
          <Route path="/settings"   element={<Settings />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
