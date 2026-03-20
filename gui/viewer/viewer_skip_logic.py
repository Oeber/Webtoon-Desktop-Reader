def merge_close_targets(targets: list[int], min_gap: int) -> list[int]:
    if not targets:
        return []

    merged = [targets[0]]
    for target in targets[1:]:
        if target - merged[-1] < min_gap:
            continue
        merged.append(target)
    return merged


def visible_overlap(start: int, end: int, top: int, view_h: int) -> int:
    bottom = top + view_h
    return max(0, min(end, bottom) - max(start, top))


def score_panel_window(
    panels: list[tuple[int, int]],
    top: int,
    view_h: int,
    anchor: tuple[int, int] | None = None,
) -> int:
    bottom = top + view_h
    covered = 0
    intervals = []

    for start, end in panels:
        if end <= top:
            continue
        if start >= bottom:
            break
        clipped_start = max(start, top)
        clipped_end = min(end, bottom)
        if clipped_end <= clipped_start:
            continue
        covered += clipped_end - clipped_start
        intervals.append((clipped_start, clipped_end))

    if not intervals:
        return 0

    internal_blank = 0
    for index in range(1, len(intervals)):
        internal_blank = max(internal_blank, intervals[index][0] - intervals[index - 1][1])

    leading_blank = max(0, intervals[0][0] - top)
    trailing_blank = max(0, bottom - intervals[-1][1])
    balance_penalty = abs(leading_blank - trailing_blank) // 3
    anchor_penalty = 0
    edge_clip_penalty = 0

    edge_pad = max(24, int(view_h * 0.06))
    if intervals[0][0] <= top + edge_pad:
        edge_clip_penalty += edge_pad - max(0, intervals[0][0] - top)
    if intervals[-1][1] >= bottom - edge_pad:
        edge_clip_penalty += edge_pad - max(0, bottom - intervals[-1][1])

    if anchor is not None:
        anchor_start, anchor_end = anchor
        anchor_mid = (anchor_start + anchor_end) / 2
        window_mid = top + (view_h / 2)
        anchor_penalty = int(abs(anchor_mid - window_mid) * 0.35)

    return covered - (internal_blank * 2) - (leading_blank // 3) - (trailing_blank // 4) - balance_penalty - anchor_penalty - edge_clip_penalty


def visible_content_overlap_between_windows(
    panels: list[tuple[int, int]],
    top_a: int,
    top_b: int,
    view_h: int,
) -> int:
    overlap_top = max(top_a, top_b)
    overlap_bottom = min(top_a + view_h, top_b + view_h)
    if overlap_bottom <= overlap_top:
        return 0

    covered = 0
    for start, end in panels:
        if end <= overlap_top:
            continue
        if start >= overlap_bottom:
            break
        covered += max(0, min(end, overlap_bottom) - max(start, overlap_top))
    return covered


def lowest_fully_visible_panel_end(panels: list[tuple[int, int]], top: int, view_h: int) -> int | None:
    bottom = top + view_h
    margin = max(12, int(view_h * 0.03))
    best_end = None

    for start, end in panels:
        panel_h = end - start
        if panel_h <= 0 or panel_h > int(view_h * 0.98):
            continue
        if start >= top + margin and end <= bottom - margin:
            best_end = end if best_end is None else max(best_end, end)

    return best_end


def bottom_carryover_panel(
    panels: list[tuple[int, int]],
    top: int,
    view_h: int,
) -> tuple[int, int] | None:
    bottom = top + view_h
    min_visible = max(24, int(view_h * 0.04))
    min_remaining = max(48, int(view_h * 0.08))
    candidate = None
    candidate_start = None

    for start, end in panels:
        visible = visible_overlap(start, end, top, view_h)
        if visible < min_visible:
            continue
        if start >= bottom:
            continue
        if end <= bottom:
            continue
        if (end - bottom) < min_remaining:
            continue
        if candidate is None or start > candidate_start:
            candidate = (start, end)
            candidate_start = start

    return candidate


def carryover_target(
    panels: list[tuple[int, int]],
    panel: tuple[int, int],
    view_h: int,
    max_scroll: int,
    current_top: int | None = None,
) -> int:
    start, end = panel
    panel_h = end - start
    if panel_h <= 0:
        return max(0, min(start, max_scroll))

    anchor = (start, end)
    base_candidates = {max(0, min(start - int(view_h * 0.06), max_scroll))}

    if panel_h <= view_h:
        base_candidates.add(max(0, min(int((start + end - view_h) / 2), max_scroll)))

    if current_top is not None:
        base_candidates.add(max(0, min(current_top + int(view_h * 0.52), max_scroll)))
        base_candidates.add(max(0, min(current_top + int(view_h * 0.68), max_scroll)))
        base_candidates.add(max(0, min(current_top + int(view_h * 0.80), max_scroll)))
        base_candidates.add(max(0, min(current_top + int(view_h * 0.90), max_scroll)))

    if panel_h > view_h:
        base_candidates.add(max(0, min(end - int(view_h * 0.74), max_scroll)))
        base_candidates.add(max(0, min(end - int(view_h * 0.66), max_scroll)))
        base_candidates.add(max(0, min(end - int(view_h * 0.58), max_scroll)))
        base_candidates.add(max(0, min(end - int(view_h * 0.48), max_scroll)))
        base_candidates.add(max(0, min(end - int(view_h * 0.40), max_scroll)))
        base_candidates.add(max(0, min(start + int(panel_h * 0.45) - int(view_h * 0.35), max_scroll)))
        base_candidates.add(max(0, min(start + int(panel_h * 0.58) - int(view_h * 0.30), max_scroll)))

    best_target = None
    best_score = None
    min_visible = max(140, min(panel_h, int(view_h * 0.32)))
    min_downward_target = None

    if current_top is not None and panel_h > view_h:
        min_downward_target = min(
            max_scroll,
            max(
                current_top + int(view_h * 0.72),
                start + int(panel_h * 0.18),
            ),
        )

    for candidate in base_candidates:
        if min_downward_target is not None and candidate < min_downward_target:
            continue
        if visible_overlap(start, end, candidate, view_h) < min_visible:
            continue
        score = score_panel_window(panels, candidate, view_h, anchor=anchor)
        if current_top is not None and panel_h > view_h:
            downward_bias = max(0, candidate - current_top)
            score += min(int(view_h * 0.34), downward_bias)
        if best_score is None or score > best_score or (score == best_score and candidate > best_target):
            best_target = candidate
            best_score = score

    if min_downward_target is not None:
        fallback = min_downward_target
    else:
        fallback = int((start + end - view_h) / 2) if panel_h <= view_h else end - int(view_h * 0.58)
    return max(0, min(best_target if best_target is not None else fallback, max_scroll))


def best_existing_target_for_panel(
    targets: list[int],
    panels: list[tuple[int, int]],
    panel: tuple[int, int],
    current_top: int,
    view_h: int,
) -> int | None:
    if not targets:
        return None

    start, end = panel
    panel_h = end - start
    min_move = max(24, int(view_h * 0.04))
    min_visible = max(140, min(panel_h, int(view_h * 0.32)))
    desired_center = int((start + end - view_h) / 2)
    prefer_centering = panel_h <= int(view_h * 1.22)
    best_target = None
    best_score = None

    for candidate in targets:
        if candidate <= current_top + min_move:
            continue
        if visible_overlap(start, end, candidate, view_h) < min_visible:
            continue

        score = score_panel_window(panels, candidate, view_h, anchor=panel)
        downward_bias = max(0, candidate - current_top)
        score += min(int(view_h * 0.34), downward_bias)
        if prefer_centering:
            score -= abs(candidate - desired_center)

        if best_score is None or score > best_score or (score == best_score and candidate > best_target):
            best_target = candidate
            best_score = score

    return best_target


def nearest_existing_target_for_panel(
    targets: list[int],
    panel: tuple[int, int],
    current_top: int,
    view_h: int,
    min_top: int = 0,
) -> int | None:
    if not targets:
        return None

    start, end = panel
    panel_h = end - start
    min_move = max(24, int(view_h * 0.04))
    min_visible = max(120, min(panel_h, int(view_h * 0.24)))
    desired_center = int((start + end - view_h) / 2)
    prefer_centering = panel_h <= int(view_h * 1.22)
    best_target = None
    best_score = None

    for candidate in targets:
        if candidate <= current_top + min_move:
            continue
        if candidate < min_top:
            continue
        if visible_overlap(start, end, candidate, view_h) < min_visible:
            continue
        if not prefer_centering:
            return candidate

        score = -abs(candidate - desired_center)
        downward_bias = max(0, candidate - current_top)
        score += min(int(view_h * 0.18), downward_bias)
        if best_score is None or score > best_score or (score == best_score and candidate > best_target):
            best_target = candidate
            best_score = score

    return best_target


def edge_safe_target(
    targets: list[int],
    panels: list[tuple[int, int]],
    candidate: int,
    current_top: int,
    view_h: int,
) -> int:
    if not targets:
        return candidate

    window_bottom = candidate + view_h
    edge_pad = max(24, int(view_h * 0.05))
    max_adjust = max(40, int(view_h * 0.22))

    top_cut = any(
        start < candidate and start >= current_top and end > candidate + edge_pad
        for start, end in panels
    )
    bottom_cut = any(
        start < window_bottom - edge_pad and start > candidate and end > window_bottom
        for start, end in panels
    )

    if not top_cut and not bottom_cut:
        return candidate

    nearby = [
        target for target in targets
        if target >= current_top and abs(target - candidate) <= max_adjust
    ]
    if not nearby:
        return candidate

    def edge_penalty(top: int) -> tuple[int, int]:
        bottom = top + view_h
        top_hits = sum(
            1 for start, end in panels
            if start < top and start >= current_top and end > top + edge_pad
        )
        bottom_hits = sum(
            1 for start, end in panels
            if start < bottom - edge_pad and start > top and end > bottom
        )
        return (top_hits + bottom_hits, abs(top - candidate))

    return min(nearby, key=edge_penalty)


def next_panel_after(panels: list[tuple[int, int]], y: int, min_gap: int = 0) -> tuple[int, int] | None:
    threshold = y + max(0, min_gap)
    for start, end in panels:
        if start >= threshold:
            return (start, end)
    return None


def expand_panel_to_cluster(
    panels: list[tuple[int, int]],
    panel: tuple[int, int],
    view_h: int,
) -> tuple[int, int]:
    try:
        index = panels.index(panel)
    except ValueError:
        return panel

    cluster_start, cluster_end = panel
    gap_limit = int(view_h * 0.14)
    short_limit = int(view_h * 0.34)

    probe = index - 1
    while probe >= 0:
        prev_start, prev_end = panels[probe]
        prev_h = prev_end - prev_start
        gap = cluster_start - prev_end
        if gap > gap_limit or prev_h > short_limit:
            break
        cluster_start = prev_start
        probe -= 1

    return (cluster_start, cluster_end)


def build_skip_targets(
    panels: list[tuple[int, int]],
    total_h: int,
    view_h: int,
    max_scroll: int,
) -> list[int]:
    if not panels:
        return []
    if total_h <= 0 or view_h <= 0:
        return []

    targets = []
    short_panel_max = int(view_h * 0.95)
    tall_step = int(view_h * 0.78)
    first_pad = int(view_h * 0.16)
    min_target_gap = max(90, int(view_h * 0.18))
    lookahead_window = int(view_h * 1.10)
    content_bias = max(24, int(view_h * 0.05))
    cluster_bottom_fraction = 0.72

    prev_end = 0
    for index, (start, end) in enumerate(panels):
        panel_h = end - start
        leading_gap = max(0, start - prev_end)
        entry_pad = max(0, first_pad - leading_gap)

        if panel_h <= short_panel_max:
            base_target = max(0, min(start - entry_pad, max_scroll))
            best_target = base_target
            anchor = (start, end)
            best_score = score_panel_window(panels, base_target, view_h, anchor=anchor)
            cluster_end = end
            min_anchor_visible = max(80, min(panel_h, int(view_h * 0.28)))

            for look_index in range(index, len(panels)):
                look_start, look_end = panels[look_index]
                if look_start - start > lookahead_window:
                    break
                cluster_end = max(cluster_end, look_end)
                cluster_mid_target = max(0, min(int((start + cluster_end - view_h) / 2), max_scroll))

                candidates = {
                    max(0, min(look_start - min(entry_pad, first_pad // 2), max_scroll)),
                    max(0, min(look_end - int(view_h * cluster_bottom_fraction), max_scroll)),
                    cluster_mid_target,
                }

                for candidate in candidates:
                    if visible_overlap(start, end, candidate, view_h) < min_anchor_visible:
                        continue
                    coverage = score_panel_window(panels, candidate, view_h, anchor=anchor)
                    forward_bias = max(0, candidate - base_target)
                    score = coverage + min(content_bias, forward_bias)
                    if score > best_score or (score == best_score and candidate > best_target):
                        best_target = candidate
                        best_score = score

            targets.append(best_target)
            prev_end = end
            continue

        anchor = (start, end)
        first_target = max(0, start - min(entry_pad, int(view_h * 0.08)))
        last_target = max(first_target, end - int(view_h * 0.80))
        probe_span = max(24, int(view_h * 0.12))

        target = first_target
        while target < last_target - 8:
            probe_candidates = {
                max(0, min(target, max_scroll)),
                max(0, min(target + probe_span, max_scroll)),
                max(0, min(target - probe_span, max_scroll)),
            }

            best_target = None
            best_score = None
            for candidate in probe_candidates:
                score = score_panel_window(panels, candidate, view_h, anchor=anchor)
                if best_score is None or score > best_score or (score == best_score and candidate > best_target):
                    best_target = candidate
                    best_score = score

            targets.append(max(0, best_target if best_target is not None else target))
            target += tall_step

        end_candidates = {
            max(0, min(last_target, max_scroll)),
            max(0, min(last_target - probe_span, max_scroll)),
        }
        best_end_target = None
        best_end_score = None
        for candidate in end_candidates:
            score = score_panel_window(panels, candidate, view_h, anchor=anchor)
            if best_end_score is None or score > best_end_score or (score == best_end_score and candidate > best_end_target):
                best_end_target = candidate
                best_end_score = score

        targets.append(max(0, best_end_target if best_end_target is not None else last_target))
        prev_end = end

    targets = [max(0, min(target, max_scroll)) for target in sorted(set(targets))]
    return merge_close_targets(targets, min_target_gap)
