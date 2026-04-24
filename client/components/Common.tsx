"use client";

import React from 'react';
import * as api from '../services/api';

export function StatCard({ title, value, icon, color, bg }: { title: string, value?: number, icon: React.ReactNode, color: string, bg: string }) {
  return (
    <div className="bg-white shadow-sm border border-slate-200 rounded-2xl p-5 flex flex-col justify-between hover:shadow-md transition-shadow duration-200">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-semibold text-slate-500">{title}</h3>
        <div className={`p-2 rounded-lg ${bg} ${color}`}>
          {icon}
        </div>
      </div>
      <div>
        {value === undefined ? (
           <div className="h-9 w-24 bg-slate-100 animate-pulse rounded-lg" />
        ) : (
          <span className="text-4xl font-black tracking-tight text-slate-800">{value.toLocaleString()}</span>
        )}
      </div>
    </div>
  );
}

export function StatusStatCard({ title, completed, total, icon, color, bg, fill }: { title: string, completed?: number, total?: number, icon: React.ReactNode, color: string, bg: string, fill: string }) {
  const percentage = (total && completed) ? Math.round((completed / total) * 100) : 0;
  
  return (
    <div className="bg-white shadow-sm border border-slate-200 rounded-2xl p-5 flex flex-col justify-between relative overflow-hidden group hover:shadow-md transition-shadow duration-200">
       <div className="absolute top-0 left-0 w-full h-1 bg-slate-100">
         <div className={`h-full ${fill}`} style={{ width: `${percentage}%`, transition: 'width 1s ease-in-out' }} />
       </div>
      <div className="flex items-center justify-between mt-2 mb-4">
        <h3 className="text-sm font-semibold text-slate-500">{title}</h3>
        <div className={`p-2 rounded-lg ${bg} ${color} flex items-center gap-2`}>
          {icon}
        </div>
      </div>
      <div className="flex items-baseline gap-2">
        {completed === undefined ? (
          <div className="h-9 w-32 bg-slate-100 animate-pulse rounded-lg" />
        ) : (
          <>
            <span className="text-4xl font-black tracking-tight text-slate-800">{completed.toLocaleString()}</span>
            <span className="text-sm text-slate-400 font-semibold">/ {total?.toLocaleString()}</span>
          </>
        )}
      </div>
    </div>
  );
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

const PROGRESS_PANEL_STYLES: Record<string, { bg: string; border: string; bar: string; barTrack: string; textBold: string; textMid: string; workerBorder: string; workerText: string; workerSubText: string }> = {
  emerald: {
    bg: "bg-emerald-50",
    border: "border-emerald-200",
    bar: "bg-emerald-500",
    barTrack: "bg-emerald-100",
    textBold: "text-emerald-800",
    textMid: "text-emerald-700",
    workerBorder: "border-emerald-100",
    workerText: "text-emerald-800",
    workerSubText: "text-emerald-600",
  },
  indigo: {
    bg: "bg-indigo-50",
    border: "border-indigo-200",
    bar: "bg-indigo-500",
    barTrack: "bg-indigo-100",
    textBold: "text-indigo-800",
    textMid: "text-indigo-700",
    workerBorder: "border-indigo-100",
    workerText: "text-indigo-800",
    workerSubText: "text-indigo-600",
  },
  blue: {
    bg: "bg-blue-50",
    border: "border-blue-200",
    bar: "bg-blue-500",
    barTrack: "bg-blue-100",
    textBold: "text-blue-800",
    textMid: "text-blue-700",
    workerBorder: "border-blue-100",
    workerText: "text-blue-800",
    workerSubText: "text-blue-600",
  }
};

export function ScrapingProgressPanel({ progress, colorClass = "emerald" }: { progress: api.ScrapingProgress; colorClass?: string }) {
  const pct = progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0;
  const s = PROGRESS_PANEL_STYLES[colorClass] ?? PROGRESS_PANEL_STYLES.emerald;

  return (
    <div className={`${s.bg} border ${s.border} rounded-xl p-4 space-y-3 shadow-sm`}>
      <div className={`w-full h-2.5 ${s.barTrack} rounded-full overflow-hidden`}>
        <div
          className={`h-full ${s.bar} rounded-full transition-all duration-1000 ease-in-out`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className={`flex flex-wrap items-center justify-between text-xs font-semibold ${s.textBold} gap-2`}>
        <span>{progress.completed.toLocaleString()} / {progress.total.toLocaleString()} rows ({pct}%)</span>
        <span>{progress.active_workers} / {progress.num_workers} workers active</span>
      </div>

      <div className={`flex flex-wrap items-center justify-between text-xs ${s.textMid} gap-2`}>
        <span>{progress.successful.toLocaleString()} matched &middot; {progress.failed.toLocaleString()} failed</span>
        <span>
          {progress.avg_seconds_per_row > 0 && `${progress.avg_seconds_per_row}s/row`}
          {progress.estimated_remaining_seconds > 0 && ` · ETA: ${formatDuration(progress.estimated_remaining_seconds)}`}
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5 pt-1">
        {progress.workers.map((w) => (
          <div key={w.id} className={`bg-white/60 border ${s.workerBorder} rounded-lg px-2.5 py-1.5 text-xs`}>
            <div className={`font-bold ${s.workerText}`}>Worker {w.id + 1}</div>
            <div className={`${s.workerSubText} truncate`}>
              {w.completed} done · <span className="capitalize">{w.status}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
