import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from skp2dwg.dxf_builder import make_layer_plans, sanitize_layer_name
from skp2dwg.matcher import EdgeIndex, ProjectedEdge, match_line
from skp2dwg.pipeline import _is_2d_dxf


class TestLayerNames(unittest.TestCase):
    def test_invalid_chars(self):
        self.assertEqual(
            sanitize_layer_name('Muro/Corte "A" <x>'),
            "Muro_Corte _A_ _x_",
        )

    def test_empty_and_spaces(self):
        self.assertEqual(sanitize_layer_name("   "), "SIN_NOMBRE")
        self.assertEqual(sanitize_layer_name("  muro   norte  "), "muro norte")

    def test_long_name_truncated(self):
        self.assertEqual(len(sanitize_layer_name("x" * 400)), 255)


class TestLayerPlans(unittest.TestCase):
    def test_rename_color_and_hidden(self):
        layers = [{"name": "muros", "visible": False, "rgb": [10, 20, 30]}]
        plans, warnings = make_layer_plans(
            layers, {"layers": {"muros": {"name": "ARQ_MUROS"}}}
        )
        self.assertFalse(warnings)
        plan = plans["muros"]
        self.assertEqual(plan.name, "ARQ_MUROS")
        self.assertEqual(plan.rgb, (10, 20, 30))
        self.assertTrue(plan.hidden)

    def test_duplicate_names_get_suffix(self):
        layers = [
            {"name": "A/B", "visible": True},
            {"name": "A:B", "visible": True},
        ]
        plans, warnings = make_layer_plans(layers)
        names = [p.name for p in plans.values()]
        self.assertEqual(len(set(n.casefold() for n in names)), 2)
        self.assertTrue(warnings)


class TestMatcher(unittest.TestCase):
    def test_exact_match(self):
        index = EdgeIndex([ProjectedEdge(0, 0, 100, 0, "A", 10, 100)], tol=0.05)
        result = match_line(0, 0, 100, 0, index)
        self.assertEqual(result.tag, "A")
        self.assertFalse(result.ambiguous)

    def test_no_match(self):
        index = EdgeIndex([ProjectedEdge(0, 0, 100, 0, "A", 10, 100)], tol=0.05)
        result = match_line(0, 20, 100, 20, index)
        self.assertIsNone(result.tag)

    def test_closest_edge_wins(self):
        index = EdgeIndex(
            [
                ProjectedEdge(0, 0, 100, 0, "FAR", 10, 100),
                ProjectedEdge(0, 0, 100, 0, "NEAR", 2, 100),
            ],
            tol=0.05,
        )
        result = match_line(0, 0, 100, 0, index)
        self.assertEqual(result.tag, "NEAR")

    def test_partial_segment(self):
        index = EdgeIndex([ProjectedEdge(0, 0, 100, 0, "A", 1, 100)], tol=0.05)
        result = match_line(20, 0, 60, 0, index)
        self.assertEqual(result.tag, "A")


class TestDxfMode(unittest.TestCase):
    def _save(self, z=0.0, insert=False) -> Path:
        import tempfile

        import ezdxf

        doc = ezdxf.new("R2013")
        msp = doc.modelspace()
        if insert:
            doc.blocks.new("B")
            msp.add_blockref("B", (0, 0))
        else:
            msp.add_line((0, 0, z), (10, 0, z))
        path = Path(tempfile.mkdtemp()) / "mode.dxf"
        doc.saveas(path)
        return path

    def test_flat_is_2d(self):
        ok, reason = _is_2d_dxf(self._save(0.0))
        self.assertTrue(ok, reason)

    def test_z_is_3d(self):
        ok, reason = _is_2d_dxf(self._save(5.0))
        self.assertFalse(ok)
        self.assertIn("3D", reason)

    def test_insert_is_3d(self):
        ok, reason = _is_2d_dxf(self._save(insert=True))
        self.assertFalse(ok)
        self.assertIn("INSERT", reason)


if __name__ == "__main__":
    unittest.main()
