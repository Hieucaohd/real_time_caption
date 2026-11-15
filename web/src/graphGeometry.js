export const WIDTH = 236,
  HEIGHT = 78,
  GAP_X = 72,
  GAP_Y = 28;
export const clamp = (n, min, max) => Math.max(min, Math.min(max, n));
export const worldPoint = (p, camera) => ({
  x: (p.x - camera.x) / camera.zoom,
  y: (p.y - camera.y) / camera.zoom,
});
export function zoomAt(camera, point, nextZoom) {
  const zoom = clamp(nextZoom, 0.1, 4),
    world = worldPoint(point, camera);
  return { zoom, x: point.x - world.x * zoom, y: point.y - world.y * zoom };
}
export function descendants(nodes, start) {
  const result = new Set(start),
    children = new Map();
  for (const n of nodes) {
    if (!children.has(n.parent_id)) children.set(n.parent_id, []);
    children.get(n.parent_id).push(n.id);
  }
  const queue = [...start];
  for (let i = 0; i < queue.length; i++)
    for (const id of children.get(queue[i]) || [])
      if (!result.has(id)) {
        result.add(id);
        queue.push(id);
      }
  return result;
}
export function visibleNodes(nodes, collapsed) {
  const hidden = new Set();
  for (const id of collapsed)
    for (const child of descendants(nodes, [id]))
      if (child !== id) hidden.add(child);
  return nodes.filter((n) => !hidden.has(n.id));
}
export function autoLayout(nodes, rootId) {
  const children = new Map(),
    points = {};
  for (const n of nodes) {
    if (!children.has(n.parent_id)) children.set(n.parent_id, []);
    children.get(n.parent_id).push(n.id);
  }
  let row = 0;
  function place(id, depth) {
    const kids = (children.get(id) || []).map((child) =>
      place(child, depth + 1),
    );
    const y = kids.length
      ? (kids[0].y + kids.at(-1).y) / 2
      : row++ * (HEIGHT + GAP_Y);
    return (points[id] = { x: depth * (WIDTH + GAP_X), y });
  }
  if (nodes.some((n) => n.id === rootId)) place(rootId, 0);
  return points;
}
export function addMissingPositions(nodes, rootId, existing) {
  if (!Object.keys(existing).length) return autoLayout(nodes, rootId);
  const positions = { ...existing };
  const occupied = (p) =>
    Object.values(positions).some(
      (q) =>
        p.x < q.x + WIDTH + 20 &&
        p.x + WIDTH + 20 > q.x &&
        p.y < q.y + HEIGHT + 20 &&
        p.y + HEIGHT + 20 > q.y,
    );
  for (const n of nodes)
    if (!positions[n.id]) {
      const parent = positions[n.parent_id];
      const p = { x: parent ? parent.x + WIDTH + GAP_X : 0, y: parent?.y || 0 };
      while (occupied(p)) p.y += HEIGHT + GAP_Y;
      positions[n.id] = p;
    }
  return positions;
}
export function bounds(nodes, positions) {
  const points = nodes.map((n) => positions[n.id]).filter(Boolean);
  if (!points.length) return { x: 0, y: 0, width: WIDTH, height: HEIGHT };
  const x = Math.min(...points.map((p) => p.x)),
    y = Math.min(...points.map((p) => p.y));
  return {
    x,
    y,
    width: Math.max(...points.map((p) => p.x)) + WIDTH - x,
    height: Math.max(...points.map((p) => p.y)) + HEIGHT - y,
  };
}
export function fitCamera(box, size) {
  const zoom = clamp(
    Math.min((size.width - 80) / box.width, (size.height - 80) / box.height),
    0.1,
    1.25,
  );
  return {
    zoom,
    x: size.width / 2 - (box.x + box.width / 2) * zoom,
    y: size.height / 2 - (box.y + box.height / 2) * zoom,
  };
}
export function edgePath(a, b) {
  let x1 = a.x + WIDTH / 2,
    y1 = a.y + HEIGHT / 2,
    x2 = b.x + WIDTH / 2,
    y2 = b.y + HEIGHT / 2;
  if (Math.abs(x2 - x1) > Math.abs(y2 - y1)) {
    const sign = x2 >= x1 ? 1 : -1;
    x1 += (sign * WIDTH) / 2;
    x2 -= (sign * WIDTH) / 2;
    const bend = Math.max(40, Math.abs(x2 - x1) * 0.45);
    return `M ${x1} ${y1} C ${x1 + sign * bend} ${y1}, ${x2 - sign * bend} ${y2}, ${x2} ${y2}`;
  }
  const sign = y2 >= y1 ? 1 : -1;
  y1 += (sign * HEIGHT) / 2;
  y2 -= (sign * HEIGHT) / 2;
  const bend = Math.max(40, Math.abs(y2 - y1) * 0.45);
  return `M ${x1} ${y1} C ${x1} ${y1 + sign * bend}, ${x2} ${y2 - sign * bend}, ${x2} ${y2}`;
}
