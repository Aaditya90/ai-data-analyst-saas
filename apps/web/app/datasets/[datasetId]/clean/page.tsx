"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Issue = {
  issue_type: string;
  column: string | null;
  severity: "low" | "medium" | "high";
  description: string;
  affected_count: number;
  suggested_operation: { op_type: string; column: string | null; params: Record<string, unknown> };
};

const SEVERITY_COLOR: Record<string, string> = {
  high: "text-red-400",
  medium: "text-amber-400",
  low: "text-slate-400",
};

export default function CleanDatasetPage() {
  const { getToken } = useAuth();
  const params = useParams<{ datasetId: string }>();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get("workspace_id") ?? "";
  const versionId = searchParams.get("version_id") ?? "";

  const [issues, setIssues] = useState<Issue[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ dataset_id: string; row_count: number } | null>(null);

  async function authHeaders() {
    const token = await getToken();
    return { Authorization: `Bearer ${token}` };
  }

  useEffect(() => {
    if (!workspaceId || !versionId) return;
    async function load() {
      try {
        const headers = await authHeaders();
        const res = await fetch(
          `${API_URL}/workspaces/${workspaceId}/datasets/${params.datasetId}/versions/${versionId}/issues`,
          { headers }
        );
        if (!res.ok) throw new Error(`Failed to load issues (${res.status})`);
        const data = await res.json();
        setIssues(data.issues);
        // Pre-select every suggestion — user can uncheck what they don't want
        setSelected(new Set(data.issues.map((_: Issue, i: number) => i)));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load issues");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [workspaceId, versionId, params.datasetId]);

  function toggle(index: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });
  }

  async function applySelected() {
    setApplying(true);
    setError(null);
    try {
      const headers = await authHeaders();
      const operations = issues
        .filter((_, i) => selected.has(i))
        .map((issue) => issue.suggested_operation);

      const res = await fetch(
        `${API_URL}/workspaces/${workspaceId}/datasets/${params.datasetId}/versions/${versionId}/clean`,
        {
          method: "POST",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({ operations }),
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Cleaning failed (${res.status})`);
      }
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cleaning failed");
    } finally {
      setApplying(false);
    }
  }

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">Clean Dataset</h1>
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

        {result && (
          <div className="rounded-lg border border-emerald-900 bg-emerald-950/30 p-4 text-sm">
            <p className="text-emerald-400">
              Cleaned dataset created — {result.row_count} rows.
            </p>
            <Link href="/datasets" className="mt-2 inline-block text-slate-300 underline">
              View in Datasets →
            </Link>
          </div>
        )}

        {!loading && !result && issues.length === 0 && !error && (
          <p className="text-sm text-slate-500">
            No issues detected — this data looks clean already.
          </p>
        )}

        {!result && issues.length > 0 && (
          <>
            <p className="text-sm text-slate-500">
              Suggestions are rule-based (nulls, duplicates, outliers, formatting) — nothing
              is applied automatically. Review and uncheck anything you don't want, then
              apply. This creates a new dataset; your original file is untouched.
            </p>
            <div className="space-y-2">
              {issues.map((issue, i) => (
                <label
                  key={i}
                  className="flex items-start gap-3 rounded-lg border border-slate-800 bg-slate-900 p-3 text-sm cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(i)}
                    onChange={() => toggle(i)}
                    className="mt-1"
                  />
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={SEVERITY_COLOR[issue.severity]}>●</span>
                      <span className="font-medium">
                        {issue.column ? `${issue.column}: ` : ""}
                        {issue.issue_type.replace(/_/g, " ")}
                      </span>
                    </div>
                    <p className="text-slate-400 mt-0.5">{issue.description}</p>
                    <p className="text-slate-600 mt-0.5">
                      Suggested: {issue.suggested_operation.op_type.replace(/_/g, " ")}
                    </p>
                  </div>
                </label>
              ))}
            </div>

            <button
              onClick={applySelected}
              disabled={applying || selected.size === 0}
              className="w-full rounded-lg bg-slate-100 py-2 text-sm font-medium text-slate-950 disabled:opacity-50"
            >
              {applying
                ? "Applying…"
                : `Apply ${selected.size} operation${selected.size === 1 ? "" : "s"}`}
            </button>
          </>
        )}
      </div>
    </main>
  );
}
