/* Minimal virtual list — renders only visible rows.
 * Usage: new VList(containerEl, rowHeight, { onRender, onClick })
 *   set(items, renderFn)  — replace data + re-render
 *   scrollTo(index)       — scroll item into view
 *   refresh()             — force re-render of visible range */
class VList {
  constructor(outer, rowHeight, opts = {}) {
    this._outer = outer;
    this._rh = rowHeight;
    this._overscan = opts.overscan ?? 5;
    this._onRender = opts.onRender ?? null;
    this._onClick = opts.onClick ?? null;

    this._items = [];
    this._fn = null;
    this._lastRange = [-1, -1];

    outer.style.overflowY = "auto";
    outer.style.position = "relative";

    // Inner div sets total scroll height; rail holds visible rows.
    this._inner = document.createElement("div");
    this._inner.style.cssText = "position:relative;width:100%";

    this._rail = document.createElement("div");
    this._rail.style.cssText = "position:absolute;left:0;right:0;top:0;will-change:transform";

    this._inner.appendChild(this._rail);
    outer.appendChild(this._inner);

    outer.addEventListener("scroll", () => this._update(), { passive: true });

    new ResizeObserver(() => {
      this._lastRange = [-1, -1];
      this._update();
    }).observe(outer);
  }

  set(items, fn) {
    this._items = items;
    this._fn = fn;
    this._lastRange = [-1, -1];
    this._inner.style.height = items.length * this._rh + "px";
    this._update();
  }

  refresh() {
    this._lastRange = [-1, -1];
    this._update();
  }

  scrollTo(index) {
    const y = Math.max(0, index) * this._rh;
    const oh = this._outer.clientHeight;
    const st = this._outer.scrollTop;
    if (y < st) {
      this._outer.scrollTop = y;
    } else if (y + this._rh > st + oh) {
      this._outer.scrollTop = y + this._rh - oh;
    }
  }

  _update() {
    const n = this._items.length;
    if (!n || !this._fn) {
      this._rail.innerHTML = "";
      return;
    }
    const st = this._outer.scrollTop;
    const oh = this._outer.clientHeight || 400;
    const start = Math.max(0, Math.floor(st / this._rh) - this._overscan);
    const end = Math.min(n, Math.ceil((st + oh) / this._rh) + this._overscan);

    if (start === this._lastRange[0] && end === this._lastRange[1]) return;
    this._lastRange = [start, end];

    this._rail.style.transform = "translateY(" + start * this._rh + "px)";

    const frag = document.createDocumentFragment();
    for (let i = start; i < end; i++) {
      const el = this._fn(this._items[i], i);
      el.style.height = this._rh + "px";
      el.style.overflow = "hidden";
      el.style.boxSizing = "border-box";
      if (this._onClick) {
        const item = this._items[i];
        el.addEventListener("click", (e) => this._onClick(item, i, e));
      }
      frag.appendChild(el);
    }
    this._rail.innerHTML = "";
    this._rail.appendChild(frag);

    if (this._onRender) this._onRender(start, end);
  }
}
