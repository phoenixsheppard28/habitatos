// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

describe('assistant Markdown', () => {
  it('renders structured responses and keeps wide tables in a scrollable region', () => {
    const content = renderMarkdown(
      [
        '## Coverage',
        '',
        '**Two** sources with *daily* observations.',
        '',
        '- Tracking',
        '- Rainfall',
        '',
        '| Source | Records |',
        '| --- | --- |',
        '| Tracking | 12 |',
        '',
        '```python',
        'print("<observation>")',
        '```',
      ].join('\n'),
    );

    expect(content.querySelector('h2')?.textContent).toBe('Coverage');
    expect(content.querySelector('strong')?.textContent).toBe('Two');
    expect(content.querySelector('em')?.textContent).toBe('daily');
    expect(content.querySelectorAll('li')).toHaveLength(2);
    expect(content.querySelector('.markdown-table table td')?.textContent).toBe('Tracking');
    expect(content.querySelector('.markdown-table')?.getAttribute('tabindex')).toBe('0');
    expect(content.querySelector('pre code')?.textContent).toBe('print("<observation>")\n');
  });

  it('removes executable HTML and unsafe URLs while retaining safe source links', () => {
    const content = renderMarkdown(
      [
        '<script>alert("unsafe")</script>',
        '<img src="x" onerror="alert(1)">',
        '<p onclick="alert(1)">Coverage</p>',
        '',
        '[Source](https://example.org/data)',
        '[Unsafe](javascript:alert%281%29)',
        '[File](file:///private/data)',
      ].join('\n'),
    );

    expect(content.querySelector('script, img, [onclick], [onerror]')).toBeNull();
    const links = Array.from(content.querySelectorAll('a'));
    expect(links[0].href).toBe('https://example.org/data');
    expect(links[0].target).toBe('_blank');
    expect(links[0].rel).toBe('noopener noreferrer');
    expect(links.slice(1).every((link) => !link.hasAttribute('href'))).toBe(true);
    expect(content.textContent).toContain('Coverage');
  });
});
