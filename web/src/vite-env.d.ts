/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_GEOSERVER_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
