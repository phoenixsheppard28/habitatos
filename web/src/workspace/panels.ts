import { element } from './dom';

type DockSide = 'left' | 'right';

export class WorkspacePanels {
  private chat = element('chat');
  private workspace = document.querySelector<HTMLElement>('.workspace')!;
  private floatingPosition: { left: number; top: number } | null = null;
  private snapSide: DockSide | null = null;

  constructor() {
    element('layers-toggle').addEventListener('click', () =>
      this.setSidebar(element('layers').hidden),
    );
    element('layers-close').addEventListener('click', () => this.setSidebar(false));
    element('rail-chat').addEventListener('click', () => this.showChat());
    element('chat-launcher').addEventListener('click', () => this.showChat());
    element('chat-dock').addEventListener('click', () => {
      if (this.chat.classList.contains('docked')) this.undockChat();
      else this.dockChat('right');
    });
    element('chat-close').addEventListener('click', () => {
      this.chat.hidden = true;
      this.previewSnap(null);
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
    if (this.chat.hidden) return;
    if (this.chat.classList.contains('docked')) {
      if (window.innerWidth <= 1100) this.setSidebar(false);

      return;
    }
    if (!this.chat.style.left) return;

    const chatBounds = this.chat.getBoundingClientRect();
    const workspaceBounds = this.workspace.getBoundingClientRect();
    this.positionChat(chatBounds.left - workspaceBounds.left, chatBounds.top - workspaceBounds.top);
  }

  private dockChat(side: DockSide): void {
    if (!this.chat.classList.contains('docked')) {
      const bounds = this.chat.getBoundingClientRect();
      const workspaceBounds = this.workspace.getBoundingClientRect();
      this.floatingPosition = {
        left: bounds.left - workspaceBounds.left,
        top: bounds.top - workspaceBounds.top,
      };
    }
    if (window.innerWidth <= 1100) this.setSidebar(false);

    this.chat.classList.add('docked');
    this.chat.dataset.dock = side;
    this.chat.style.removeProperty('left');
    this.chat.style.removeProperty('top');
    this.chat.style.removeProperty('right');
    this.previewSnap(null);
    this.updateDockControl();
  }

  private undockChat(): void {
    this.chat.classList.remove('docked');
    delete this.chat.dataset.dock;
    this.updateDockControl();
    if (this.floatingPosition) {
      this.positionChat(this.floatingPosition.left, this.floatingPosition.top);
    }
  }

  private updateDockControl(): void {
    const docked = this.chat.classList.contains('docked');
    const control = element('chat-dock');
    const label = docked ? 'Float assistant' : 'Dock assistant to the right';
    control.setAttribute('aria-pressed', String(docked));
    control.setAttribute('aria-label', label);
    control.title = label;
  }

  private previewSnap(side: DockSide | null): void {
    this.snapSide = side;
    if (side) this.workspace.dataset.chatSnap = side;
    else delete this.workspace.dataset.chatSnap;
  }

  private enableChatDrag(): void {
    const handle = element('chat-handle');
    let drag: {
      left: number;
      top: number;
      startX: number;
      startY: number;
      width: number;
      moved: boolean;
    } | null = null;
    handle.addEventListener('pointerdown', (event) => {
      if ((event.target as HTMLElement).closest('button') || event.button !== 0) return;

      const bounds = this.chat.getBoundingClientRect();
      drag = {
        left: event.clientX - bounds.left,
        top: event.clientY - bounds.top,
        startX: event.clientX,
        startY: event.clientY,
        width: bounds.width,
        moved: false,
      };
      handle.setPointerCapture(event.pointerId);
      event.preventDefault();
    });
    handle.addEventListener('pointermove', (event) => {
      if (!drag) return;
      if (!drag.moved && Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) < 4)
        return;

      drag.moved = true;
      if (this.chat.classList.contains('docked')) {
        this.undockChat();
        drag.left *= this.chat.offsetWidth / drag.width;
      }

      const bounds = this.workspace.getBoundingClientRect();
      this.positionChat(
        event.clientX - bounds.left - drag.left,
        event.clientY - bounds.top - drag.top,
      );
      const snapSide =
        event.clientX <= bounds.left + 48
          ? 'left'
          : event.clientX >= bounds.right - 48
            ? 'right'
            : null;
      this.previewSnap(snapSide);
    });
    handle.addEventListener('pointerup', (event) => {
      if (drag?.moved && this.snapSide) this.dockChat(this.snapSide);

      drag = null;
      this.previewSnap(null);
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    });
    for (const eventName of ['pointercancel', 'lostpointercapture']) {
      handle.addEventListener(eventName, () => {
        drag = null;
        this.previewSnap(null);
      });
    }
    handle.addEventListener('keydown', (event) => {
      if (
        event.target !== handle ||
        !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)
      )
        return;

      event.preventDefault();
      if (this.chat.classList.contains('docked')) this.undockChat();

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
