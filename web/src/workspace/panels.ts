import { element } from './dom';

export class WorkspacePanels {
  private chat = element('chat');
  private workspace = document.querySelector<HTMLElement>('.workspace')!;

  constructor() {
    element('layers-toggle').addEventListener('click', () =>
      this.setSidebar(element('layers').hidden),
    );
    element('layers-close').addEventListener('click', () => this.setSidebar(false));
    element('rail-chat').addEventListener('click', () => this.showChat());
    element('chat-launcher').addEventListener('click', () => this.showChat());
    element('chat-close').addEventListener('click', () => {
      this.chat.hidden = true;
      element('chat-launcher').hidden = false;
      element('chat-launcher').focus();
    });
    element('minimize').addEventListener('click', () => {
      const collapsed = this.chat.classList.toggle('collapsed');
      const control = element('minimize');
      control.setAttribute('aria-expanded', String(!collapsed));
      control.setAttribute('aria-label', collapsed ? 'Expand assistant' : 'Minimize assistant');
      this.constrainChat();
    });
    document.querySelectorAll('[data-view]').forEach((control) => {
      control.addEventListener('click', () => {
        if (window.innerWidth <= 760) this.setSidebar(false);
      });
    });
    if (window.innerWidth <= 760) this.setSidebar(false);

    this.enableChatDrag();
    new ResizeObserver(() => this.constrainChat()).observe(this.workspace);
  }

  setSidebar(visible: boolean): void {
    element('layers').hidden = !visible;
    element('layers-toggle').setAttribute('aria-expanded', String(visible));
  }

  showChat(): void {
    if (window.innerWidth <= 760) this.setSidebar(false);

    this.chat.hidden = false;
    this.chat.classList.remove('collapsed');
    element('chat-launcher').hidden = true;
    element('minimize').setAttribute('aria-expanded', 'true');
    element('minimize').setAttribute('aria-label', 'Minimize assistant');
    this.constrainChat();
    element('question').focus();
  }

  private positionChat(left: number, top: number): void {
    const maxLeft = Math.max(0, this.workspace.clientWidth - this.chat.offsetWidth);
    const maxTop = Math.max(0, this.workspace.clientHeight - this.chat.offsetHeight);
    this.chat.style.left = `${Math.max(0, Math.min(left, maxLeft))}px`;
    this.chat.style.top = `${Math.max(0, Math.min(top, maxTop))}px`;
    this.chat.style.right = 'auto';
  }

  private constrainChat(): void {
    if (this.chat.hidden || !this.chat.style.left) return;

    const chatBounds = this.chat.getBoundingClientRect();
    const workspaceBounds = this.workspace.getBoundingClientRect();
    this.positionChat(chatBounds.left - workspaceBounds.left, chatBounds.top - workspaceBounds.top);
  }

  private enableChatDrag(): void {
    const handle = element('chat-handle');
    let dragOffset: { left: number; top: number } | null = null;
    handle.addEventListener('pointerdown', (event) => {
      if ((event.target as HTMLElement).closest('button') || event.button !== 0) return;

      const bounds = this.chat.getBoundingClientRect();
      dragOffset = {
        left: event.clientX - bounds.left,
        top: event.clientY - bounds.top,
      };
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    handle.addEventListener('pointermove', (event) => {
      if (!dragOffset) return;

      const bounds = this.workspace.getBoundingClientRect();
      this.positionChat(
        event.clientX - bounds.left - dragOffset.left,
        event.clientY - bounds.top - dragOffset.top,
      );
    });
    for (const eventName of ['pointerup', 'pointercancel', 'lostpointercapture']) {
      handle.addEventListener(eventName, () => {
        dragOffset = null;
      });
    }
    handle.addEventListener('keydown', (event) => {
      if (
        event.target !== handle ||
        !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
      )
        return;

      event.preventDefault();
      const bounds = this.chat.getBoundingClientRect();
      const workspaceBounds = this.workspace.getBoundingClientRect();
      const distance = event.shiftKey ? 40 : 12;
      const horizontal =
        event.key === 'ArrowLeft' ? -distance : event.key === 'ArrowRight' ? distance : 0;
      const vertical =
        event.key === 'ArrowUp' ? -distance : event.key === 'ArrowDown' ? distance : 0;
      this.positionChat(
        bounds.left - workspaceBounds.left + horizontal,
        bounds.top - workspaceBounds.top + vertical,
      );
    });
  }
}
