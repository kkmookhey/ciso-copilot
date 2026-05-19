import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { SignIn } from "./routes/SignIn";
import { Callback } from "./routes/Callback";
import { PendingApproval } from "./routes/PendingApproval";
import { Welcome } from "./routes/Welcome";
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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/signin"             element={<SignIn />} />
        <Route path="/callback"           element={<Callback />} />
        <Route path="/pending"            element={<PendingApproval />} />
        <Route path="/public/trust/:slug" element={<TrustPublic />} />

        <Route element={<Shell />}>
          <Route path="/"          element={<Welcome />} />
          <Route path="/connect"   element={<ConnectClouds />} />
          <Route path="/findings"  element={<TopRisks />} />
          <Route path="/risks"     element={<Risks />} />
          <Route path="/policies"       element={<Policies />} />
          <Route path="/questionnaires" element={<Questionnaires />} />
          <Route path="/trust"          element={<TrustAdmin />} />
          <Route path="/admin"     element={<Admin />} />
          <Route path="/ai/install/callback" element={<InstallCallback />} />
          <Route path="/ai/connections/:id/repos" element={<RepoPicker />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
