import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from gui.viewer.viewer_skip_logic import build_skip_targets
from gui.viewer.viewer_support import ImageLoader


def make_image(width: int, height: int, blocks: list[tuple[int, int, int, int]]) -> QImage:
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("black"))
    for x, y, w, h in blocks:
        painter.drawRect(x, y, w, h)
    painter.end()
    return image


class ViewerAutoSkipTests(unittest.TestCase):
    def test_short_panels_center_on_panel_windows(self):
        targets = build_skip_targets(
            panels=[(0, 260), (340, 600)],
            total_h=600,
            view_h=400,
            max_scroll=600,
        )
        self.assertEqual(targets, [0, 270])

    def test_tall_panels_progress_in_order_without_gap_targets(self):
        targets = build_skip_targets(
            panels=[(0, 1000)],
            total_h=1000,
            view_h=400,
            max_scroll=1000,
        )
        self.assertEqual(targets, [0, 264, 528, 600])

    def test_medium_panels_stay_centered_at_larger_zoom(self):
        targets = build_skip_targets(
            panels=[(0, 520), (620, 1140)],
            total_h=1140,
            view_h=400,
            max_scroll=1140,
        )
        self.assertEqual(targets, [60, 680])

    def test_panel_detection_keeps_short_dialogue_band_with_previous_panel(self):
        loader = ImageLoader()
        image = make_image(
            200,
            600,
            [
                (20, 40, 160, 140),
                (70, 240, 60, 24),
                (20, 320, 160, 180),
            ],
        )

        ranges = loader._compute_panel_ranges(image, min_blank=18, row_step=4)
        loader.shutdown()

        self.assertEqual(len(ranges), 2)
        first_start, first_end = ranges[0]
        second_start, second_end = ranges[1]
        self.assertLess(first_start, 0.1)
        self.assertGreater(first_end, 0.40)
        self.assertGreater(second_start, 0.50)
        self.assertGreater(second_end, 0.80)

    def test_panel_detection_preserves_large_real_gutter(self):
        loader = ImageLoader()
        image = make_image(
            200,
            600,
            [
                (20, 40, 160, 140),
                (20, 360, 160, 160),
            ],
        )

        ranges = loader._compute_panel_ranges(image, min_blank=18, row_step=4)
        loader.shutdown()

        self.assertEqual(len(ranges), 2)
        self.assertLess(ranges[0][1], 0.40)
        self.assertGreater(ranges[1][0], 0.55)


if __name__ == "__main__":
    unittest.main()
