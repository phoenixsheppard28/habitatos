import '../workspace.css';
import { WorkspaceAssistant } from './assistant';
import { element, node } from './dom';
import { parseGeographicFile } from './imports';
import { WorkspaceMap } from './map';
import { WorkspacePanels } from './panels';
import { WorkspaceSidebar } from './sidebar';
import { WorkspaceStore } from './store';
import { WorkspaceTimeline } from './timeline';
import { WorkspaceViews } from './views';

const store = new WorkspaceStore();
const panels = new WorkspacePanels();
const map = new WorkspaceMap(store);
const sidebar = new WorkspaceSidebar(store, panels);
const assistant = new WorkspaceAssistant(store, panels);
new WorkspaceViews(store, (question) => {
  void assistant.ask(question);
});
new WorkspaceTimeline(store);

const fileInput = element<HTMLInputElement>('file-input');
for (const id of ['rail-add', 'attach'])
  element(id).addEventListener('click', () => fileInput.click());

fileInput.addEventListener('change', async () => {
  const file = fileInput.files?.[0];
  if (!file) return;

  try {
    if (file.size > 5 * 1024 * 1024) throw new Error('Import a file smaller than 5 MB.');

    const data = parseGeographicFile(await file.text(), file.name);
    const layer = map.addImport(data, file.name);
    sidebar.addImport(file.name, layer, map);
    assistant.notify(
      `Imported ${data.features.length} geographic features from ${file.name}. ` +
        'The file remains on this device. Database analysis and exports use the selected published dataset.',
    );
  } catch (error) {
    assistant.notify(error instanceof Error ? error.message : 'Cannot import this file.');
  } finally {
    fileInput.value = '';
  }
});

element('refresh').addEventListener('click', () => {
  void store.refresh();
});
element('export').addEventListener('click', () => {
  const { selected, snapshot } = store.state;
  if (!selected || !snapshot || !store.visibleFeatures.length) return;

  const data = {
    type: 'FeatureCollection',
    features: store.visibleFeatures,
    dataset_id: selected.dataset_id,
    dataset_version: snapshot.version,
    through: store.through,
    grain: snapshot.grain,
    truncated: snapshot.truncated,
    sources: snapshot.sources,
  };
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/geo+json' }),
  );
  const link = node('a');
  link.href = url;
  link.download = `habitat-${selected.source_id}-${store.through}.geojson`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

void store.refresh();
