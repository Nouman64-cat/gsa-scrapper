"use client";

import React, { useState } from 'react';
import { 
  LayoutDashboard, 
  Database, 
  FileSpreadsheet, 
  CheckCircle, 
  Search, 
  Download, 
  Loader2,
  Package,
  Link2
} from 'lucide-react';
import toast from 'react-hot-toast';
import * as api from '../services/api';
import { useStatus } from '@/components/StatusProvider';
import { StatCard, StatusStatCard } from '@/components/Common';

export default function DashboardOverview() {
  const { status, importStatus, error } = useStatus();
  const [isExporting, setIsExporting] = useState(false);

  const handleExport = async () => {
    try {
      setIsExporting(true);
      const info = await api.getExportInfo();

      if (info.active_engine === 'none') {
        toast.error("No scraped data found. Run Price Extraction or Link Extraction first.", { duration: 5000 });
        return;
      }

      toast.loading(`Compiling export data...`, { id: 'export' });
      await api.downloadExport();
      toast.success("Excel file downloaded successfully!", { id: 'export', duration: 5000 });
    } catch (err: any) {
      toast.error(`Export failed: ${err?.response?.data?.detail || err.message}`, { id: 'export' });
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex justify-between items-center border-b border-slate-200 pb-6">
        <div>
          <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3 text-slate-800">
            <LayoutDashboard className="w-8 h-8 text-blue-600" />
            Operations Overview
          </h1>
          <p className="text-slate-500 mt-2 text-sm">Real-time status of GSA scraping pipeline and data inventory.</p>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 bg-white px-4 py-2 rounded-xl border border-slate-200 shadow-sm">
            <span className="flex h-2.5 w-2.5 relative">
              <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${error ? 'bg-red-400' : 'bg-emerald-400'}`}></span>
              <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${error ? 'bg-red-500' : 'bg-emerald-500'}`}></span>
            </span>
            <span className={`text-sm font-bold ${error ? 'text-red-600' : 'text-slate-700'}`}>
              {error ? 'Server Offline' : 'System Online'}
            </span>
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 p-4 rounded-xl flex items-center gap-3 shadow-sm">
          <Database className="w-5 h-5 flex-shrink-0" />
          <p className="text-sm font-medium">{error}</p>
        </div>
      )}

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Parts"
          value={importStatus?.imported_parts_count}
          icon={<Package className="w-5 h-5" />}
          color="text-amber-600"
          bg="bg-amber-50"
        />
        <StatCard
          title="Total Links"
          value={(importStatus?.product_detail_count ?? 0) + (importStatus?.search_count ?? 0)}
          icon={<Link2 className="w-5 h-5" />}
          color="text-indigo-600"
          bg="bg-indigo-50"
        />
        <StatusStatCard
          title="Extraction Progress"
          completed={status?.database.total_successfully_scraped_links_count}
          total={status?.database.total_generated_links_count}
          icon={<CheckCircle className="w-5 h-5" />}
          color="text-emerald-600"
          bg="bg-emerald-50"
          fill="bg-emerald-500"
        />
        <StatCard
          title="Scraped Records"
          value={status?.database.total_scraped_data_records}
          icon={<Database className="w-5 h-5" />}
          color="text-purple-600"
          bg="bg-purple-50"
        />
      </div>

      {/* Main Content Area */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Active Tasks / Status */}
        <div className="lg:col-span-2 space-y-6">
          <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
            <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
              <Search className="w-5 h-5 text-blue-600" />
              Live Engine Status
            </h2>
            <div className="space-y-4">
              <StatusRow 
                label="Price Extraction" 
                isRunning={status?.is_scraping_running ?? false} 
                progress={status?.scraping_progress}
              />
              <StatusRow 
                label="Link Extraction" 
                isRunning={status?.is_link_extraction_running ?? false} 
                progress={status?.link_extraction_progress}
              />
              <StatusRow 
                label="URL Generation" 
                isRunning={status?.is_link_generation_running ?? false} 
              />
            </div>
          </div>

          <div className="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
            <h2 className="text-xl font-bold mb-4 flex items-center gap-2">
              <FileSpreadsheet className="w-5 h-5 text-emerald-600" />
              Recent Activity
            </h2>
            <div className="text-slate-500 text-sm italic">
              Activity logging will appear here in future updates. Currently monitoring live scraper streams.
            </div>
          </div>
        </div>

        {/* Export Center */}
        <div className="space-y-6">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl text-white h-full flex flex-col">
            <div className="mb-6">
              <h2 className="text-xl font-bold mb-2 flex items-center gap-2">
                <Download className="w-5 h-5 text-blue-400" />
                Export Center
              </h2>
              <p className="text-slate-400 text-sm leading-relaxed">
                Generate and download the final Excel spreadsheet containing all matched pricing data.
              </p>
            </div>
            
            <div className="flex-1 flex flex-col justify-end">
              <button 
                onClick={handleExport}
                disabled={isExporting || !!error}
                className="w-full group flex items-center justify-center gap-3 bg-blue-600 hover:bg-blue-700 disabled:bg-slate-800 disabled:text-slate-600 disabled:border-slate-700 disabled:border text-white font-bold px-6 py-4 rounded-xl transition-all shadow-lg active:scale-95"
              >
                {isExporting ? (
                  <><Loader2 className="w-5 h-5 animate-spin" /> Compiling...</>
                ) : (
                  <><Download className="w-5 h-5 group-hover:-translate-y-1 transition-transform" /> Download Final .XLSX</>
                )}
              </button>
              <p className="text-[10px] text-slate-500 mt-4 text-center font-medium uppercase tracking-widest">
                Format: Microsoft Excel (.xlsx)
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusRow({ label, isRunning, progress }: { label: string, isRunning: boolean, progress?: any }) {
  return (
    <div className="flex items-center justify-between p-4 bg-slate-50 rounded-xl border border-slate-100">
      <div className="flex items-center gap-3">
        <div className={`w-2 h-2 rounded-full ${isRunning ? 'bg-emerald-500 animate-pulse' : 'bg-slate-300'}`} />
        <span className="font-bold text-slate-700">{label}</span>
      </div>
      <div className="flex items-center gap-3">
        {isRunning && progress && (
          <span className="text-xs font-bold text-slate-500 bg-slate-200 px-2 py-0.5 rounded-lg">
            {Math.round((progress.completed / progress.total) * 100)}%
          </span>
        )}
        <span className={`text-xs font-bold px-3 py-1 rounded-full ${isRunning ? 'bg-emerald-100 text-emerald-700 border border-emerald-200' : 'bg-slate-200 text-slate-500'}`}>
          {isRunning ? 'RUNNING' : 'IDLE'}
        </span>
      </div>
    </div>
  );
}