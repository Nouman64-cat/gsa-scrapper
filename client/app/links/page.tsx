"use client";

import React, { useState, useRef } from 'react';
import { Link2, Upload, Loader2, RefreshCw, Search, Link as LinkIcon } from 'lucide-react';
import toast from 'react-hot-toast';
import * as api from '../../services/api';
import { useStatus } from '@/components/StatusProvider';
import { StatCard, ScrapingProgressPanel } from '@/components/Common';

export default function LinksPage() {
  const { status, importStatus, error, refreshStatus } = useStatus();
  const [numLinkWorkers, setNumLinkWorkers] = useState(0);
  const [linkSortOrder, setLinkSortOrder] = useState<'low_to_high' | 'high_to_low'>('low_to_high');
  const [isUploadingLinks, setIsUploadingLinks] = useState(false);
  const linksFileInputRef = useRef<HTMLInputElement>(null);

  const handleLinksUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      setIsUploadingLinks(true);
      toast.loading("Uploading and importing links data...", { id: 'importLinks' });
      const result = await api.uploadLinks(file);
      toast.success(
        `Imported ${result.rows_imported.toLocaleString()} links (${result.product_detail_links} product detail, ${result.search_links} search)`,
        { id: 'importLinks', duration: 5000 }
      );
      refreshStatus();
    } catch (err: any) {
      toast.error(`Import failed: ${err?.response?.data?.detail || err.message}`, { id: 'importLinks' });
    } finally {
      setIsUploadingLinks(false);
      if (linksFileInputRef.current) linksFileInputRef.current.value = '';
    }
  };

  const handleLinkExtraction = async () => {
    try {
      const workers = numLinkWorkers > 0 ? numLinkWorkers : undefined;
      toast.loading(`Initiating Link Extraction${workers ? ` (${workers} workers)` : ''}...`, { id: 'linkExtract' });
      await api.startLinkExtraction({ sort_order: linkSortOrder, num_workers: workers });
      toast.success("Link Extraction queued successfully!", { id: 'linkExtract' });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Error starting Link Extraction: ${err?.response?.data?.detail || err.message}`, { id: 'linkExtract' });
    }
  };

  const handleStopLinkExtraction = async () => {
    try {
      toast.loading("Sending stop signal to Link Extractor...", { id: 'linkExtractStop' });
      await api.stopLinkExtraction();
      toast.success("Stop signal received. Extractor will close soon.", { id: 'linkExtractStop' });
      refreshStatus();
    } catch (err: any) {
      toast.error(`Stop failed: ${err.message}`, { id: 'linkExtractStop' });
    }
  };

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight flex items-center gap-3 text-slate-800">
          <Link2 className="w-8 h-8 text-indigo-600" />
          Links Management
        </h1>
        <p className="text-slate-500 mt-2 text-sm">Manage internal links and extract data from previously discovered pages.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <StatCard
          title="Product Detail Links"
          value={importStatus?.product_detail_count ?? 0}
          icon={<LinkIcon className="w-5 h-5" />}
          color="text-indigo-600"
          bg="bg-indigo-50 border border-indigo-100"
        />
        <StatCard
          title="Search Links"
          value={importStatus?.search_count ?? 0}
          icon={<Search className="w-5 h-5" />}
          color="text-cyan-600"
          bg="bg-cyan-50 border border-cyan-100"
        />
        <StatCard
          title="Extracted Records"
          value={status?.database.total_scraped_data_records}
          icon={<RefreshCw className="w-5 h-5" />}
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
                <Upload className="w-5 h-5 text-indigo-600" />
                Import Links
              </h2>
              <p className="text-slate-500 text-sm mt-1">
                Upload Excel with <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs font-mono">Internal Link URL</code> or <code className="bg-slate-100 px-1.5 py-0.5 rounded text-xs font-mono">Manufacturer Part Number</code>.
              </p>
            </div>
            <div>
              <input
                ref={linksFileInputRef}
                type="file"
                accept=".xlsx,.xls"
                onChange={handleLinksUpload}
                className="hidden"
              />
              <button
                onClick={() => linksFileInputRef.current?.click()}
                disabled={isUploadingLinks || status?.is_link_extraction_running}
                className="w-full flex items-center justify-center gap-2 bg-indigo-500 hover:bg-indigo-600 disabled:opacity-50 text-white font-semibold px-5 py-3 rounded-xl transition-all shadow-md"
              >
                {isUploadingLinks ? (
                  <><Loader2 className="w-5 h-5 animate-spin" /> Importing...</>
                ) : (
                  <><Upload className="w-5 h-5" /> Upload Links .XLSX</>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Link Extraction Panel */}
        <div className="bg-white border border-slate-200 shadow-sm rounded-2xl p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-xl font-bold flex items-center gap-2 text-slate-800">
              <Search className="w-5 h-5 text-indigo-600" />
              Link Extraction
            </h2>
            {status?.is_link_extraction_running && (
              <span className="flex items-center gap-2 text-xs font-semibold bg-indigo-50 text-indigo-700 px-3 py-1 rounded-full border border-indigo-200">
                <RefreshCw className="w-3 h-3 animate-spin" /> Extracting...
              </span>
            )}
          </div>
          
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="flex flex-col gap-2">
                <label className="text-xs uppercase font-bold text-slate-500 tracking-wider">Price Sort Order</label>
                <select
                  value={linkSortOrder}
                  onChange={(e) => setLinkSortOrder(e.target.value as 'low_to_high' | 'high_to_low')}
                  disabled={status?.is_link_extraction_running}
                  className="bg-white border border-slate-200 text-sm rounded-lg px-4 py-2.5"
                >
                  <option value="low_to_high">Low to High</option>
                  <option value="high_to_low">High to Low</option>
                </select>
              </div>
              <div className="flex flex-col gap-2">
                <label className="text-xs uppercase font-bold text-slate-500 tracking-wider">Parallel Workers</label>
                <select
                  value={numLinkWorkers}
                  onChange={(e) => setNumLinkWorkers(Number(e.target.value))}
                  disabled={status?.is_link_extraction_running}
                  className="bg-white border border-slate-200 text-sm rounded-lg px-4 py-2.5"
                >
                  <option value={0}>Auto-detect</option>
                  {[1, 2, 3, 4, 5].map(n => <option key={n} value={n}>{n} Worker{n > 1 ? 's' : ''}</option>)}
                </select>
              </div>
            </div>

            {status?.link_extraction_progress && status.is_link_extraction_running && (
              <ScrapingProgressPanel progress={status.link_extraction_progress} colorClass="indigo" />
            )}

            <div className="flex gap-2">
              <button
                onClick={handleLinkExtraction}
                disabled={status?.is_link_extraction_running || status?.is_scraping_running || !!error}
                className="flex-1 flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 text-white font-semibold px-4 py-3 rounded-xl transition-all shadow-md"
              >
                {status?.is_link_extraction_running ? (
                  <><Loader2 className="w-5 h-5 animate-spin" /> Running Link Scraper...</>
                ) : (
                  <><LinkIcon className="w-5 h-5" /> Start Link Extraction</>
                )}
              </button>
              {status?.is_link_extraction_running && (
                <button
                  onClick={handleStopLinkExtraction}
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
