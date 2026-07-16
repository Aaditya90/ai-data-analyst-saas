"use client";

import { useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type QueryResult = {
  question: string;
  sql: string | null;
  explanation: string;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  cached: boolean;
};

export default function AiQueryPage() {
  const { getToken } = useAuth();
  const params = useParams<{ datasetId: string }>();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get("workspace_id") ?? "";

  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    if (!question.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const token = await getToken();
      const res = await fetch(
        `${API_URL}/workspaces/${workspaceId}/datasets/${params.datasetId}/query`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({ question }),
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Query failed (${res.status})`);
      }
      setResult(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Query failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-3xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">Ask a Question</h1>
          <Link href="/datasets" className="text-sm text-slate-400 hover:text-slate-200">
            ← Datasets
          </Link>
        </div>

        <div className="flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask()}
            placeholder="e.g. What's the average salary by department?"
            className="flex-1 rounded-lg border border-slate-800 bg-slate-900 p-3 text-sm"
          />
          <button
            onClick={ask}
            disabled={loading || !question.trim()}
            className="rounded-lg bg-slate-100 px-4 text-sm font-medium text-slate-950 disabled:opacity-50"
          >
            {loading ? "Thinking…" : "Ask"}
          </button>
        </div>

        {error && (
          <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {result && (
          <div className="space-y-4">
            <p className="text-sm text-slate-300">{result.explanation}</p>

            {result.sql && (
              <div className="rounded-lg border border-slate-800 bg-slate-900 p-3">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-slate-500">
                    Generated SQL{result.cached && " · cached"}
                  </span>
                </div>
                <pre className="text-xs text-slate-400 whitespace-pre-wrap font-mono">
                  {result.sql}
                </pre>
              </div>
            )}

            {result.columns.length > 0 && (
              <div className="overflow-auto rounded-lg border border-slate-800 bg-slate-900">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800">
                      {result.columns.map((c) => (
                        <th key={c} className="text-left p-2 text-slate-400">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, i) => (
                      <tr key={i} className="border-b border-slate-800/50">
                        {row.map((cell, j) => (
                          <td key={j} className="p-2 text-slate-300">
                            {String(cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div className="p-2 text-xs text-slate-600">{result.row_count} rows</div>
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
