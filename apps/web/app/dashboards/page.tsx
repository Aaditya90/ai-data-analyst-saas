"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Workspace = { id: string; name: string; slug: string; role: string };
type DashboardSummary = { id: string; name: string; created_at: string; widget_count: number };

export default function DashboardsListPage() {
  const { getToken } = useAuth();
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState("");
  const [dashboards, setDashboards] = useState<DashboardSummary[]>([]);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function authHeaders() {
    const token = await getToken();
    return { Authorization: `Bearer ${token}` };
  }

  useEffect(() => {
    async function load() {
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
    load();
  }, []);

  async function loadDashboards(workspaceId: string) {
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_URL}/workspaces/${workspaceId}/dashboards`, { headers });
      if (!res.ok) throw new Error(`Failed to load dashboards (${res.status})`);
      setDashboards(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboards");
    }
  }

  useEffect(() => {
    if (selectedWorkspace) loadDashboards(selectedWorkspace);
  }, [selectedWorkspace]);

  async function createDashboard() {
    if (!newName.trim() || !selectedWorkspace) return;
    setCreating(true);
    setError(null);
    try {
      const headers = await authHeaders();
      const res = await fetch(`${API_URL}/workspaces/${selectedWorkspace}/dashboards`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim() }),
      });
      if (!res.ok) throw new Error(`Failed to create dashboard (${res.status})`);
      setNewName("");
      await loadDashboards(selectedWorkspace);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create dashboard");
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-2xl space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">Dashboards</h1>
          <Link href="/datasets" className="text-sm text-slate-400 hover:text-slate-200">
            ← Datasets
          </Link>
        </div>

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
          <div className="flex gap-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="New dashboard name"
              className="flex-1 rounded-lg border border-slate-800 bg-slate-900 p-2 text-sm"
              onKeyDown={(e) => e.key === "Enter" && createDashboard()}
            />
            <button
              onClick={createDashboard}
              disabled={creating || !newName.trim()}
              className="rounded-lg bg-slate-100 px-4 text-sm font-medium text-slate-950 disabled:opacity-50"
            >
              Create
            </button>
          </div>
        )}

        {error && (
          <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        <div className="space-y-2">
          {dashboards.map((d) => (
            <Link
              key={d.id}
              href={`/dashboards/${d.id}?workspace_id=${selectedWorkspace}`}
              className="block rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm hover:border-slate-700"
            >
              <div className="flex items-center justify-between">
                <span className="font-medium">{d.name}</span>
                <span className="text-slate-500">{d.widget_count} widgets</span>
              </div>
            </Link>
          ))}
          {dashboards.length === 0 && selectedWorkspace && (
            <p className="text-sm text-slate-500">No dashboards yet — create one above.</p>
          )}
        </div>
      </div>
    </main>
  );
}
