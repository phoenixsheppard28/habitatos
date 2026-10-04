import { element, formatMonth } from './dom';
import type { WorkspaceStore } from './store';
import type { DatasetSnapshot } from './types';

export class WorkspaceTimeline {
  private timer: ReturnType<typeof setInterval> | null = null;
  private play = element<HTMLButtonElement>('play');
  private slider = element<HTMLInputElement>('month');
  private snapshot: DatasetSnapshot | null = null;

  constructor(private store: WorkspaceStore) {
    store.subscribe((state) => {
      if (this.snapshot !== state.snapshot) this.stop();
      this.snapshot = state.snapshot;
      this.slider.max = String(Math.max(0, state.months.length - 1));
      this.slider.value = String(state.monthIndex);
      this.slider.disabled = state.months.length < 2;
      this.play.disabled = state.months.length < 2;
      element('when').textContent = formatMonth(store.through);
      element('timeline-start').textContent = formatMonth(state.months[0]);
      element('timeline-end').textContent = formatMonth(state.months.at(-1));
    });
    this.slider.addEventListener('input', () => {
      this.stop();
      store.setMonth(Number(this.slider.value));
    });
    this.play.addEventListener('click', () => {
      if (this.timer) this.stop();
      else this.start();
    });
    element('refresh').addEventListener('click', () => this.stop());
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.play.textContent = 'Play';
    this.play.setAttribute('aria-pressed', 'false');
  }

  private start(): void {
    if (this.store.state.months.length < 2) return;
    if (this.store.state.monthIndex >= this.store.state.months.length - 1) this.store.setMonth(0);

    this.play.textContent = 'Pause';
    this.play.setAttribute('aria-pressed', 'true');
    this.timer = setInterval(() => {
      const next = this.store.state.monthIndex + 1;
      if (next >= this.store.state.months.length) this.stop();
      else this.store.setMonth(next);
    }, 900);
  }
}
