import { useEffect } from "react";
import { Link } from "react-router-dom";
import { api, errorMessage } from "../api/client";
import type { Scan } from "../api/types";
import { formatDateTime, isScanActive, prettyUrl, roundScore, SCAN_STATUS_LABEL, scanTone } from "../lib/format";
import { useResource } from "../lib/hooks";
import { useAnnounce } from "./Announcer";
import { Spinner, StatusBadge } from "./Badges";
import { ConfirmButton } from "./ConfirmButton";
import { Icon } from "./Icon";
import { LoadError } from "./Notice";

export function ScanList() {
  const scans = useResource((signal) => api.listScans(signal), []);
  const announce = useAnnounce();
  const anyActive = scans.data?.some((s) => isScanActive(s.status)) ?? false;

  // Keep running scans fresh without hammering the server.
  useEffect(() => {
    if (!anyActive) return;
    const t = window.setTimeout(scans.reload, 5000);
    return () => window.clearTimeout(t);
  }, [anyActive, scans.data, scans.reload]);

  async function remove(scan: Scan) {
    try {
      await api.deleteScan(scan.id);
      announce(`Deleted the scan ${scan.name}.`);
      scans.reload();
    } catch (err) {
      announce(`Couldn't delete the scan. ${errorMessage(err)}`);
    }
  }

  if (scans.error && !scans.data) return <LoadError error={scans.error} onRetry={scans.reload} />;
  if (!scans.data) return <Spinner label="Loading your scans…" />;
  if (scans.data.length === 0) {
    return (
      <div className="empty">
        <Icon name="file" size={28} />
        <p>No scans yet. When you scan a website or upload PDFs, they'll show up here.</p>
      </div>
    );
  }

  return (
    <div className="table-scroll" role="region" aria-label="Table of your scans" tabIndex={0}>
      <table className="data-table">
        <caption className="visually-hidden">Your scans, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Scan</th>
            <th scope="col">Started</th>
            <th scope="col">Status</th>
            <th scope="col" className="num">
              PDFs
            </th>
            <th scope="col" className="num">
              Average score
            </th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {scans.data.map((scan) => {
            const avg = roundScore(scan.summary.average_score);
            return (
              <tr key={scan.id}>
                <th scope="row" className="cell-main">
                  <Link to={`/scans/${encodeURIComponent(scan.id)}`} className="strong-link">
                    {scan.name}
                  </Link>
                  <span className="cell-sub">
                    {scan.kind === "upload"
                      ? "Uploaded files"
                      : prettyUrl(scan.start_url) === scan.name
                        ? "Website scan"
                        : prettyUrl(scan.start_url)}
                  </span>
                </th>
                <td className="nowrap">{formatDateTime(scan.created_at)}</td>
                <td>
                  <StatusBadge tone={scanTone(scan.status)} label={SCAN_STATUS_LABEL[scan.status]} />
                </td>
                <td className="num">{scan.summary.pdfs.toLocaleString()}</td>
                <td className="num">
                  {avg == null ? (
                    <>
                      <span aria-hidden="true">—</span>
                      <span className="visually-hidden">No score yet</span>
                    </>
                  ) : (
                    <>
                      <strong>{avg}</strong>
                      <span className="muted"> / 100</span>
                    </>
                  )}
                </td>
                <td className="actions-cell">
                  <ConfirmButton
                    className="btn btn--ghost btn--sm"
                    ariaLabel={`Delete scan ${scan.name}`}
                    title={`Delete the scan of ${scan.name}?`}
                    body={<p>This removes the scan and Readpath's copies of its files, including any fixed versions. Your website is not changed.</p>}
                    confirmLabel="Delete scan"
                    destructive
                    onConfirm={() => void remove(scan)}
                  >
                    <Icon name="trash" size={16} /> Delete
                  </ConfirmButton>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
