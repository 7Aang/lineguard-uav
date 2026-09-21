"""Exact pairwise distance for synchronized piecewise-linear dry-run paths.

Assumptions: simultaneous launch, constant commanded speed per segment, hold at
last waypoint. This is NOT a guarantee for asynchronous PX4 flight or wind.
"""

import math


def timed_path(assignment):
    points = [assignment.home_position, *assignment.waypoints]
    result = [(0.0, (points[0].x_m, points[0].y_m, points[0].z_m))]
    elapsed = 0.0
    for a, b in zip(points, points[1:]):
        distance = math.dist((a.x_m, a.y_m, a.z_m), (b.x_m, b.y_m, b.z_m))
        elapsed += max(1.0, distance / assignment.cruise_speed_mps)
        result.append((elapsed, (b.x_m, b.y_m, b.z_m)))
    return result


def position(path, when):
    for (t0, p0), (t1, p1) in zip(path, path[1:]):
        if when <= t1:
            ratio = max(0.0, (when - t0) / (t1 - t0))
            return tuple(x + (y - x) * ratio for x, y in zip(p0, p1))
    return path[-1][1]


def closest_approach(first, second):
    a, b = timed_path(first), timed_path(second)
    knots = sorted({t for t, _ in a + b})
    best = (math.inf, 0.0)
    for start, end in zip(knots, knots[1:]):
        pa0, pb0, pa1, pb1 = (
            position(a, start),
            position(b, start),
            position(a, end),
            position(b, end),
        )
        r = tuple(x - y for x, y in zip(pa0, pb0))
        delta = tuple((x - y) - z for x, y, z in zip(pa1, pb1, r))
        norm = sum(x * x for x in delta)
        alpha = max(0.0, min(1.0, -sum(x * y for x, y in zip(r, delta)) / norm)) if norm else 0.0
        distance = math.sqrt(sum((x + alpha * y) ** 2 for x, y in zip(r, delta)))
        if distance < best[0]:
            best = (distance, start + alpha * (end - start))
    return best
