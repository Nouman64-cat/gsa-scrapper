import axios from 'axios';

const SERVER_URL = process.env.NEXT_PUBLIC_SERVER_URL || 'http://localhost:8000';

export interface LinkGenerationRequest {
  mode: 'test' | 'full' | 'custom';
  item_limit?: number;
  start_row?: number;
  end_row?: number;
}

export interface ScrapingRequest {
  mode: 'test' | 'full' | 'missing' | 'custom';
  item_limit?: number;
  start_row?: number;
  end_row?: number;
  num_workers?: number;
  sort_order?: 'low_to_high' | 'high_to_low';
  headless?: boolean;
}

export interface WorkerStatus {
  id: number;
  completed: number;
  current_part: string;
  status: string;
}

export interface ScrapingProgress {
  total: number;
  completed: number;
  successful: number;
  failed: number;
  active_workers: number;
  num_workers: number;
  elapsed_seconds: number;
  avg_seconds_per_row: number;
  estimated_remaining_seconds: number;
  workers: WorkerStatus[];
}

export interface DatabaseStatus {
  total_generated_links_count: number;
  total_successfully_scraped_links_count: number;
  total_scraped_data_records: number;
}

export interface AppStatus {
  is_link_generation_running: boolean;
  is_scraping_running: boolean;
  is_link_extraction_running: boolean;
  database: DatabaseStatus;
  scraping_progress: ScrapingProgress | null;
  link_extraction_progress: ScrapingProgress | null;  // same shape, avg_seconds_per_row = per-link
}

export interface LinkExtractionRequest {
  sort_order?: 'low_to_high' | 'high_to_low';
  num_workers?: number;
  headless?: boolean;
}

const api = axios.create({
  baseURL: SERVER_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export const getStatus = async (): Promise<AppStatus> => {
  const response = await api.get<AppStatus>('/api/status');
  return response.data;
};

export const startLinkGeneration = async (data: LinkGenerationRequest) => {
  const response = await api.post('/api/links/generate', data);
  return response.data;
};

export const startScraping = async (data: ScrapingRequest) => {
  const response = await api.post('/api/scrape/start', data);
  return response.data;
};

export const stopLinkGeneration = async () => {
  const response = await api.post('/api/links/stop');
  return response.data;
};

export const stopScraping = async () => {
  const response = await api.post('/api/scrape/stop');
  return response.data;
};

export const startLinkExtraction = async (data: LinkExtractionRequest) => {
  const response = await api.post('/api/scrape/links/start', data);
  return response.data;
};

export const stopLinkExtraction = async () => {
  const response = await api.post('/api/scrape/links/stop');
  return response.data;
};


export interface ImportStatus {
  imported_parts_count: number;
  imported_links_count: number;
  product_detail_count: number;
  search_count: number;
}

export const uploadParts = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  const response = await api.post('/api/import', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

export const uploadLinks = async (file: File) => {
  const formData = new FormData();
  formData.append('file', file);
  const response = await api.post('/api/import/links', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
  return response.data;
};

export const getImportStatus = async (): Promise<ImportStatus> => {
  const response = await api.get<ImportStatus>('/api/import/status');
  return response.data;
};

export interface ExportInfo {
  has_parts_data: boolean;
  has_links_data: boolean;
  parts_records: number;
  links_records: number;
  active_engine: 'parts' | 'links' | 'both' | 'none';
}

export const getExportInfo = async (): Promise<ExportInfo> => {
  const response = await api.get<ExportInfo>('/api/export/info');
  return response.data;
};

export interface Job {
  id: number;
  type: 'parts' | 'links';
  status: 'pending' | 'running' | 'completed' | 'failed';
  input_filename: string;
  input_row_count: number;
  has_input_file: boolean;
  has_output_file: boolean;
  output_filename: string | null;
  created_at: string;
  completed_at: string | null;
}

export const getJobs = async (): Promise<Job[]> => {
  const response = await api.get<Job[]>('/api/jobs');
  return response.data;
};

export const getJobInputUrl = async (jobId: number): Promise<string> => {
  const response = await api.get<{ url: string }>(`/api/jobs/${jobId}/input-url`);
  return response.data.url;
};

export const getJobOutputUrl = async (jobId: number): Promise<string> => {
  const response = await api.get<{ url: string }>(`/api/jobs/${jobId}/output-url`);
  return response.data.url;
};

export const downloadExport = async () => {
  // Use axios instead of window location so we can await the download completion for loading states
  const response = await api.get('/api/export', { responseType: 'blob' });
  
  const url = window.URL.createObjectURL(new Blob([response.data]));
  const link = document.createElement('a');
  link.href = url;
  
  let filename = 'GSA_Export.xlsx';
  const contentDisposition = response.headers['content-disposition'];
  if (contentDisposition) {
    const filenameMatch = contentDisposition.match(/filename="?([^"]+)"?/);
    if (filenameMatch && filenameMatch.length > 1) {
      filename = filenameMatch[1];
    }
  }
  
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link?.parentNode?.removeChild(link);
  window.URL.revokeObjectURL(url);
};

