export function element<T extends HTMLElement = HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`Missing workspace element: ${id}`);

  return found as T;
}

export function node<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  text?: string,
  className?: string,
): HTMLElementTagNameMap[K] {
  const created = document.createElement(tag);
  if (text !== undefined) created.textContent = text;
  if (className) created.className = className;

  return created;
}

export function button(text: string, onClick: () => void, className = 'plain-button'): HTMLButtonElement {
  const created = node('button', text, className);
  created.type = 'button';
  created.addEventListener('click', onClick);

  return created;
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return '—';

  return value.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatMonth(value: string | undefined): string {
  if (!value) return 'No dates';

  return new Date(`${value}-01T00:00:00Z`).toLocaleDateString(undefined, {
    month: 'short', year: 'numeric', timeZone: 'UTC',
  });
}

export function sourceLink(text: string, url: string | null | undefined): HTMLElement {
  if (!url) return node('span', text);

  try {
    const parsed = new URL(url);
    if (!['https:', 'http:'].includes(parsed.protocol)) return node('span', text);

    const link = node('a', text);
    link.href = parsed.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';

    return link;
  } catch {
    return node('span', text);
  }
}
