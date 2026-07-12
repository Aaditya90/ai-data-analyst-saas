const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function getApiStatus() {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export default async function HomePage() {
  const status = await getApiStatus();

  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-6">
      <div className="max-w-md w-full space-y-6 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">
          AI Data Analyst
        </h1>
        <p className="text-slate-400 text-sm">
          Phase 1 — Foundation. Workspace-aware backend skeleton is live.
        </p>

        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-left text-sm">
          <div className="flex items-center justify-between">
            <span className="text-slate-400">API status</span>
            <span
              className={
                status
                  ? "text-emerald-400 font-medium"
                  : "text-red-400 font-medium"
              }
            >
              {status ? "connected" : "unreachable"}
            </span>
          </div>
          <div className="mt-2 flex items-center justify-between">
            <span className="text-slate-400">Endpoint</span>
            <code className="text-slate-300">{API_URL}</code>
          </div>
        </div>

        <p className="text-xs text-slate-600">
          Next: run <code>docker compose up -d</code>, apply migrations,
          then start the API to see this turn green.
        </p>
      </div>
    </main>
  );
}
