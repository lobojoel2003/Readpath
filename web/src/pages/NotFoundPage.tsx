import { Link } from "react-router-dom";
import { PageTitle } from "../components/PageParts";
import { useDocumentTitle } from "../lib/hooks";

export function NotFoundPage() {
  useDocumentTitle("Page not found");
  return (
    <div className="narrow stack">
      <PageTitle>We can't find that page</PageTitle>
      <p className="lead">The link may be old, or the scan may have been deleted.</p>
      <p>
        <Link to="/" className="btn btn--primary">
          Go to the home page
        </Link>
      </p>
    </div>
  );
}
