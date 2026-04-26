"use client";

import React, { useCallback, useEffect, useState } from 'react';
import {
  History,
  Download,
  FileInput,
  FileOutput,
  Loader2,
  RefreshCw,
  CheckCircle,
  XCircle,
  Clock,
  Zap,
} from 'lucide-react';
import toast from 'react-hot-toast';
import * as api from '../../services/api';
import type { Job } from '../../services/api';

function StatusBadge({ status }: { status: Job['status'] }) {
  const map = {
    pending:   { label: 'Pending',   cls: 'bg-slate-100 text-slate-600 border-slate-200',   icon: <Clock className="w-3 h-3" /> },
    running:   { label: 'Running',   cls: 'bg-blue-50 text-blue-700 border-blue-200',        icon: <Loader2 className="w-3 h-3 animate-spin" /> },
    completed: { label: 'Completed', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', icon: <CheckCircle className="w-3 h-3" /> },
    failed:    { label: 'Failed',    cls: 'bg-red-50 text-red-700 border-red-200',           icon: <XCircle className="w-3 h-3" /> },
  };
  const { label, cls, icon } = map[status] ?? map.pending;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border ${cls}`}>
      {icon}{label}
    </span>
  );
}

function TypeBadge({ type }: { type: Job['type'] }) {
  return type === 'parts'
    ? <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-50 text-amber-700 border border-amber-200">Parts</span>
    : <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold bg-purple-50 text-purple-700 border border-purple-200">Links</span>;
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

function duration(created: string, completed: string | null): string {
  if (!completed) return '—';
  const ms = new Date(completed).getTime() - new Date(created).getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

export default function HistoryPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState<Record<string, boolean>>({});

  const fetchJobs = useCallback(async () => {
    try {
      const data = await api.getJobs();
      setJobs(data);
    } catch {
      // silently ignore on auto-refresh
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  // Auto-refresh while any job is running or pending
  useEffect(() => {
    const hasActive = jobs.some(j => j.status === 'running' || j.status === 'pending');
    if (!hasActive) return;
    const id = setInterval(fetchJobs, 8000);
    return () => clearInterval(id);
  }, [jobs, fetchJobs]);

  const download = async (jobId: number, type: 'input' | 'output') => {
    const key = `${jobId}-${type}`;
    setDownloading(d => ({ ...d, [key]: true }));
    try {
      const url = type === 'input'
        ? await api.getJobInputUrl(jobId)
        : await api.getJobOutputUrl(jobId);
      window.open(url, '_blank');
    } catch (err: any) {
      toast.error(err?.response?.data?.detail || 'Download failed');
    } finally {
      setDownloading(d => ({ ...d, [key]: false }));
    }
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3 text-slate-800">
            <History className="w-8 h-8 text-blue-600" />
            Job History
          </h1>
          <p className="text-slate-500 mt-2 text-sm">
            Every import and scraping run is recorded here. Download the original input sheet or the output results at any time.
          </p>
        </div>
        <button
          onClick={() => { setLoading(true); fetchJobs(); }}
          className="flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-semibold transition-all"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {loading && jobs.length === 0 ? (
        <div className="flex items-center justify-center py-24 text-slate-400">
          <Loader2 className="w-8 h-8 animate-spin" />
        </div>
      ) : jobs.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 text-slate-400 gap-4">
          <Zap className="w-12 h-12 text-slate-200" />
          <p className="text-lg font-semibold">No jobs yet</p>
          <p className="text-sm">Upload a parts or links sheet to create your first job.</p>
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-slate-50 border-b border-slate-200">
                <th className="text-left px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider w-10">#</th>
                <th className="text-left px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Type</th>
                <th className="text-left px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Status</th>
                <th className="text-left px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Input File</th>
                <th className="text-right px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Rows</th>
                <th className="text-left px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Created</th>
                <th className="text-left px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Completed</th>
                <th className="text-right px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Duration</th>
                <th className="text-center px-5 py-3.5 font-bold text-slate-500 text-xs uppercase tracking-wider">Downloads</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {jobs.map((job) => (
                <tr key={job.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-5 py-4 text-slate-400 font-mono text-xs">{job.id}</td>
                  <td className="px-5 py-4"><TypeBadge type={job.type} /></td>
                  <td className="px-5 py-4"><StatusBadge status={job.status} /></td>
                  <td className="px-5 py-4">
                    <span className="font-medium text-slate-700 flex items-center gap-1.5">
                      <FileInput className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
                      <span className="truncate max-w-[220px]" title={job.input_filename}>{job.input_filename}</span>
                    </span>
                  </td>
                  <td className="px-5 py-4 text-right font-mono text-slate-600">
                    {job.input_row_count.toLocaleString()}
                  </td>
                  <td className="px-5 py-4 text-slate-600 whitespace-nowrap">{fmt(job.created_at)}</td>
                  <td className="px-5 py-4 text-slate-600 whitespace-nowrap">{fmt(job.completed_at)}</td>
                  <td className="px-5 py-4 text-right text-slate-500 font-mono text-xs whitespace-nowrap">
                    {duration(job.created_at, job.completed_at)}
                  </td>
                  <td className="px-5 py-4">
                    <div className="flex items-center justify-center gap-2">
                      <button
                        onClick={() => download(job.id, 'input')}
                        disabled={!job.has_input_file || downloading[`${job.id}-input`]}
                        title={job.has_input_file ? 'Download input sheet' : 'Input file not saved to S3'}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all
                          disabled:opacity-40 disabled:cursor-not-allowed
                          bg-amber-50 text-amber-700 border border-amber-200 hover:bg-amber-100 disabled:hover:bg-amber-50"
                      >
                        {downloading[`${job.id}-input`]
                          ? <Loader2 className="w-3 h-3 animate-spin" />
                          : <Download className="w-3 h-3" />}
                        Input
                      </button>
                      <button
                        onClick={() => download(job.id, 'output')}
                        disabled={!job.has_output_file || downloading[`${job.id}-output`]}
                        title={job.has_output_file ? 'Download output results' : 'Output not available yet'}
                        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all
                          disabled:opacity-40 disabled:cursor-not-allowed
                          bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 disabled:hover:bg-emerald-50"
                      >
                        {downloading[`${job.id}-output`]
                          ? <Loader2 className="w-3 h-3 animate-spin" />
                          : <FileOutput className="w-3 h-3" />}
                        Output
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
