"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Insight = {
  insight_type: "outlier" | "correlation" | "imbalance" | "trend";
  column: string;
  significance: number;
  stats: Record<string, unknown>;
  narration: string;
};

type InsightsResponse = {
  insights: Insight[];
  ai_narration_used: boolean;
  cached: boolean;
};

const TYPE_LABEL: Record<string, string> = {
  outlier: "Unusual values",
  correlation: "Relationship",
  imbalance: "Imbalance",
  trend: "Trend",
};

const TYPE_COLOR: Record<string, string> = {
  outlier: "text-amber-400",
  correlation: "text-sky-400",
  imbalance: "text-violet-400",
  trend: "text-emerald-400",
};

export default function InsightsPage() {
  const { getToken } = useAuth();
  const params = useParams<{ datasetId: string }>();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get("workspace_id") ?? "";
  const versionId = searchParams.get("version_id") ?? "";

  const [data, setData] = useState<InsightsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(refresh = false) {
    if (!workspaceId || !versionId) return;
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_URL}/workspaces/${workspaceId}/datasets/${params.datasetId}/versions/${versionId}/insights${refresh ? "?refresh=true" : ""}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Failed to load insights (${res.status})`);
      }
      setData(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load insights");
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
      <div className="mx-auto max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">AI Insights</h1>
          <Link href="/datasets" className="text-sm text-slate-400 hover:text-slate-200">
            ← Datasets
          </Link>
        </div>

        {loading && <p className="text-sm text-slate-500">Analyzing data…</p>}
        {error && (
          <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {data && (
          <>
            <div className="flex items-center justify-between text-xs text-slate-500">
              <span>
                {data.insights.length} insight{data.insights.length === 1 ? "" : "s"} found
                {data.cached && " · cached"}
                {!data.ai_narration_used && data.insights.length > 0 && (
                  <span className="text-amber-500"> · plain-text descriptions (AI narration unavailable)</span>
                )}
              </span>
              <button onClick={() => load(true)} className="underline hover:text-slate-300">
                Recompute
              </button>
            </div>

            {data.insights.length === 0 && (
              <p className="text-sm text-slate-500">
                No statistically significant patterns found in this dataset.
              </p>
            )}

            <div className="space-y-3">
              {data.insights.map((insight, i) => (
                <div key={i} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
                  <div className="flex items-center justify-between mb-1">
                    <span className={`text-xs font-medium ${TYPE_COLOR[insight.insight_type]}`}>
                      {TYPE_LABEL[insight.insight_type]} · {insight.column}
                    </span>
                    <span className="text-xs text-slate-600">
                      {Math.round(insight.significance * 100)}% significance
                    </span>
                  </div>
                  <p className="text-sm text-slate-200">{insight.narration}</p>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  );
}
