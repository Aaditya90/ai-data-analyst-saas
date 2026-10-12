"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type NumericStats = { min: number; max: number; mean: number; median: number; std: number; q1: number; q3: number };
type CategoricalStats = { top_values: { value: string; count: number }[] };
type DatetimeStats = { min: string; max: string };

type ColumnSummary = {
  column: string;
  type: "numeric" | "categorical" | "datetime";
  count: number;
  null_count: number;
  null_pct: number;
  unique_count: number;
  stats?: NumericStats | CategoricalStats | DatetimeStats;
};

type CorrelationPair = { column_a: string; column_b: string; correlation: number };
type ChartSuggestion = { chart_type: string; title: string; x: string; y: string | null; reason: string };

type EdaProfile = {
  row_count: number;
  column_count: number;
  columns: ColumnSummary[];
  correlations: { columns: string[]; pairs: CorrelationPair[] };
  chart_suggestions: ChartSuggestion[];
  cached: boolean;
};

export default function EdaPage() {
  const { getToken } = useAuth();
  const params = useParams<{ datasetId: string }>();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get("workspace_id") ?? "";
  const versionId = searchParams.get("version_id") ?? "";

  const [profile, setProfile] = useState<EdaProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(refresh = false) {
    if (!workspaceId || !versionId) return;
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_URL}/workspaces/${workspaceId}/datasets/${params.datasetId}/versions/${versionId}/eda${refresh ? "?refresh=true" : ""}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) throw new Error(`Failed to load EDA profile (${res.status})`);
      setProfile(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load EDA profile");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, versionId]);

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">Auto EDA</h1>
          <Link href="/datasets" className="text-sm text-slate-400 hover:text-slate-200">
            ← Datasets
          </Link>
        </div>

        {loading && <p className="text-sm text-slate-500">Profiling data…</p>}
        {error && (
          <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {profile && (
          <>
            <div className="flex items-center justify-between text-sm text-slate-500">
              <span>
                {profile.row_count} rows · {profile.column_count} columns
                {profile.cached && " · cached"}
              </span>
              <button onClick={() => load(true)} className="underline hover:text-slate-300">
                Recompute
              </button>
            </div>

            <section className="space-y-3">
              <h2 className="text-sm font-medium text-slate-300">Columns</h2>
              {profile.columns.map((col) => (
                <ColumnCard key={col.column} col={col} />
              ))}
            </section>

            {profile.correlations.pairs.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-sm font-medium text-slate-300">Correlations</h2>
                <div className="rounded-lg border border-slate-800 bg-slate-900 divide-y divide-slate-800">
                  {profile.correlations.pairs.slice(0, 10).map((pair, i) => (
                    <div key={i} className="flex items-center justify-between p-3 text-sm">
                      <span className="text-slate-300">
                        {pair.column_a} ↔ {pair.column_b}
                      </span>
                      <span className={correlationColor(pair.correlation)}>
                        {pair.correlation.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {profile.chart_suggestions.length > 0 && (
              <section className="space-y-2">
                <h2 className="text-sm font-medium text-slate-300">Suggested Charts</h2>
                <p className="text-xs text-slate-500">
                  Rule-based suggestions from column types — build these out in the
                  Dashboard Builder (Phase 6).
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {profile.chart_suggestions.map((chart, i) => (
                    <div
                      key={i}
                      className="rounded-lg border border-slate-800 bg-slate-900 p-3 text-sm"
                    >
                      <div className="text-slate-200">{chart.title}</div>
                      <div className="text-slate-500 text-xs mt-1">
                        {chart.chart_type} · {chart.reason}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </main>
  );
}

function correlationColor(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 0.7) return value > 0 ? "text-emerald-400" : "text-red-400";
  if (abs >= 0.4) return "text-amber-400";
  return "text-slate-500";
}

function ColumnCard({ col }: { col: ColumnSummary }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-3 text-sm">
      <div className="flex items-center justify-between">
        <span className="font-medium">{col.column}</span>
        <span className="text-slate-500 text-xs">
          {col.type} · {col.unique_count} unique
          {col.null_pct > 0 && ` · ${col.null_pct}% null`}
        </span>
      </div>

      {col.type === "numeric" && col.stats && (
        <div className="mt-2 grid grid-cols-4 gap-2 text-xs text-slate-400">
          <Stat label="min" value={(col.stats as NumericStats).min} />
          <Stat label="mean" value={(col.stats as NumericStats).mean} />
          <Stat label="median" value={(col.stats as NumericStats).median} />
          <Stat label="max" value={(col.stats as NumericStats).max} />
        </div>
      )}

      {col.type === "categorical" && col.stats && (
        <div className="mt-2 space-y-1">
          {(col.stats as CategoricalStats).top_values.slice(0, 5).map((tv) => {
            const max = (col.stats as CategoricalStats).top_values[0].count;
            const pct = Math.round((tv.count / max) * 100);
            return (
              <div key={tv.value} className="flex items-center gap-2 text-xs">
                <span className="w-20 truncate text-slate-400">{tv.value}</span>
                <div className="flex-1 h-2 rounded bg-slate-800">
                  <div
                    className="h-2 rounded bg-slate-500"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="w-8 text-right text-slate-500">{tv.count}</span>
              </div>
            );
          })}
        </div>
      )}

      {col.type === "datetime" && col.stats && (
        <div className="mt-2 text-xs text-slate-400">
          {(col.stats as DatetimeStats).min?.slice(0, 10)} →{" "}
          {(col.stats as DatetimeStats).max?.slice(0, 10)}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-slate-600">{label}</div>
      <div className="text-slate-300">{Number.isFinite(value) ? value.toFixed(1) : "—"}</div>
    </div>
  );
}
