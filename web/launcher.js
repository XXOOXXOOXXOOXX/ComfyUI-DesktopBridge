// A click opens the connection dialog; a deliberate drag only moves the launcher.
export function movableLauncher(element, key = "desktop-bridge-launcher-position") {
    let drag, suppressClick = false, disposed = false;
    const clamp = (x, y) => ({ x: Math.max(0, Math.min(x, innerWidth - element.offsetWidth)), y: Math.max(0, Math.min(y, innerHeight - element.offsetHeight)) });
    const place = position => { const next = clamp(position.x, position.y); Object.assign(element.style, { left: next.x + "px", top: next.y + "px", bottom: "auto", right: "auto" }); return next; };
    const save = position => { try { localStorage.setItem(key, JSON.stringify(position)); } catch { /* Movement still works when storage is disabled. */ } };
    try { const position = JSON.parse(localStorage.getItem(key)); if (Number.isFinite(position?.x) && Number.isFinite(position?.y)) place(position); } catch { /* Use the initial bottom-left position. */ }
    element.style.touchAction = "none"; element.style.userSelect = "none"; element.style.cursor = "grab";
    const down = event => {
        if (event.button !== 0) return;
        event.stopPropagation(); suppressClick = false;
        const rect = element.getBoundingClientRect();
        drag = { id: event.pointerId, x: event.clientX, y: event.clientY, left: rect.left, top: rect.top, moved: false };
        element.setPointerCapture(event.pointerId);
    };
    const move = event => {
        if (!drag || event.pointerId !== drag.id) return;
        const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) < 5) return;
        drag.moved = true; event.preventDefault(); event.stopPropagation(); element.style.cursor = "grabbing";
        place({ x: drag.left + dx, y: drag.top + dy });
    };
    const end = event => {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.moved) { const rect = element.getBoundingClientRect(); save(place({ x: rect.left, y: rect.top })); suppressClick = true; }
        drag = null; element.style.cursor = "grab";
        if (element.hasPointerCapture(event.pointerId)) element.releasePointerCapture(event.pointerId);
    };
    const click = event => { if (suppressClick && event.detail !== 0) { event.preventDefault(); event.stopImmediatePropagation(); } suppressClick = false; };
    const resize = () => { if (disposed) return; const rect = element.getBoundingClientRect(); save(place({ x: rect.left, y: rect.top })); };
    element.addEventListener("pointerdown", down); element.addEventListener("pointermove", move);
    element.addEventListener("pointerup", end); element.addEventListener("pointercancel", end); element.addEventListener("click", click, true);
    addEventListener("resize", resize);
    return () => { disposed = true; removeEventListener("resize", resize); element.removeEventListener("pointerdown", down); element.removeEventListener("pointermove", move); element.removeEventListener("pointerup", end); element.removeEventListener("pointercancel", end); element.removeEventListener("click", click, true); };
}
