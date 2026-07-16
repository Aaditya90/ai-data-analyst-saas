"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth, useUser, UserButton } from "@clerk/nextjs";
import Link from "next/link";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type ApiUser = {
  id: string;
  email: string;
  full_name: string | null;
};

type Workspace = {
  id: string;
  name: string;
  slug: string;
  role: string;
};

function slugify(value: string): string {
  return value
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

export default function DashboardPage() {
  const { user } = useUser();
  const { getToken } = useAuth();
  const [apiUser, setApiUser] = useState<ApiUser | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [newWorkspaceName, setNewWorkspaceName] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  async function authHeaders() {
    const token = await getToken();
    return { Authorization: `Bearer ${token}` };
  }

  const load = useCallback(async () => {
    try {
      const headers = await authHeaders();
      const [meRes, workspacesRes] = await Promise.all([
        fetch(`${API_URL}/me`, { headers }),
        fetch(`${API_URL}/workspaces`, { headers }),
      ]);

      if (!meRes.ok) throw new Error(`/me returned ${meRes.status}`);
      if (!workspacesRes.ok)
        throw new Error(`/workspaces returned ${workspacesRes.status}`);

      setApiUser(await meRes.json());
      setWorkspaces(await workspacesRes.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reach API");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Creates an Organization + Workspace in one click — previously this
  // required going to /docs and calling both endpoints by hand. Every new
  // workspace gets its own freshly created Organization for simplicity;
  // multiple workspaces under one Organization can still be created via
  // the API directly (billing/org management UI is Phase 14).
  async function createWorkspace() {
    const name = newWorkspaceName.trim();
    if (!name) return;

    setCreating(true);
    setCreateError(null);
    try {
      const headers = await authHeaders();
      const slug = `${slugify(name)}-${Math.random().toString(36).slice(2, 6)}`;

      const orgRes = await fetch(`${API_URL}/organizations`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug }),
      });
      if (!orgRes.ok) {
        const body = await orgRes.json().catch(() => ({}));
        throw new Error(body.detail || `Failed to create organization (${orgRes.status})`);
      }
      const org = await orgRes.json();

      const workspaceRes = await fetch(`${API_URL}/workspaces`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({ name, slug, organization_id: org.id }),
      });
      if (!workspaceRes.ok) {
        const body = await workspaceRes.json().catch(() => ({}));
        throw new Error(body.detail || `Failed to create workspace (${workspaceRes.status})`);
      }

      setNewWorkspaceName("");
      await load();
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to create workspace");
    } finally {
      setCreating(false);
    }
  }

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-lg space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">
            Welcome{user?.firstName ? `, ${user.firstName}` : ""}
          </h1>
          <div className="flex items-center gap-4">
            <Link href="/datasets" className="text-sm text-slate-400 hover:text-slate-200">
              Datasets →
            </Link>
            <UserButton afterSignOutUrl="/sign-in" />
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm space-y-2">
          <div className="text-slate-400">Backend session check</div>
          {error && <div className="text-red-400">{error}</div>}
          {!error && !apiUser && (
            <div className="text-slate-500">Verifying token with API…</div>
          )}
          {apiUser && (
            <div className="flex items-center justify-between">
              <span className="text-slate-400">Signed in as</span>
              <span className="text-emerald-400">{apiUser.email}</span>
            </div>
          )}
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm space-y-3">
          <div className="text-slate-400">Your workspaces</div>

          {workspaces.length === 0 && (
            <p className="text-slate-500">No workspaces yet — create one below.</p>
          )}
          <ul className="space-y-1">
            {workspaces.map((w) => (
              <li key={w.id} className="flex items-center justify-between">
                <span>{w.name}</span>
                <span className="text-slate-500">{w.role}</span>
              </li>
            ))}
          </ul>

          <div className="flex gap-2 pt-2 border-t border-slate-800">
            <input
              value={newWorkspaceName}
              onChange={(e) => setNewWorkspaceName(e.target.value)}
              placeholder="New workspace name"
              className="flex-1 rounded border border-slate-800 bg-slate-950 p-2 text-sm"
              onKeyDown={(e) => e.key === "Enter" && createWorkspace()}
            />
            <button
              onClick={createWorkspace}
              disabled={creating || !newWorkspaceName.trim()}
              className="rounded bg-slate-100 px-3 text-sm font-medium text-slate-950 disabled:opacity-50"
            >
              {creating ? "Creating…" : "Create"}
            </button>
          </div>
          {createError && <div className="text-red-400 text-xs">{createError}</div>}
        </div>
      </div>
    </main>
  );
}
