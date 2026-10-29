// Preserve live nodes, focus, open details, and scroll while updating changed content.
export function patchHTML(container, html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const key = (node) =>
    node.nodeType === 1
      ? node.getAttribute("data-key") || node.id || null
      : null;
  const compatible = (a, b) =>
    a &&
    a.nodeType === b.nodeType &&
    a.nodeName === b.nodeName &&
    key(a) === key(b);
  function patch(parent, incoming) {
    const keyed = new Map(
      [...parent.childNodes].filter(key).map((node) => [key(node), node]),
    );
    let cursor = parent.firstChild;
    for (const fresh of [...incoming.childNodes]) {
      let current = key(fresh) ? keyed.get(key(fresh)) : cursor;
      if (!compatible(current, fresh)) current = fresh.cloneNode(true);
      if (current !== cursor) parent.insertBefore(current, cursor);
      if (current.nodeType === 3) {
        if (current.nodeValue !== fresh.nodeValue)
          current.nodeValue = fresh.nodeValue;
      } else if (current.nodeType === 1) {
        for (const attr of [...current.attributes]) {
          if (current.tagName === "DETAILS" && attr.name === "open") continue;
          if (!fresh.hasAttribute(attr.name))
            current.removeAttribute(attr.name);
        }
        for (const attr of [...fresh.attributes]) {
          if (current.getAttribute(attr.name) !== attr.value)
            current.setAttribute(attr.name, attr.value);
        }
        patch(current, fresh);
      }
      cursor = current.nextSibling;
    }
    while (cursor) {
      const next = cursor.nextSibling;
      cursor.remove();
      cursor = next;
    }
  }
  patch(container, template.content);
}
