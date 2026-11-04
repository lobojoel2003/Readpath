import type { VeraPdfResult } from "../api/types";
import { plural } from "../lib/format";
import { StatusBadge } from "./Badges";

/** Results from veraPDF, the reference PDF/UA validator, when the server has it. */
export function VeraPdfPanel({ result }: { result: VeraPdfResult }) {
  const profile = result.profile ?? "PDF/UA-1";
  let summary: { tone: "ready" | "help" | "neutral" | "error"; label: string; text: string };
  if (!result.available) {
    summary = { tone: "neutral", label: "Not installed", text: "veraPDF isn't installed on this Readpath server." };
  } else if (result.error) {
    summary = { tone: "error", label: "Didn't run", text: result.error };
  } else if (!result.ran) {
    summary = { tone: "neutral", label: "Not run yet", text: 'Choose "Run again" to validate this PDF with veraPDF.' };
  } else if (result.compliant) {
    summary = { tone: "ready", label: `Meets ${profile}`, text: `veraPDF found no ${profile} problems.` };
  } else {
    summary = {
      tone: "help",
      label: `${plural(result.failed_rules.length, "rule")} failed`,
      text: `veraPDF found problems with ${profile}. Many are fixed by Readpath's automatic fixes and the guided review.`,
    };
  }

  return (
    <section aria-labelledby="verapdf-heading" className="card stack">
      <h2 id="verapdf-heading" className="h3">
        Technical validation (veraPDF)
      </h2>
      <div className="inline-row">
        <StatusBadge tone={summary.tone} label={summary.label} />
        <span className="small muted">{summary.text}</span>
      </div>
      {result.ran && result.failed_rules.length > 0 && (
        <details className="disclosure">
          <summary>Show the {plural(result.failed_rules.length, "failed rule")}</summary>
          <div className="disclosure__body">
            <div className="table-scroll" role="region" aria-label="veraPDF failed rules" tabIndex={0}>
              <table className="data-table data-table--compact">
                <caption className="visually-hidden">{profile} rules that failed</caption>
                <thead>
                  <tr>
                    <th scope="col">Clause</th>
                    <th scope="col" className="num">
                      Test
                    </th>
                    <th scope="col">What failed</th>
                    <th scope="col" className="num">
                      Times
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {result.failed_rules.map((r) => (
                    <tr key={`${r.clause}-${r.test}`}>
                      <th scope="row" className="nowrap">
                        {r.clause}
                      </th>
                      <td className="num">{r.test}</td>
                      <td>{r.description}</td>
                      <td className="num">{r.count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </details>
      )}
    </section>
  );
}
