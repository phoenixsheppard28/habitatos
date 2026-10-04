import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { node } from './dom';

export function renderMarkdown(text: string): HTMLElement {
  const content = node('div', undefined, 'finding markdown');
  const html = marked.parse(text, { async: false, gfm: true, breaks: true });
  const fragment = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: [
      'p',
      'br',
      'strong',
      'em',
      'del',
      'a',
      'h1',
      'h2',
      'h3',
      'h4',
      'h5',
      'h6',
      'ul',
      'ol',
      'li',
      'blockquote',
      'pre',
      'code',
      'hr',
      'table',
      'thead',
      'tbody',
      'tr',
      'th',
      'td',
    ],
    ALLOWED_ATTR: ['href', 'title', 'start', 'align'],
    RETURN_DOM_FRAGMENT: true,
  });
  content.append(fragment);

  for (const link of content.querySelectorAll('a')) {
    try {
      const url = new URL(link.getAttribute('href') ?? '');
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('Unsupported link');

      link.href = url.href;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    } catch {
      link.removeAttribute('href');
    }
  }

  for (const table of content.querySelectorAll('table')) {
    const container = node('div', undefined, 'markdown-table');
    container.tabIndex = 0;
    container.setAttribute('role', 'region');
    container.setAttribute('aria-label', 'Assistant response table');
    table.replaceWith(container);
    container.append(table);
  }

  return content;
}
