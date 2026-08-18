"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Workspace = { id: string; name: string; slug: string; role: string };
type DatasetVersion = {
  id: string;
  version_number: number;
  status: string;
  row_count: number | null;
  column_count: number | null;
  original_filename: string | null;
  source_table_name: string | null;
};
type Dataset = {
  id: string;
  name: string;
  source_type: string;
  created_at: string;
  latest_version: DatasetVersion | null;
};

export default function DatasetsPage() {
  const { getToken } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string>("");
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function authHeaders() {
    const token = await getToken();
    return { Authorization: `Bearer ${token}` };
  }

  useEffect(() => {
    async function loadWorkspaces() {
      try {
        const headers = await authHeaders();
        const res = await fetch(`${API_URL}/workspaces`, { headers });
        if (!res.ok) throw new Error(`Failed to load workspaces (${res.status})`);
        const data: Workspace[] = await res.json();
        setWorkspaces(data);
        if (data.length > 0) setSelectedWorkspace(data[0].id);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load workspaces");
      }
    }
    loadWorkspaces();
  }, []);

  async function loadDatasets(workspaceId: string) {
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_URL}/workspaces/${workspaceId}/datasets`, { headers });
      if (!res.ok) throw new Error(`Failed to load datasets (${res.status})`);
      setDatasets(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load datasets");
    }
  }

  useEffect(() => {
    if (selectedWorkspace) loadDatasets(selectedWorkspace);
  }, [selectedWorkspace]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !selectedWorkspace) return;

    setUploading(true);
    setError(null);
    try {
      const token = await getToken();
      const formData = new FormData();
      formData.append("file", file);

      const res = await fetch(
        `${API_URL}/workspaces/${selectedWorkspace}/datasets/upload`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: formData,
        }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Upload failed (${res.status})`);
      }
      await loadDatasets(selectedWorkspace);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">Datasets</h1>
          <div className="flex items-center gap-4">
            <Link href="/dashboards" className="text-sm text-slate-400 hover:text-slate-200">
              Dashboards →
            </Link>
            <Link href="/dashboard" className="text-sm text-slate-400 hover:text-slate-200">
              ← Dashboard
            </Link>
          </div>
        </div>

        {workspaces.length === 0 && !error && (
          <p className="text-sm text-slate-500">
            No workspaces yet. Create one via the API first (
            <code>POST /workspaces</code>).
          </p>
        )}

        {workspaces.length > 0 && (
          <select
            value={selectedWorkspace}
            onChange={(e) => setSelectedWorkspace(e.target.value)}
            className="w-full rounded-lg border border-slate-800 bg-slate-900 p-2 text-sm"
          >
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name} ({w.role})
              </option>
            ))}
          </select>
        )}

        {selectedWorkspace && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
            <label className="block text-sm text-slate-400 mb-2">
              Upload a CSV, TSV, Excel, or JSON file
            </label>
            <input
              type="file"
              accept=".csv,.tsv,.xlsx,.xls,.json"
              onChange={handleUpload}
              disabled={uploading}
              className="text-sm text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-slate-800 file:px-3 file:py-1.5 file:text-sm file:text-slate-200"
            />
            {uploading && <p className="mt-2 text-sm text-slate-500">Uploading…</p>}
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        <div className="space-y-3">
          {datasets.map((d) => (
            <div
              key={d.id}
              className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{d.name}</span>
                <span className="text-slate-500">{d.source_type}</span>
              </div>
              {d.latest_version && (
                <div className="mt-1 flex items-center justify-between text-slate-400">
                  <span>
                    {d.latest_version.row_count ?? "?"} rows ·{" "}
                    {d.latest_version.column_count ?? "?"} columns ·{" "}
                    <span
                      className={
                        d.latest_version.status === "ready"
                          ? "text-emerald-400"
                          : d.latest_version.status === "failed"
                          ? "text-red-400"
                          : "text-amber-400"
                      }
                    >
                      {d.latest_version.status}
                    </span>
                  </span>
                  {d.source_type === "file_upload" && d.latest_version.status === "ready" && (
                    <span className="flex gap-3">
                      <Link
                        href={`/datasets/${d.id}/query?workspace_id=${selectedWorkspace}`}
                        className="text-slate-300 hover:text-white underline"
                      >
                        Ask →
                      </Link>
                      <Link
                        href={`/datasets/${d.id}/insights?workspace_id=${selectedWorkspace}&version_id=${d.latest_version.id}`}
                        className="text-slate-300 hover:text-white underline"
                      >
                        Insights →
                      </Link>
                      <Link
                        href={`/datasets/${d.id}/eda?workspace_id=${selectedWorkspace}&version_id=${d.latest_version.id}`}
                        className="text-slate-300 hover:text-white underline"
                      >
                        EDA →
                      </Link>
                      <Link
                        href={`/datasets/${d.id}/clean?workspace_id=${selectedWorkspace}&version_id=${d.latest_version.id}`}
                        className="text-slate-300 hover:text-white underline"
                      >
                        Clean →
                      </Link>
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
          {datasets.length === 0 && selectedWorkspace && (
            <p className="text-sm text-slate-500">No datasets yet — upload one above.</p>
          )}
        </div>
      </div>
    </main>
  );
}
