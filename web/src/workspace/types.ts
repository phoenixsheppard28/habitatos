import type { Feature, FeatureCollection, Geometry } from 'geojson';

export interface Dataset {
  dataset_id: string;
  version: number;
  family: string;
  source_id: string;
  description: string;
  summary: string | null;
  coverage: {
    bbox: [number, number, number, number] | null;
    start: string | null;
    end: string | null;
    species: string[];
  };
  variables: string[];
  row_count: number;
  created_at: string;
  raw_artifact_refs: string[];
}

export interface CatalogResponse {
  datasets: Dataset[];
  assistant_available: boolean;
}

export interface Observation {
  observed_at: string;
  source_record_id: string;
  dataset_version: string | number;
  entity_id?: string;
  species?: string | null;
  last_fix_at?: string;
  fix_count?: number;
  cell_id?: string;
  daily_displacement_km?: number | null;
  variable?: string;
  value?: number | null;
  unit?: string;
  quality_flag?: string;
  valid_fraction?: number;
  observed_until?: string;
}

export type ObservationFeature = Feature<Geometry, Observation>;

export interface MonthlySummary {
  month: string;
  records: number;
  entities?: number;
  fixes?: number;
  segments?: number;
  distance_km?: number | null;
  mean_distance_km?: number | null;
  variable?: string;
  cells?: number;
  mean_value?: number | null;
  measured_records?: number;
}

export interface SourceCitation {
  source: { name: string; url: string | null; study_id?: string | null } | null;
  rights: { license: string | null; attribution: string | null } | null;
}

export interface DatasetSnapshot extends FeatureCollection<Geometry, Observation> {
  dataset_id: string;
  version: number;
  monthly: MonthlySummary[];
  total_records: number;
  truncated: boolean;
  limit: number;
  grain: string;
  sources: SourceCitation[];
}

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
}

export interface ChatResponse {
  answer: string;
  updated: boolean;
  retrieved_dataset_ids?: string[];
  citations?: SourceCitation[];
}

export type View = 'map' | 'table' | 'chart' | 'overview' | 'sources';
export type LoadStatus = 'loading' | 'ready' | 'empty' | 'error';

export interface WorkspaceState {
  datasets: Dataset[];
  selected: Dataset | null;
  snapshot: DatasetSnapshot | null;
  status: LoadStatus;
  error: string | null;
  months: string[];
  monthIndex: number;
  view: View;
  assistantAvailable: boolean;
}
