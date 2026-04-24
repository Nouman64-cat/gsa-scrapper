"use client";

import React, { useState, useRef } from 'react';
import { Package, Upload, CheckCircle, Loader2, RefreshCw, Database, Rocket } from 'lucide-react';
import toast from 'react-hot-toast';
import * as api from '../../services/api';
import { useStatus } from '@/components/StatusProvider';
import { StatCard, ScrapingProgressPanel } from '@/components/Common';

export default function PartsPage() {
  const { status, importStatus, error, refreshStatus } = useStatus();
  const [numWorkers, setNumWorkers] = useState(0);
  const [sortOrder, setSortOrder] = useState<'low_to_high' | 'high_to_low'>('low_to_high');
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setIsUploading(true);
      toast.loading("Uploading and importing parts data...", { id: 'import' });
      const result = await api.uploadParts(file);
      toast.success(`Imported ${result.rows_imported.toLocaleString()} parts from ${result.filename}`, { id: 'import', duration: 5000 });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Import failed: ${err?.response?.data?.detail || err.message}`, { id: 'import' });
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleLinkGeneration = async () => {
    try {
      toast.loading("Initiating Full Link Generation...", { id: 'linkGen' });
      await api.startLinkGeneration({ mode: 'full' });
      toast.success("Link Generation queued successfully!", { id: 'linkGen' });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Error queueing Link Generation: ${err?.response?.data?.detail || err.message}`, { id: 'linkGen' });
    }
  };

  const handleStopLinkGen = async () => {
    try {
      toast.loading("Sending stop signal to Link Engine...", { id: 'linkGenStop' });
      await api.stopLinkGeneration();
      toast.success("Stop signal received. Terminating shortly.", { id: 'linkGenStop' });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Stop failed: ${err.message}`, { id: 'linkGenStop' });
    }
  };

  const handleScraping = async () => {
    try {
      const workers = numWorkers > 0 ? numWorkers : undefined;
      toast.loading(`Initiating Full Selenium Scraper${workers ? ` (${workers} workers)` : ''}...`, { id: 'scrape' });
      await api.startScraping({ mode: 'full', num_workers: workers, sort_order: sortOrder });
      toast.success("Selenium Scraping queued successfully!", { id: 'scrape' });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Error queueing Scraping: ${err?.response?.data?.detail || err.message}`, { id: 'scrape' });
    }
  };

  const handleStopScraping = async () => {
    try {
      toast.loading("Sending stop signal to Selenium Scraper...", { id: 'scrapeStop' });
      await api.stopScraping();
      toast.success("Stop signal received. Driver will close soon.", { id: 'scrapeStop' });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Stop failed: ${err.message}`, { id: 'scrapeStop' });
    }
  };

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3 text-slate-800">
          <Package className="w-8 h-8 text-blue-600" />
          Parts Management
        </h1>
        <p className="text-slate-500 mt-2 text-sm">Manage part imports, generate search URLs, and run the Selenium pricing scraper.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
        <StatCard
          title="Imported Parts"
          value={importStatus?.imported_parts_count ?? 0}
          icon={<Upload className="w-5 h-5" />}
          color="text-amber-600"
          bg="bg-amber-50 border border-amber-100"
        />
        <StatCard
          title="Generated URLs"
          value={status?.database.total_generated_links_count}
          icon={<Rocket className="w-5 h-5" />}
          color="text-blue-600"
          bg="bg-blue-50 border border-blue-100"
        />
        <StatCard
          title="Extracted Pricing"
          value={status?.database.total_scraped_data_records}
          icon={<Database className="w-5 h-5" />}
          color="text-purple-600"
          bg="bg-purple-50 border border-purple-100"
        />
        <StatCard
          title="Successful Matches"
          value={status?.database.total_successfully_scraped_links_count}
          icon={<CheckCircle className="w-5 h-5" />}
          color="text-emerald-600"
          bg="bg-emerald-50 border border-emerald-100"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Import Section */}
        <div className="bg-white border border-slate-200 shadow-sm rounded-2xl p-6">
          <div className="flex flex-col gap-4">
            <div>
              <h2 className="text-xl font-bold flex items-center gap-2 text-slate-800">
                <Upload className="w-5 h-5 text-amber-600" />
                Import Parts
              </h2>
              <p className="text-slate-500 text-sm mt-1">
                Upload an Excel file with <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs font-mono">part_number</code> and <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs font-mono">manufacturer</code> columns.
              </p>
            </div>
            <div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls"
                onChange={handleFileUpload}
                className="hidden"
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading || status?.is_scraping_running}
                className="w-full flex items-center justify-center gap-2 bg-amber-500 hover:bg-amber-600 disabled:opacity-50 text-white font-semibold px-5 py-3 rounded-xl transition-all shadow-md"
              >
                {isUploading ? (
                  <><Loader2 className="w-5 h-5 animate-spin" /> Importing...</>
                ) : (
                  <><Upload className="w-5 h-5" /> Upload Parts .XLSX</>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Link Gen Panel */}
        <div className="bg-white border border-slate-200 shadow-sm rounded-2xl p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-bold flex items-center gap-2 text-slate-800">
              <Rocket className="w-5 h-5 text-blue-600" />
              URL Generation
            </h2>
            {status?.is_link_generation_running && (
              <span className="flex items-center gap-2 text-xs font-semibold bg-blue-50 text-blue-700 px-3 py-1 rounded-full border border-blue-200">
                <RefreshCw className="w-3 h-3 animate-spin" /> Generating...
              </span>
            )}
          </div>
          <div className="space-y-4">
            <p className="text-sm text-slate-500">Automatically generate GSA Advantage search URLs for all imported parts.</p>
            <div className="flex gap-2">
              <button
                onClick={handleLinkGeneration}
                disabled={status?.is_link_generation_running || !!error}
                className="flex-1 flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white font-semibold px-4 py-3 rounded-xl transition-all shadow-md"
              >
                {status?.is_link_generation_running ? (
                  <><Loader2 className="w-5 h-5 animate-spin" /> Processing...</>
                ) : (
                  <><Rocket className="w-5 h-5" /> Launch URL Generator</>
                )}
              </button>
              {status?.is_link_generation_running && (
                <button
                  onClick={handleStopLinkGen}
                  className="bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 font-bold px-6 py-3 rounded-xl transition-all"
                >
                  Stop
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Scraping Panel */}
        <div className="lg:col-span-2 bg-white border border-slate-200 shadow-sm rounded-2xl p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-bold flex items-center gap-2 text-slate-800">
              <RefreshCw className="w-5 h-5 text-emerald-600" />
              Price Extraction
            </h2>
            {status?.is_scraping_running && (
              <span className="flex items-center gap-2 text-xs font-semibold bg-emerald-50 text-emerald-700 px-3 py-1 rounded-full border border-emerald-200">
                <RefreshCw className="w-3 h-3 animate-spin" /> Scraping...
              </span>
            )}
          </div>

          <div className="space-y-4">
            <div className="flex flex-col gap-2">
              <label className="text-xs uppercase font-bold text-slate-500 tracking-wider">Parallel Workers</label>
              <select
                value={numWorkers}
                onChange={(e) => setNumWorkers(Number(e.target.value))}
                disabled={status?.is_scraping_running}
                className="bg-white border border-slate-200 text-sm rounded-lg px-4 py-2.5 focus:ring-2 focus:ring-emerald-500/50"
              >
                <option value={0}>Auto-detect</option>
                {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} Worker{n > 1 ? 's' : ''}</option>)}
              </select>
            </div>

            {status?.scraping_progress && status.is_scraping_running && (
              <ScrapingProgressPanel progress={status.scraping_progress} colorClass="emerald" />
            )}

            <div className="flex gap-2">
              <button
                onClick={handleScraping}
                disabled={status?.is_scraping_running || !!error}
                className="flex-1 flex items-center justify-center gap-2 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white font-semibold px-4 py-3 rounded-xl transition-all shadow-md"
              >
                {status?.is_scraping_running ? (
                  <><Loader2 className="w-5 h-5 animate-spin" /> Running Selenium...</>
                ) : (
                  <><CheckCircle className="w-5 h-5" /> Start Price Extraction</>
                )}
              </button>
              {status?.is_scraping_running && (
                <button
                  onClick={handleStopScraping}
                  className="bg-red-50 hover:bg-red-100 text-red-600 border border-red-200 font-bold px-6 py-3 rounded-xl transition-all"
                >
                  Stop
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
