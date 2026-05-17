import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { SignIn } from "./routes/SignIn";
import { Callback } from "./routes/Callback";
import { PendingApproval } from "./routes/PendingApproval";
import { Welcome } from "./routes/Welcome";
import { ConnectClouds } from "./routes/ConnectClouds";
import { TopRisks } from "./routes/TopRisks";
import { Shell } from "./routes/Shell";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/signin"   element={<SignIn />} />
        <Route path="/callback" element={<Callback />} />
        <Route path="/pending"  element={<PendingApproval />} />

        <Route element={<Shell />}>
          <Route path="/"          element={<Welcome />} />
          <Route path="/connect"   element={<ConnectClouds />} />
          <Route path="/findings"  element={<TopRisks />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
