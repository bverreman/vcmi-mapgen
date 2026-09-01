"""Generic zone-tile-set primitives shared across the gameplay/pickup/repair/vegetation
steps: neighbourhoods, edge distance, and the open-run-length statistic."""
import collections

NB8 = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

EBINS = 6      # edge-distance bins (0..4, 5+)


def edge_dist(ts):
    """Chebyshev distance from each zone tile to the nearest NON-zone tile (the rim)."""
    d = {}
    q = collections.deque()
    for (x, y) in ts:
        if any((x + dx, y + dy) not in ts for dx in (-1, 0, 1) for dy in (-1, 0, 1)):
            d[(x, y)] = 0
            q.append((x, y))
    while q:
        x, y = q.popleft()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                n = (x + dx, y + dy)
                if n in ts and n not in d:
                    d[n] = d[(x, y)] + 1
                    q.append(n)
    return d


def run_lengths(ts, O):
    """Open-run-length histogram (horizontal + vertical) over a field."""
    h = collections.Counter()
    xs = [x for x, _ in ts]; ys = [y for _, y in ts]
    for y in range(min(ys), max(ys) + 1):
        run = 0
        for x in range(min(xs), max(xs) + 2):
            if (x, y) in ts and (x, y) in O:
                run += 1
            else:
                if run:
                    h[run] += 1
                run = 0
    for x in range(min(xs), max(xs) + 1):
        run = 0
        for y in range(min(ys), max(ys) + 2):
            if (x, y) in ts and (x, y) in O:
                run += 1
            else:
                if run:
                    h[run] += 1
                run = 0
    return h
