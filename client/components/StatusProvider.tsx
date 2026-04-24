"use client";

import React, { createContext, useContext, useEffect, useState, useRef } from 'react';
import toast from 'react-hot-toast';
import * as api from '../services/api';

interface StatusContextType {
  status: api.AppStatus | null;
  importStatus: api.ImportStatus | null;
  loading: boolean;
  error: string | null;
  refreshStatus: () => Promise<void>;
}

const StatusContext = createContext<StatusContextType | undefined>(undefined);

export function StatusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<api.AppStatus | null>(null);
  const [importStatus, setImportStatus] = useState<api.ImportStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const prevLinkRunning = useRef<boolean>(false);
  const prevScrapeRunning = useRef<boolean>(false);
  const prevLinkExtractionRunning = useRef<boolean>(false);

  const fetchStatus = async () => {
    try {
      const data = await api.getStatus();
      
      // Detection logic for checking if something JUST finished
      if (prevLinkRunning.current && !data.is_link_generation_running) {
         toast.success("Link Generation Phase completed successfully!", { duration: 5000 });
      }
      if (prevScrapeRunning.current && !data.is_scraping_running) {
         toast.success("Web Scraping Phase completed successfully!", { duration: 5000 });
      }
      if (prevLinkExtractionRunning.current && !data.is_link_extraction_running) {
         toast.success("Link Extraction completed successfully!", { duration: 5000 });
      }

      prevLinkRunning.current = data.is_link_generation_running;
      prevScrapeRunning.current = data.is_scraping_running;
      prevLinkExtractionRunning.current = data.is_link_extraction_running;

      setStatus(data);

      try {
        const impData = await api.getImportStatus();
        setImportStatus(impData);
      } catch { /* ignore if endpoint not available */ }

      if (error) {
        toast.success("Server re-connected!");
        setError(null);
      }
    } catch (err: any) {
      console.error("Failed to fetch status:", err);
      setError("Unable to connect to GSA Automation Server. Is FastAPI running?");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <StatusContext.Provider value={{ status, importStatus, loading, error, refreshStatus: fetchStatus }}>
      {children}
    </StatusContext.Provider>
  );
}

export function useStatus() {
  const context = useContext(StatusContext);
  if (context === undefined) {
    throw new Error('useStatus must be used within a StatusProvider');
  }
  return context;
}
