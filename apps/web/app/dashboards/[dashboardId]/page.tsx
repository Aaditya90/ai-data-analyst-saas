"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import GridLayout, { Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type WidgetType = "chart" | "table" | "kpi" | "text";

type Widget = {
  id: string;
  widget_type: WidgetType;
  title: string;
  dataset_id: string | null;
  config_json: Record<string, unknown>;
  x: number;
  y: number;
  w: number;
  h: number;
};

type Dataset = { id: string; name: string; latest_version: { schema?: { name: string }[] } | null };

export default function DashboardCanvasPage() {
  const { getToken } = useAuth();
  const params = useParams<{ dashboardId: string }>();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get("workspace_id") ?? "";

  const [name, setName] = useState("");
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [widgetData, setWidgetData] = useState<Record<string, unknown>>({});
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function authHeaders() {
    const token = await getToken();
    return { Authorization: `Bearer ${token}` };
  }

  const loadDashboard = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const headers = await authHeaders();
      const res = await fetch(
        `${API_URL}/workspaces/${workspaceId}/dashboards/${params.dashboardId}`,
        { headers }
      );
      if (!res.ok) throw new Error(`Failed to load dashboard (${res.status})`);
      const data = await res.json();
      setName(data.name);
      setWidgets(data.widgets);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, params.dashboardId]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    async function loadDatasets() {
      if (!workspaceId) return;
      const headers = await authHeaders();
      const res = await fetch(`${API_URL}/workspaces/${workspaceId}/datasets`, { headers });
      if (res.ok) setDatasets(await res.json());
    }
    loadDatasets();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  useEffect(() => {
    async function loadAllWidgetData() {
      const headers = await authHeaders();
      const results: Record<string, unknown> = {};
      await Promise.all(
        widgets.map(async (w) => {
          const res = await fetch(
            `${API_URL}/workspaces/${workspaceId}/dashboards/${params.dashboardId}/widgets/${w.id}/data`,
            { headers }
          );
          results[w.id] = res.ok ? await res.json() : { error: `Failed (${res.status})` };
        })
      );
      setWidgetData(results);
    }
    if (widgets.length > 0) loadAllWidgetData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgets, workspaceId]);

  async function handleLayoutChange(layout: Layout[]) {
    const headers = await authHeaders();
    // Only persist positions that actually changed, one PATCH per widget —
    // acceptable at dashboard-widget-count scale, not a bulk-write phase.
    await Promise.all(
      layout.map((item) =>
        fetch(
          `${API_URL}/workspaces/${workspaceId}/dashboards/${params.dashboardId}/widgets/${item.i}`,
          {
            method: "PATCH",
            headers: { ...headers, "Content-Type": "application/json" },
            body: JSON.stringify({ x: item.x, y: item.y, w: item.w, h: item.h }),
          }
        )
      )
    );
  }

  async function deleteWidget(widgetId: string) {
    const headers = await authHeaders();
    await fetch(
      `${API_URL}/workspaces/${workspaceId}/dashboards/${params.dashboardId}/widgets/${widgetId}`,
      { method: "DELETE", headers }
    );
    loadDashboard();
  }

  const layout: Layout[] = widgets.map((w) => ({ i: w.id, x: w.x, y: w.y, w: w.w, h: w.h }));

  return (
    <main className="min-h-screen px-6 py-10">
      <div className="mx-auto max-w-5xl space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{name || "Dashboard"}</h1>
            <p className="text-xs text-slate-500">Drag to move, resize from the corner</p>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={() => setShowForm(!showForm)}
              className="rounded-lg bg-slate-100 px-3 py-1.5 text-sm font-medium text-slate-950"
            >
              + Add Widget
            </button>
            <Link href="/dashboards" className="text-sm text-slate-400 hover:text-slate-200">
              ← Dashboards
            </Link>
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-red-900 bg-red-950/40 p-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {showForm && (
          <AddWidgetForm
            workspaceId={workspaceId}
            dashboardId={params.dashboardId}
            datasets={datasets}
            getAuthHeaders={authHeaders}
            onCreated={() => {
              setShowForm(false);
              loadDashboard();
            }}
          />
        )}

        {widgets.length === 0 && !showForm && (
          <p className="text-sm text-slate-500">
            No widgets yet. Click &quot;+ Add Widget&quot; to build your dashboard.
          </p>
        )}

        <GridLayout
          className="layout"
          layout={layout}
          cols={12}
          rowHeight={60}
          width={1024}
          onDragStop={handleLayoutChange}
          onResizeStop={handleLayoutChange}
        >
          {widgets.map((w) => (
            <div key={w.id} className="rounded-lg border border-slate-800 bg-slate-900 p-3 overflow-hidden">
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium truncate">{w.title}</span>
                <button
                  onClick={() => deleteWidget(w.id)}
                  className="text-slate-600 hover:text-red-400 text-xs"
                >
                  ✕
                </button>
              </div>
              <WidgetContent widget={w} data={widgetData[w.id]} />
            </div>
          ))}
        </GridLayout>
      </div>
    </main>
  );
}

function WidgetContent({ widget, data }: { widget: Widget; data: unknown }) {
  if (!data) return <p className="text-xs text-slate-600">Loading…</p>;
  const d = data as Record<string, unknown>;
  if (d.error) return <p className="text-xs text-red-400">{String(d.error)}</p>;

  if (widget.widget_type === "text") {
    return <p className="text-sm text-slate-300 whitespace-pre-wrap">{String(d.content ?? "")}</p>;
  }

  if (widget.widget_type === "kpi") {
    return <div className="text-3xl font-semibold text-slate-100">{String(d.value ?? "—")}</div>;
  }

  if (widget.widget_type === "table") {
    const columns = (d.columns as string[]) ?? [];
    const rows = (d.rows as string[][]) ?? [];
    return (
      <div className="overflow-auto text-xs">
        <table className="w-full">
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c} className="text-left text-slate-500 pr-3 pb-1">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((row, i) => (
              <tr key={i}>
                {row.map((cell, j) => (
                  <td key={j} className="pr-3 text-slate-300">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (widget.widget_type === "chart") {
    const labels = (d.labels as string[]) ?? [];
    const values = (d.values as number[]) ?? [];
    const max = Math.max(...values, 1);
    return (
      <div className="space-y-1">
        {labels.slice(0, 6).map((label, i) => (
          <div key={label} className="flex items-center gap-2 text-xs">
            <span className="w-16 truncate text-slate-400">{label}</span>
            <div className="flex-1 h-3 rounded bg-slate-800">
              <div
                className="h-3 rounded bg-slate-500"
                style={{ width: `${(values[i] / max) * 100}%` }}
              />
            </div>
            <span className="w-10 text-right text-slate-500">{values[i]?.toFixed(0)}</span>
          </div>
        ))}
      </div>
    );
  }

  return null;
}

function AddWidgetForm({
  workspaceId,
  dashboardId,
  datasets,
  getAuthHeaders,
  onCreated,
}: {
  workspaceId: string;
  dashboardId: string;
  datasets: Dataset[];
  getAuthHeaders: () => Promise<{ Authorization: string }>;
  onCreated: () => void;
}) {
  const [widgetType, setWidgetType] = useState<WidgetType>("kpi");
  const [title, setTitle] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [column, setColumn] = useState("");
  const [yColumn, setYColumn] = useState("");
  const [aggregation, setAggregation] = useState("sum");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);

  const selectedDataset = datasets.find((d) => d.id === datasetId);
  const columns = selectedDataset?.latest_version?.schema?.map((c) => c.name) ?? [];

  async function submit() {
    setSaving(true);
    try {
      const headers = await getAuthHeaders();
      let config_json: Record<string, unknown> = {};
      if (widgetType === "kpi") config_json = { column, aggregation };
      if (widgetType === "chart")
        config_json = { chart_type: "bar", x_column: column, y_column: yColumn || null, aggregation };
      if (widgetType === "table") config_json = { columns: [] };
      if (widgetType === "text") config_json = { content };

      await fetch(`${API_URL}/workspaces/${workspaceId}/dashboards/${dashboardId}/widgets`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({
          widget_type: widgetType,
          title: title || widgetType,
          dataset_id: widgetType === "text" ? null : datasetId || null,
          config_json,
          x: 0,
          y: 0,
          w: widgetType === "kpi" ? 3 : 5,
          h: widgetType === "kpi" ? 2 : 4,
        }),
      });
      onCreated();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <select
          value={widgetType}
          onChange={(e) => setWidgetType(e.target.value as WidgetType)}
          className="rounded border border-slate-800 bg-slate-950 p-2"
        >
          <option value="kpi">KPI</option>
          <option value="chart">Chart</option>
          <option value="table">Table</option>
          <option value="text">Text</option>
        </select>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Widget title"
          className="rounded border border-slate-800 bg-slate-950 p-2"
        />
      </div>

      {widgetType !== "text" && (
        <select
          value={datasetId}
          onChange={(e) => setDatasetId(e.target.value)}
          className="w-full rounded border border-slate-800 bg-slate-950 p-2"
        >
          <option value="">Select dataset…</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
      )}

      {(widgetType === "kpi" || widgetType === "chart") && datasetId && (
        <div className="grid grid-cols-2 gap-3">
          <select
            value={column}
            onChange={(e) => setColumn(e.target.value)}
            className="rounded border border-slate-800 bg-slate-950 p-2"
          >
            <option value="">{widgetType === "chart" ? "X column…" : "Column…"}</option>
            {columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          {widgetType === "chart" && (
            <select
              value={yColumn}
              onChange={(e) => setYColumn(e.target.value)}
              className="rounded border border-slate-800 bg-slate-950 p-2"
            >
              <option value="">Y column (optional — count if blank)</option>
              {columns.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          )}
          <select
            value={aggregation}
            onChange={(e) => setAggregation(e.target.value)}
            className="rounded border border-slate-800 bg-slate-950 p-2"
          >
            {["sum", "avg", "count", "min", "max"].map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </div>
      )}

      {widgetType === "text" && (
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          placeholder="Text content…"
          className="w-full rounded border border-slate-800 bg-slate-950 p-2"
          rows={3}
        />
      )}

      <button
        onClick={submit}
        disabled={saving || !title}
        className="rounded-lg bg-slate-100 px-4 py-1.5 text-sm font-medium text-slate-950 disabled:opacity-50"
      >
        {saving ? "Adding…" : "Add Widget"}
      </button>
    </div>
  );
}
