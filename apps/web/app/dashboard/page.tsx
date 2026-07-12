"use client";

import { useEffect, useState } from "react";
import { useAuth, useUser, UserButton } from "@clerk/nextjs";

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

export default function DashboardPage() {
  const { user } = useUser();
  const { getToken } = useAuth();
  const [apiUser, setApiUser] = useState<ApiUser | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        const token = await getToken();
        const headers = { Authorization: `Bearer ${token}` };

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
    }
    load();
  }, [getToken]);

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-lg space-y-6">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-semibold tracking-tight">
            Welcome{user?.firstName ? `, ${user.firstName}` : ""}
          </h1>
          <UserButton afterSignOutUrl="/sign-in" />
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

        <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm">
          <div className="text-slate-400 mb-2">Your workspaces</div>
          {workspaces.length === 0 && (
            <p className="text-slate-500">
              No workspaces yet. Create one via the API (
              <code>POST /workspaces</code>) to see it listed here.
            </p>
          )}
          <ul className="space-y-1">
            {workspaces.map((w) => (
              <li key={w.id} className="flex items-center justify-between">
                <span>{w.name}</span>
                <span className="text-slate-500">{w.role}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </main>
  );
}
