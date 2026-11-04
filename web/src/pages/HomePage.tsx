import { api } from "../api/client";
import { Icon } from "../components/Icon";
import { PageTitle } from "../components/PageParts";
import { ScanForm } from "../components/ScanForm";
import { ScanList } from "../components/ScanList";
import { UploadForm } from "../components/UploadForm";
import { useDocumentTitle, useResource } from "../lib/hooks";

function ServerCapabilities() {
  const health = useResource((signal) => api.health(signal), []);
  if (!health.data) return null;
  const h = health.data;
  const item = (label: string, on: boolean) => (
    <li>
      <Icon name={on ? "check" : "minus"} size={14} />
      {label}: <strong>{on ? "on" : "off"}</strong>
    </li>
  );
  return (
    <section aria-labelledby="server-heading" className="capabilities">
      <h2 id="server-heading" className="capabilities__title">
        This Readpath server
      </h2>
      <ul>
        {item("veraPDF validation", h.verapdf)}
        {item("Image description drafts", h.vision)}
        {item("Text recognition for scanned PDFs", h.ocr)}
        <li>Version {h.version}</li>
      </ul>
    </section>
  );
}

export function HomePage() {
  useDocumentTitle("Check your website's PDFs");

  return (
    <div className="home stack-lg">
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero__text">
          <PageTitle>
            <span id="hero-title">Check your website's PDFs for accessibility</span>
          </PageTitle>
          <p className="lead">
            A federal rule (ADA Title II) says state and local governments must make the PDFs on their websites
            usable by people with disabilities, including people who use screen readers.
          </p>
          <ul className="deadlines" aria-label="Deadlines">
            <li>
              <span className="deadlines__date">April 26, 2027</span>
              <span className="deadlines__who">if you serve 50,000 people or more</span>
            </li>
            <li>
              <span className="deadlines__date">April 26, 2028</span>
              <span className="deadlines__who">for smaller communities and special districts</span>
            </li>
          </ul>
        </div>
      </section>

      <div className="start-grid">
        <section className="card card--feature" aria-labelledby="scan-heading">
          <h2 id="scan-heading" className="card__title">
            <Icon name="globe" size={24} /> Scan your website
          </h2>
          <ScanForm />
        </section>
        <section className="card" aria-labelledby="upload-heading">
          <h2 id="upload-heading" className="card__title">
            <Icon name="upload" size={24} /> Or check PDFs from your computer
          </h2>
          <UploadForm />
        </section>
      </div>

      <section aria-labelledby="scans-heading" className="stack">
        <h2 id="scans-heading">Your scans</h2>
        <ScanList />
      </section>

      <section aria-labelledby="how-heading" className="stack">
        <h2 id="how-heading">How Readpath works</h2>
        <ol className="how-steps">
          <li>
            <span className="how-steps__num" aria-hidden="true">
              1
            </span>
            <h3>Find every PDF</h3>
            <p>Readpath visits your site, lists each PDF, and gives it a score out of 100.</p>
          </li>
          <li>
            <span className="how-steps__num" aria-hidden="true">
              2
            </span>
            <h3>Fix the easy things for you</h3>
            <p>One click adds titles, language and hidden structure that screen readers use. Your originals are kept.</p>
          </li>
          <li>
            <span className="how-steps__num" aria-hidden="true">
              3
            </span>
            <h3>Walk you through the rest</h3>
            <p>Short guided steps help you describe images, check reading order and mark table headings.</p>
          </li>
        </ol>
      </section>

      <ServerCapabilities />
    </div>
  );
}
