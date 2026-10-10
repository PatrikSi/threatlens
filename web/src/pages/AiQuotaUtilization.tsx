import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "../api/client";
import { resolveApiErrorMessage } from "../api/errors";
import { TEAM_BUTTON } from "./teamPresentation";

type Usage = {
  team_key: string;
  active_requests: number;
  requests_last_minute: number;
  reserved_tokens_last_hour: number;
  reported_tokens_last_hour: number;
  conservative_tokens_last_hour: number;
  waiting_seconds: number | null;
  deferral_reason: string | null;
};
type Utilization = {
  totals: Usage;
  teams: Usage[];
  teams_truncated: boolean;
  oldest_wait_seconds: number | null;
};
export function AiQuotaUtilization({ groupId }: { groupId: string }) {
  const query = useQuery({
    queryKey: ["ai", "quota-utilization", groupId],
    queryFn: ({ signal }) =>
      apiFetch<Utilization>(`/ai/quota-groups/${groupId}/utilization`, {
        signal,
      }),
    refetchInterval: 15_000,
  });
  return (
    <section className="space-y-2" aria-label="Account quota utilization">
      <h5 className="font-semibold">Current local account utilization</h5>
      <p className="text-xs">
        Rolling-hour estimates and reported usage are separate. Missing provider
        usage retains the conservative reservation. These are local admission
        totals, not invoices.
      </p>
      {query.isError && (
        <p role="alert">
          {resolveApiErrorMessage(query.error, "Utilization unavailable.")}{" "}
          <button className={TEAM_BUTTON} onClick={() => void query.refetch()}>
            Retry utilization
          </button>
        </p>
      )}
      {!query.isError && query.data && (
        <>
          <p className="text-sm">
            {query.data.totals.active_requests} active ·{" "}
            {query.data.totals.requests_last_minute} requests/minute ·{" "}
            {query.data.totals.conservative_tokens_last_hour.toLocaleString()}{" "}
            accounted tokens/hour · Oldest account wait:{" "}
            {query.data.oldest_wait_seconds ?? 0}s
          </p>
          <div className="overflow-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">Account usage by team</caption>
              <thead>
                <tr>
                  {[
                    "Allocation",
                    "Active",
                    "Reserved",
                    "Reported",
                    "Accounted",
                    "Wait",
                    "Deferral",
                  ].map((label) => (
                    <th key={label} className="p-2" scope="col">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {query.data.teams.map((team) => (
                  <tr key={team.team_key}>
                    <th className="p-2" scope="row">
                      {team.team_key}
                    </th>
                    <td>{team.active_requests}</td>
                    <td>{team.reserved_tokens_last_hour}</td>
                    <td>{team.reported_tokens_last_hour}</td>
                    <td>{team.conservative_tokens_last_hour}</td>
                    <td>{team.waiting_seconds ?? 0}s</td>
                    <td>{team.deferral_reason?.replaceAll("_", " ") ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {query.data.teams_truncated && (
            <p>
              Showing up to 200 allocations; totals include all allocations.
            </p>
          )}
        </>
      )}
    </section>
  );
}
