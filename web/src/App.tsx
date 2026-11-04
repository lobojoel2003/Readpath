import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { Layout } from "./components/Layout";
import { DocumentPage } from "./pages/DocumentPage";
import { HomePage } from "./pages/HomePage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ReviewPage } from "./pages/ReviewPage";
import { ScanPage } from "./pages/ScanPage";

function ReviewIndexRedirect() {
  const { id = "" } = useParams();
  return <Navigate to={`/documents/${encodeURIComponent(id)}/review/basics`} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<HomePage />} />
        <Route path="scans/:id" element={<ScanPage />} />
        <Route path="documents/:id" element={<DocumentPage />} />
        <Route path="documents/:id/review" element={<ReviewIndexRedirect />} />
        <Route path="documents/:id/review/:step" element={<ReviewPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
