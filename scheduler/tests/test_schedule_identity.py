"""
Schedule Identity Evaluator tests — pure engine tests for
schedule_identity.evaluate_schedule_identity(), covering every
classification and the safety cases the directive calls out by name:
same-proj_id-but-different-structure (must NOT blindly say SAME_PROJECT)
and different-proj_id-but-same-structure (must recognize LIKELY_SAME_PROJECT
— this is exactly the real AWP2025 scenario).
"""
import time

from django.test import SimpleTestCase

from scheduler import schedule_identity as si


def _acts(n, wbs='Area A', milestone_every=0, prefix='ACT'):
    out = []
    for i in range(n):
        out.append({
            'id': f'{prefix}-{i:05d}', 'code': f'{prefix}-{i:05d}', 'name': f'Activity {i}',
            'wbs': wbs, 'isMilestone': bool(milestone_every and i % milestone_every == 0),
        })
    return out


class SameProjectTests(SimpleTestCase):
    def test_identical_activities_same_p6_id_is_same_project(self):
        ref = _acts(111)
        up = _acts(111)
        result = si.evaluate_schedule_identity(ref, '4551', 'AWP2025-BL', up, '4551', 'AWP2025-BL')
        self.assertEqual(result['classification'], si.SAME_PROJECT)
        self.assertFalse(result['confirmationRequired'])
        self.assertEqual(result['signals']['activityIdOverlap']['referenceOverlapRatio'], 1.0)
        self.assertTrue(result['signals']['p6ProjectId']['match'])

    def test_normal_schedule_evolution_recognized_as_continuity(self):
        # 10% new activities, 5% removed — a realistic forward update.
        ref = _acts(200)
        up = _acts(190) + [  # 190 of the original 200 (5% removed) ...
            {'id': f'NEW-{i:04d}', 'code': f'NEW-{i:04d}', 'name': f'New {i}', 'wbs': 'Area A', 'isMilestone': False}
            for i in range(20)  # ... plus 20 new (10% growth)
        ]
        result = si.evaluate_schedule_identity(ref, '4551', 'Proj', up, '4551', 'Proj')
        self.assertIn(result['classification'], (si.SAME_PROJECT, si.LIKELY_SAME_PROJECT))
        self.assertFalse(result['confirmationRequired'])

    def test_legitimate_growth_uses_reference_ratio_not_penalized_by_new_activities(self):
        # Every reference activity still present, plus substantial new
        # scope — referenceOverlapRatio should be 100% even though
        # uploadedOverlapRatio is much lower, and that should NOT be
        # penalized (matches the item-13 "major restructure" guidance).
        ref = _acts(111)
        up = _acts(111) + _acts(150, prefix='NEWSCOPE')
        result = si.evaluate_schedule_identity(ref, '4551', 'Proj', up, '4551', 'Proj')
        self.assertEqual(result['signals']['activityIdOverlap']['referenceOverlapRatio'], 1.0)
        self.assertEqual(result['classification'], si.SAME_PROJECT)


class ChangedP6IdSameStructureTests(SimpleTestCase):
    """Item 24 / the real AWP2025 scenario — the primary case this phase exists to solve."""

    def test_different_proj_id_high_overlap_is_likely_same_project(self):
        ref = _acts(111)
        up = _acts(108) + _acts(3, prefix='NEWACT')  # 108/111 = 97.3% overlap
        result = si.evaluate_schedule_identity(ref, '4551', 'AWP2025-BL', up, '4532', 'AWP2025-BL continued')
        self.assertEqual(result['classification'], si.LIKELY_SAME_PROJECT)
        self.assertFalse(result['confirmationRequired'])
        self.assertFalse(result['signals']['p6ProjectId']['match'])
        self.assertIn('P6 Project ID changed', ' '.join(result['warnings']))
        # Raw P6 identity is never hidden even when classified as likely-same.
        self.assertEqual(result['signals']['p6ProjectId']['reference'], '4551')
        self.assertEqual(result['signals']['p6ProjectId']['uploaded'], '4532')

    def test_explanation_is_factual_not_ai_flavored(self):
        ref, up = _acts(111), _acts(108) + _acts(3, prefix='NEWACT')
        result = si.evaluate_schedule_identity(ref, '4551', 'X', up, '4532', 'X')
        text = result['explanation'].lower()
        for banned in ('ai confidence', 'model prediction', 'probably okay', 'magic'):
            self.assertNotIn(banned, text)


class SameP6IdDifferentStructureSafetyTests(SimpleTestCase):
    """Item 23 — matching proj_id must NEVER be enough by itself to claim SAME_PROJECT."""

    def test_matching_proj_id_but_unrelated_activities_is_not_same_project(self):
        ref = _acts(200, prefix='REF')
        up = _acts(200, prefix='UNRELATED')  # 0% overlap despite identical proj_id
        result = si.evaluate_schedule_identity(ref, '4551', 'Proj', up, '4551', 'Proj')
        self.assertNotEqual(result['classification'], si.SAME_PROJECT)
        self.assertEqual(result['classification'], si.UNCERTAIN)
        self.assertTrue(result['confirmationRequired'])

    def test_matching_proj_id_mismatched_name_unrelated_activities_is_likely_different(self):
        ref = _acts(200, prefix='REF')
        up = _acts(200, prefix='UNRELATED')
        result = si.evaluate_schedule_identity(ref, '4551', 'Original Project', up, '4551', 'Totally Different')
        self.assertEqual(result['classification'], si.LIKELY_DIFFERENT_PROJECT)
        self.assertTrue(result['confirmationRequired'])


class LikelyDifferentProjectTests(SimpleTestCase):
    def test_different_id_different_name_low_overlap_is_likely_different(self):
        ref = _acts(150, prefix='AWP')
        up = _acts(150, prefix='PIPT')
        result = si.evaluate_schedule_identity(ref, '4551', 'AWP2025', up, '9001', 'PIPT001')
        self.assertEqual(result['classification'], si.LIKELY_DIFFERENT_PROJECT)
        self.assertTrue(result['confirmationRequired'])


class InsufficientDataTests(SimpleTestCase):
    def test_new_project_too_few_activities_is_uncertain(self):
        ref = _acts(2)
        up = _acts(2)
        result = si.evaluate_schedule_identity(ref, None, None, up, None, None)
        self.assertEqual(result['classification'], si.UNCERTAIN)
        self.assertTrue(result['confirmationRequired'])

    def test_empty_reference_is_uncertain_not_fabricated_confidence(self):
        result = si.evaluate_schedule_identity([], None, None, _acts(50), '4551', 'X')
        self.assertEqual(result['classification'], si.UNCERTAIN)
        self.assertTrue(result['confirmationRequired'])


class WbsAndMilestoneOverlapTests(SimpleTestCase):
    def test_wbs_and_milestone_overlap_computed(self):
        ref = _acts(20, wbs='Area A', milestone_every=5)
        up = _acts(20, wbs='Area A', milestone_every=5)
        result = si.evaluate_schedule_identity(ref, '1', 'X', up, '1', 'X')
        self.assertEqual(result['signals']['wbsOverlap']['referenceOverlapRatio'], 1.0)
        self.assertEqual(result['signals']['milestoneOverlap']['overlapCount'], 4)  # indices 0,5,10,15

    def test_milestone_overlap_unavailable_when_no_milestones_either_side(self):
        ref, up = _acts(20), _acts(20)
        result = si.evaluate_schedule_identity(ref, '1', 'X', up, '1', 'X')
        self.assertFalse(result['signals']['milestoneOverlap']['available'])
        self.assertIsNone(result['signals']['milestoneOverlap']['referenceOverlapRatio'])


class ProjectNameNormalizationTests(SimpleTestCase):
    def test_case_and_punctuation_insensitive_name_match(self):
        ref, up = _acts(111), _acts(111)
        result = si.evaluate_schedule_identity(ref, '1', 'AWP2025-BL-1', up, '1', 'awp2025 bl 1')
        self.assertTrue(result['signals']['projectName']['match'])

    def test_genuinely_different_names_do_not_match(self):
        ref, up = _acts(111), _acts(111)
        result = si.evaluate_schedule_identity(ref, '1', 'AWP2025', up, '1', 'PIPT001')
        self.assertFalse(result['signals']['projectName']['match'])


class ComplexityTests(SimpleTestCase):
    def test_no_quadratic_blowup_at_20000_activities(self):
        ref = _acts(20000)
        up = _acts(19500) + _acts(500, prefix='NEWACT')
        t0 = time.perf_counter()
        result = si.evaluate_schedule_identity(ref, '1', 'X', up, '1', 'X')
        elapsed = time.perf_counter() - t0
        # A quadratic (O(n^2)) implementation at n=20000 would take many
        # seconds to minutes; a linear one completes in well under a second
        # even in a slow CI environment. 5s is a generous, non-flaky ceiling.
        self.assertLess(elapsed, 5.0)
        self.assertEqual(result['signals']['activityIdOverlap']['referenceCount'], 20000)

    def test_no_truncation_last_activity_contributes(self):
        ref = _acts(20000)
        up = _acts(20000)
        result = si.evaluate_schedule_identity(ref, '1', 'X', up, '1', 'X')
        # If only a head-sampled subset were compared, overlap would be
        # less than the full 20,000 — proves the complete population,
        # including activities near the end (ACT-19999), was considered.
        self.assertEqual(result['signals']['activityIdOverlap']['overlapCount'], 20000)


class VersionLineageCompatibilityTests(SimpleTestCase):
    """'Same underlying project' and 'safe to treat as the next chronological
    version' are separate questions — see the module's governing principle
    and the real Barn case this phase exists to solve: a scoped subset
    export sharing near-total one-directional overlap and matching P6
    identity with a much larger master schedule."""

    def test_ordinary_forward_update_is_compatible(self):
        # Sep-23 -> Sep-25 shape: 2,127 -> 2,282 activities (ratio 0.93),
        # ordinary week-to-week growth.
        ref = _acts(2127, prefix='ACT')
        up = _acts(2086, prefix='ACT') + _acts(196, prefix='NEWACT')  # 2282 total, 98% ref overlap
        result = si.evaluate_schedule_identity(ref, '6564', 'Barn', up, '6569', 'Barn')
        self.assertEqual(result['classification'], si.LIKELY_SAME_PROJECT)
        self.assertEqual(result['versionLineageCompatibility'], si.COMPATIBLE)
        self.assertFalse(result['scopeDivergence']['diverges'])
        self.assertEqual(result['projectIdentity']['classification'], si.LIKELY_SAME_PROJECT)

    def test_scoped_subset_of_a_much_larger_master_schedule_is_scope_divergent(self):
        # The real June case's shape: a 1,769-activity scoped export where
        # every uploaded... err, reference activity is found inside a much
        # larger 6,114-activity master schedule (ratio 0.29), matching P6
        # identity throughout.
        subset = _acts(1769, prefix='ACT')
        master = _acts(1769, prefix='ACT') + _acts(4345, prefix='MASTERSCOPE')
        result = si.evaluate_schedule_identity(subset, '6173', 'Barn 06-May-12', master, '6173', 'Barn 06-May-12')
        self.assertIn(result['classification'], (si.SAME_PROJECT, si.LIKELY_SAME_PROJECT))
        self.assertEqual(result['versionLineageCompatibility'], si.SCOPE_DIVERGENT)
        self.assertTrue(result['scopeDivergence']['diverges'])
        self.assertLess(result['scopeDivergence']['populationRatio'], si.SCOPE_POPULATION_RATIO_THRESHOLD)
        # Never silently decided — the classification/confirmationRequired
        # fields that already existed are untouched by this new signal.
        self.assertFalse(result['confirmationRequired'])
        self.assertIn('Scope divergence', ' '.join(result['warnings']))

    def test_version_lineage_compatibility_is_not_applicable_when_project_identity_itself_is_unresolved(self):
        ref = _acts(200, prefix='REF')
        up = _acts(200, prefix='UNRELATED')
        result = si.evaluate_schedule_identity(ref, '1', 'X', up, '2', 'Y')
        self.assertEqual(result['classification'], si.LIKELY_DIFFERENT_PROJECT)
        self.assertEqual(result['versionLineageCompatibility'], si.NOT_APPLICABLE)

    def test_scope_divergence_is_generic_not_hard_coded_to_any_activity_count(self):
        # A completely different population size pair reproduces the same
        # SCOPE_DIVERGENT signal — the threshold is a ratio, not a lookup
        # table of known project sizes.
        subset = _acts(40, prefix='ACT')
        master = _acts(40, prefix='ACT') + _acts(200, prefix='MASTERSCOPE')
        result = si.evaluate_schedule_identity(subset, '9', 'X', master, '9', 'X')
        self.assertEqual(result['versionLineageCompatibility'], si.SCOPE_DIVERGENT)

    def test_population_ratio_just_above_threshold_is_compatible(self):
        n = 1000
        ref = _acts(n, prefix='ACT')
        # 61% ratio -> just above the 0.60 threshold.
        up = _acts(n, prefix='ACT') + _acts(int(n * 0.64), prefix='NEWACT')
        result = si.evaluate_schedule_identity(ref, '1', 'X', up, '1', 'X')
        self.assertGreaterEqual(result['scopeDivergence']['populationRatio'], si.SCOPE_POPULATION_RATIO_THRESHOLD)
        self.assertEqual(result['versionLineageCompatibility'], si.COMPATIBLE)
