import { BackupItem, fmtKST, getJSON, Incident, PortalSummary } from "@/lib/server";

// ISR: 엣지 캐시 (최대 60s stale).
export const revalidate = 60;

export default async function StatusPage() {
  const [summary, incidents, backups] = await Promise.all([
    getJSON<PortalSummary>("/portal/summary", 60),
    getJSON<{ items: Incident[] }>("/portal/incidents?limit=20", 60),
    getJSON<{ items: BackupItem[] }>("/portal/backups?limit=10", 60),
  ]);

  const inc = incidents?.items || [];
  const bk = backups?.items || [];
  const openN = inc.filter((i) => i.status === "open").length;
  const ok = summary?.status === "ok";
  const dim = "text-sm text-gray-600 dark:text-gray-300";

  return (
    <main className="mx-auto w-full max-w-5xl p-6">
      <h1 className="text-2xl font-bold mb-4">📡 상태</h1>

      <section className="grid gap-4 sm:grid-cols-3 mb-8">
        <div className="rounded-xl border border-gray-200 dark:border-gray-800 p-4">
          <div className={dim}>서버</div>
          <div className="text-lg font-semibold">
            <span className={`inline-block w-2 h-2 rounded-full mr-2 ${ok ? "bg-green-500" : "bg-red-500"}`} />
            {ok ? "정상" : "응답 없음"}
          </div>
        </div>
        <div className="rounded-xl border border-gray-200 dark:border-gray-800 p-4">
          <div className={dim}>미해결 incident</div>
          <div className="text-lg font-semibold">{openN}</div>
        </div>
        <div className="rounded-xl border border-gray-200 dark:border-gray-800 p-4">
          <div className={dim}>최근 백업</div>
          <div className="text-lg font-semibold truncate">
            {summary?.last_backup ? fmtKST(summary.last_backup.time) : "—"}
          </div>
        </div>
      </section>

      <section className="mb-8">
        <h2 className="font-semibold mb-2">Incidents ({inc.length})</h2>
        {inc.length ? (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {inc.map((i) => (
              <li key={i.id} className="py-2 flex items-center gap-3">
                <span
                  className={`text-xs px-2 py-0.5 rounded ${
                    i.status === "open" ? "bg-red-100 text-red-700" : "bg-gray-100 text-gray-600"
                  }`}
                >
                  {i.status}
                </span>
                <span className="font-mono text-xs">{i.component}</span>
                <span className={`${dim} truncate flex-1`}>{i.symptom}</span>
                {i.action ? (
                  <span className="text-xs text-gray-400">{i.action}({i.action_result})</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className={dim}>기록 없음</p>
        )}
      </section>

      <section>
        <h2 className="font-semibold mb-2">Backups ({bk.length})</h2>
        {bk.length ? (
          <ul className="divide-y divide-gray-100 dark:divide-gray-800">
            {bk.map((b) => (
              <li key={b.name} className="py-2 flex items-center gap-3">
                <span className="font-mono text-xs flex-1 truncate">{b.name}</span>
                <span className={dim}>{(b.size / 1e6).toFixed(1)} MB</span>
                <span className="text-xs text-gray-400">{fmtKST(b.time)}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className={dim}>백업 없음</p>
        )}
      </section>
    </main>
  );
}
