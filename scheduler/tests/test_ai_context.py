from datetime import date

from django.test import SimpleTestCase

from scheduler.ai_context import build_ai_context
from .fixtures import make_activity

DD = date(2026, 6, 1)
PROJECT_META = {'id': 'proj-1', 'name': 'Test Project', 'projectNumber': 'P-100', 'client': 'Test Client'}
VERSION_META = {'id': 'ver-1', 'versionLabel': '2026-06-01 Update'}


class AiContextBasicTests(SimpleTestCase):
    def test_context_references_correct_project_and_version(self):
        acts = [make_activity('A1')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['project']['name'], 'Test Project')
        self.assertEqual(ctx['currentVersion']['id'], 'ver-1')
        self.assertEqual(ctx['dataDate'], '2026-06-01')

    def test_no_comparison_without_previous_activities(self):
        acts = [make_activity('A1')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertFalse(ctx['hasComparison'])
        self.assertIsNone(ctx['comparisonSummary'])
        self.assertIsNone(ctx['comparisonNarrative'])
        self.assertEqual(ctx['comparisonInsights'], [])

    def test_comparison_populated_when_previous_supplied(self):
        previous = [make_activity('A1', b_finish='2026-06-01')]
        current = [make_activity('A1', b_finish='2026-06-20')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD, previous_activities=previous)
        self.assertTrue(ctx['hasComparison'])
        self.assertIsNotNone(ctx['comparisonNarrative'])
        self.assertGreater(len(ctx['topFinishSlips']), 0)
        self.assertEqual(ctx['topFinishSlips'][0]['activityId'], 'A1')

    def test_top_risk_activities_reference_real_activity_ids(self):
        acts = [make_activity('A1', total_float=-10.0), make_activity('A2', total_float=15.0)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        ids = {a['activityId'] for a in ctx['topRiskActivities']}
        self.assertTrue(ids.issubset({'A1', 'A2'}))
        self.assertIn('A1', ids)   # the negative-float activity should show up

    def test_milestones_included(self):
        acts = [make_activity('M1', is_milestone=True, dur=0, total_float=-1.0)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['milestoneCount'], 1)
        self.assertEqual(len(ctx['criticalMilestones']), 1)

    def test_no_document_text_when_none_supplied(self):
        acts = [make_activity('A1')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['narrativeDocuments'], [])

    def test_document_excerpt_is_truncated(self):
        acts = [make_activity('A1')]
        long_text = 'x' * 5000
        docs = [{'id': 'doc-1', 'filename': 'report.pdf', 'documentType': 'SCHEDULE_NARRATIVE', 'extractedText': long_text}]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD, documents=docs)
        self.assertEqual(len(ctx['narrativeDocuments'][0]['excerpt']), 2000)


class AiContextFocusTests(SimpleTestCase):
    def test_focus_narrows_scoped_activity_count(self):
        acts = [
            make_activity('A1', area='Area C'),
            make_activity('A2', area='Area A'),
        ]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD, focus={'area': 'Area C'})
        self.assertEqual(ctx['scopedActivityCount'], 1)
        self.assertEqual(ctx['focus'], {'area': 'Area C'})

    def test_focus_on_missing_field_does_not_crash(self):
        acts = [make_activity('A1')]   # no 'area' field at all
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD, focus={'area': 'Area C'})
        self.assertEqual(ctx['scopedActivityCount'], 0)

    def test_no_focus_uses_full_activity_list(self):
        acts = [make_activity('A1'), make_activity('A2')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['scopedActivityCount'], 2)
        self.assertIsNone(ctx['focus'])


class AiContextMasterActivityGroundingTests(SimpleTestCase):
    """Master Schedule Analysis, Float, Progress & Milestone Intelligence
    phase — AI Context must ground Total Float/criticality/driving/logic
    facts in the SAME completion-aware master rows the UI uses, never a
    stale or re-derived value, and must never fabricate a section (Update
    Intelligence, Risk Register, Look Ahead) that genuinely isn't
    available for the given inputs."""

    def test_completed_activity_float_not_treated_as_critical(self):
        acts = [make_activity('A1', total_float=0.0, pct_complete=100.0, finish='2026-05-20')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['masterActivitySummary']['completedActivitiesExcludedFromCurrentFloat'], 1)
        self.assertEqual(ctx['masterActivitySummary']['criticalActionableCount'], 0)

    def test_newly_negative_float_grounded_and_bounded(self):
        previous = [make_activity('A1', total_float=2.0), make_activity('A2', total_float=3.0)]
        current = [make_activity('A1', total_float=-1.0), make_activity('A2', total_float=-2.0)]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD,
            previous_version_meta={'dataDate': '2026-05-01'}, previous_activities=previous,
            previous_data_date=date(2026, 5, 1),
        )
        ids = {r['activityId'] for r in ctx['topNewlyNegativeFloat']}
        self.assertEqual(ids, {'A1', 'A2'})

    def test_update_intelligence_and_risk_sections_absent_without_previous_data_date(self):
        # previous_activities supplied but no previous_data_date -- these
        # sections must stay absent (not fabricated), like every other
        # optional section in this module.
        previous = [make_activity('A1', total_float=5.0)]
        current = [make_activity('A1', total_float=-3.0)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD, previous_activities=previous)
        self.assertIsNone(ctx['updateIntelligenceSummary'])
        self.assertEqual(ctx['scheduleRisks'], [])
        self.assertFalse(ctx['hasUpdateIntelligence'])

    def test_update_intelligence_summary_populated_with_previous_data_date(self):
        previous = [make_activity('A1', total_float=5.0, b_finish='2026-05-01')]
        current = [make_activity('A1', total_float=-3.0, b_finish='2026-05-01')]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous,
            previous_data_date=date(2026, 5, 1),
        )
        self.assertTrue(ctx['hasUpdateIntelligence'])
        self.assertIn('narrative', ctx['updateIntelligenceSummary'])
        self.assertIn('movementCounts', ctx['updateIntelligenceSummary'])

    def test_schedule_risk_float_grounded_against_completion_not_raw(self):
        # A1 goes negative AND completes in the same update. Whether or
        # not risk_register's own logic flags it is that engine's own
        # concern (untouched here) -- but IF it appears in scheduleRisks,
        # AI Context must show its float as unavailable/complete, never
        # the raw negative value that would misread as currently critical.
        previous = [make_activity('A1', total_float=5.0, pct_complete=40.0, start='2026-04-01')]
        current = [make_activity(
            'A1', total_float=-3.0, pct_complete=100.0, start='2026-04-01', finish='2026-05-15',
        )]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous,
            previous_data_date=date(2026, 5, 1),
        )
        risk = next((r for r in ctx['scheduleRisks'] if r['activityId'] == 'A1'), None)
        if risk is not None:
            self.assertIsNone(risk['currentTotalFloat'])
            self.assertTrue(risk['activityComplete'])

    def test_legacy_float_deterioration_excludes_completed_activities(self):
        previous = [make_activity('A1', total_float=10.0), make_activity('A2', total_float=10.0)]
        current = [
            make_activity('A1', total_float=-4.0, pct_complete=100.0, finish='2026-05-20'),  # complete
            make_activity('A2', total_float=-4.0, pct_complete=40.0, start='2026-05-01'),    # incomplete
        ]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD, previous_activities=previous)
        ids = {r['activityId'] for r in ctx['topFloatDeterioration']}
        self.assertNotIn('A1', ids)
        self.assertIn('A2', ids)

    def test_lookahead_and_recovery_scenarios_present(self):
        acts = [make_activity('A1', b_start='2026-06-02', b_finish='2026-06-10')]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, acts, DD,
            recovery_scenarios=[{'name': 'Compress Foundations', 'status': 'DRAFT', 'recoveryDays': 5}],
        )
        self.assertIn('window', ctx['lookAhead'])
        self.assertEqual(ctx['activeRecoveryScenarios'][0]['name'], 'Compress Foundations')

    def test_no_recovery_scenarios_is_empty_list_not_fabricated(self):
        acts = [make_activity('A1')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['activeRecoveryScenarios'], [])

    def test_driving_activities_bounded_and_traceable(self):
        acts = [
            make_activity('A1', total_float=0.0, onLongestPath=True),
            make_activity('A2', total_float=5.0),
        ]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        ids = {r['activityId'] for r in ctx['topDrivingActivities']}
        self.assertEqual(ids, {'A1'})


class ClassifyFocusTests(SimpleTestCase):
    """Focus detection is token-based and label-anchored for short values —
    never substring matching (Area 'C' must not fire on the letter c)."""

    def setUp(self):
        self.acts = [
            make_activity('A1', area='B'), make_activity('A2', area='C'), make_activity('A3', area='D'),
            make_activity('A4', area='E'), make_activity('A5', area='BE'), make_activity('A6', area='OFCI C'),
            make_activity('A7', discipline='E', contractor='Related'),
            make_activity('A8', contractor='Keller'),
        ]

    def focus(self, q):
        from scheduler.ai_context import classify_focus
        return classify_focus(q, self.acts)

    def test_explicit_area_forms_match(self):
        for q in ('What is happening in Area C?', 'what is happening in area c', 'WHAT IS HAPPENING IN AREA C?', 'Area C status'):
            self.assertEqual(self.focus(q), {'area': 'C'}, q)

    def test_other_areas_do_not_cross_match(self):
        self.assertEqual(self.focus('Show me Area B activities'), {'area': 'B'})
        self.assertEqual(self.focus('What is driving Area D?'), {'area': 'D'})
        self.assertEqual(self.focus('Any float problems in area e?'), {'area': 'E'})
        self.assertEqual(self.focus('area be status'), {'area': 'BE'})       # not B, not E

    def test_letter_c_inside_words_never_activates_area_c(self):
        for q in (
            'What changed since the previous update?', 'Which activities are currently critical?',
            'Which activities are critical?', 'What is current?', 'Show me changed activities',
            'commissioning status', 'What are the top schedule risks?', 'Why is this project delayed?',
            'Which activities have newly negative float?', 'What recovery scenarios are available?',
        ):
            self.assertIsNone(self.focus(q), q)

    def test_bare_standalone_letter_is_deliberately_not_an_area(self):
        # A lone "C"/"E" cannot be told apart from an ordinary letter, so it is not supported.
        self.assertIsNone(self.focus("What's happening in C?"))
        self.assertIsNone(self.focus('Plan B or plan E?'))

    def test_multi_token_value_matches_as_a_run(self):
        self.assertEqual(self.focus('status of OFCI C'), {'area': 'OFCI C'})

    def test_ambiguous_field_is_skipped_not_guessed(self):
        self.assertIsNone(self.focus('Compare Area B and Area C'))

    def test_short_discipline_needs_its_label(self):
        self.assertEqual(self.focus('discipline E problems'), {'discipline': 'E'})
        self.assertIsNone(self.focus('E'))

    def test_contractor_word_that_is_common_english_needs_label(self):
        self.assertIsNone(self.focus('Which related activities slipped?'))
        self.assertEqual(self.focus('contractor Related progress'), {'contractor': 'Related'})
        self.assertEqual(self.focus('What is Keller working on?'), {'contractor': 'Keller'})   # distinctive name: bare token ok

    def test_never_invents_a_value_absent_from_the_schedule(self):
        self.assertIsNone(self.focus('What is happening in Area Z?'))


class AiContextStaysBoundedTests(SimpleTestCase):
    """The payload sent to the model must not grow with schedule size — a
    ~2,000-activity update once produced a 366k-token context because the
    raw per-field change list was embedded."""

    def test_context_size_is_bounded_for_a_large_fully_changed_schedule(self):
        import json
        n = 2500
        previous = [make_activity(f'A{i}', b_finish='2026-06-01', total_float=5.0) for i in range(n)]
        current = [make_activity(f'A{i}', b_finish='2026-07-15', total_float=-4.0) for i in range(n)]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous,
            previous_data_date=date(2026, 5, 1), previous_version_meta={'dataDate': '2026-05-01'}, top_n=8,
        )
        self.assertNotIn('changes', ctx['comparisonSummary'])
        self.assertLessEqual(len(ctx['comparisonSummary']['added']), 8)
        # Aggregate counts are still present and complete.
        self.assertEqual(ctx['comparisonSummary']['changedActivityCount'], n)
        self.assertLess(len(json.dumps(ctx, default=str)), 150_000)

    def test_bounded_summary_keeps_counts_and_flags_truncated_lists(self):
        previous = [make_activity('A1')]
        current = [make_activity('A1')] + [make_activity(f'N{i}') for i in range(20)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD, previous_activities=previous, top_n=5)
        cs = ctx['comparisonSummary']
        self.assertEqual(cs['addedCount'], 20)
        self.assertEqual(len(cs['added']), 5)
        self.assertTrue(cs['addedListTruncated'])


class AiContextListSemanticsTests(SimpleTestCase):
    def test_should_have_started_and_finished_are_defined_separately_with_counts(self):
        acts = [
            make_activity('S1', b_start='2026-05-01', b_finish='2026-05-20'),                                   # not started, baseline start passed
            make_activity('F1', b_start='2026-05-01', b_finish='2026-05-20', start='2026-05-02', pct_complete=50.0),  # started, unfinished, baseline finish passed
        ]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual([r['activityId'] for r in ctx['topShouldHaveStarted']], ['S1'])
        self.assertEqual([r['activityId'] for r in ctx['topShouldHaveFinished']].count('F1'), 1)
        self.assertNotIn('F1', [r['activityId'] for r in ctx['topShouldHaveStarted']])
        d = ctx['listDefinitions']
        self.assertIn('BASELINE START', d['topShouldHaveStarted'])
        self.assertIn('BASELINE FINISH', d['topShouldHaveFinished'])
        self.assertIn('Total: 1', d['topShouldHaveStarted'])

    def test_empty_should_have_started_states_none_explicitly(self):
        acts = [make_activity('A1', b_start='2026-07-01', b_finish='2026-07-10')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        self.assertEqual(ctx['topShouldHaveStarted'], [])
        self.assertIn('Total: 0', ctx['listDefinitions']['topShouldHaveStarted'])
        self.assertIn('none', ctx['listDefinitions']['topShouldHaveStarted'])

    def test_lookahead_status_withheld_without_a_baseline(self):
        acts = [make_activity('A1', b_start='2026-06-02', b_finish='2026-06-10', earlyStart='2026-06-02', earlyFinish='2026-06-10')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        la = ctx['lookAhead']
        self.assertIsNone(la['statusCounts'])
        self.assertIn('No baseline', la['statusUnavailableReason'])
        self.assertTrue(all(a['status'] is None for a in la['activities']))

    def test_lookahead_status_present_with_a_baseline(self):
        acts = [make_activity('A1', b_start='2026-06-02', b_finish='2026-06-10', earlyStart='2026-06-02', earlyFinish='2026-06-10')]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD, baseline_activities=acts)
        self.assertIsNotNone(ctx['lookAhead']['statusCounts'])
        self.assertIsNone(ctx['lookAhead']['statusUnavailableReason'])


class AiContextMilestoneGroundingTests(SimpleTestCase):
    def _ms(self, code, due, tf, **kw):
        return make_activity(code, is_milestone=True, dur=0, b_finish=due, total_float=tf, earlyFinish=due, **kw)

    def test_complete_milestone_never_presented_as_critical_or_zero_float(self):
        acts = [self._ms('M-DONE', '2026-05-01', 0.0, pct_complete=100.0, finish='2026-05-01'),
                self._ms('M-NEXT', '2026-07-01', -3.0)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        crit_ids = [m['activityId'] for m in ctx['criticalMilestones']]
        self.assertNotIn('M-DONE', crit_ids)

    def test_upcoming_milestones_are_future_incomplete_sorted_and_carry_driving_predecessor(self):
        pred = make_activity('P1', total_float=-3.0, successors=[])
        pred['successors'] = [{'actId': 'M-B', 'relType': 'FS', 'lagDays': 0}]
        acts = [
            pred,
            self._ms('M-DONE', '2026-05-01', 0.0, pct_complete=100.0, finish='2026-05-01'),
            self._ms('M-B', '2026-08-01', -3.0, predecessors=[{'actId': 'P1', 'relType': 'FS', 'lagDays': 0}]),
            self._ms('M-A', '2026-07-01', 2.0),
        ]
        ctx = build_ai_context(PROJECT_META, VERSION_META, acts, DD)
        ids = [m['activityId'] for m in ctx['upcomingMilestones']]
        self.assertEqual(ids, ['M-A', 'M-B'])                                  # complete excluded, earliest first
        self.assertIn('upcomingMilestones', ctx['listDefinitions'])

    def test_next_milestone_carries_its_driving_chain(self):
        p1 = make_activity('P1', total_float=-3.0)
        p1['successors'] = [{'actId': 'M-A', 'relType': 'FS', 'lagDays': 0}]
        m = self._ms('M-A', '2026-07-01', -3.0, predecessors=[{'actId': 'P1', 'relType': 'FS', 'lagDays': 0}])
        ctx = build_ai_context(PROJECT_META, VERSION_META, [p1, m], DD)
        nm = ctx['nextMilestone']
        self.assertEqual(nm['activityId'], 'M-A')
        self.assertEqual([s['activityId'] for s in nm['drivingChainUpstreamFarthestFirst']], ['P1'])

    def test_no_next_milestone_is_none_not_invented(self):
        ctx = build_ai_context(PROJECT_META, VERSION_META, [make_activity('A1')], DD)
        self.assertIsNone(ctx['nextMilestone'])

    def test_milestone_summary_gives_exact_totals_beyond_the_capped_list(self):
        current = [self._ms(f'M{i}', '2026-09-01', 0.0) for i in range(12)]
        baseline = [self._ms(f'M{i}', '2026-06-01', 0.0) for i in range(12)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD, baseline_activities=baseline, top_n=5)
        self.assertEqual(len(ctx['slippedMilestones']), 5)                       # capped sample
        self.assertEqual(ctx['milestoneSummary']['slippedVsBaselineCount'], 12)  # exact total
        self.assertEqual(ctx['milestoneSummary']['milestoneCount'], 12)
        self.assertIn('milestoneSummary', ctx['listDefinitions']['slippedMilestones'])

    def test_milestone_variance_is_unavailable_not_invented_without_a_baseline(self):
        current = [self._ms(f'M{i}', '2026-09-01', 0.0) for i in range(3)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD)
        self.assertEqual(ctx['milestoneSummary']['slippedVsBaselineCount'], 0)
        self.assertEqual(ctx['slippedMilestones'], [])


class AiContextRiskResourcingTests(SimpleTestCase):
    """Dashboard Consolidation Phase 1 — AI context's top-risk sections are
    re-sourced from risk_register.py (the SAME engine the Risk Register/
    Risk Heat Map tabs use) whenever a previous version is available, so
    AI Chat can never name a "top risk" the Risk & Milestones workspace
    itself wouldn't also show. riskOverall (a continuous gauge) is a
    different, legitimate concept and is intentionally left alone."""

    def _ms(self, code, due, tf, **kw):
        return make_activity(code, is_milestone=True, dur=0, b_finish=due, total_float=tf, earlyFinish=due, **kw)

    def test_top_risk_activities_matches_schedule_risks_when_register_available(self):
        previous = [make_activity('A1', total_float=5.0, area='Area A')]
        current = [make_activity('A1', total_float=-3.0, earlyFinish='2026-06-20', area='Area A', is_critical=True, onLongestPath=True)]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous,
            previous_data_date=date(2026, 5, 1), previous_version_meta={'dataDate': '2026-05-01'},
        )
        self.assertTrue(ctx['hasUpdateIntelligence'])
        self.assertEqual(ctx['topRiskActivities'], ctx['scheduleRisks'])
        if ctx['topRiskActivities']:
            self.assertIn('severity', ctx['topRiskActivities'][0])
            self.assertNotIn('score', ctx['topRiskActivities'][0])   # not the old schedule_risk.py shape

    def test_top_risk_areas_grounded_in_risk_register_discrete_severity(self):
        previous = [make_activity('A1', total_float=5.0, area='Area A')]
        current = [make_activity('A1', total_float=-14.0, earlyFinish='2026-06-25', area='Area A', is_critical=True, onLongestPath=True)]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous,
            previous_data_date=date(2026, 5, 1),
        )
        areas = ctx['topRiskAreas']
        self.assertTrue(areas)
        cell = areas[0]
        self.assertIn('riskCount', cell)
        self.assertIn('criticalCount', cell)
        self.assertNotIn('score', cell)          # discrete counts, not the old continuous score
        self.assertNotIn('totalFloatSum', cell)  # never summed

    def test_risk_register_summary_present_only_when_register_available(self):
        previous = [make_activity('A1', total_float=5.0)]
        current = [make_activity('A1', total_float=-3.0, earlyFinish='2026-06-20')]
        with_prev = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous, previous_data_date=date(2026, 5, 1))
        self.assertIsNotNone(with_prev['riskRegisterSummary'])
        self.assertIn('totalRisks', with_prev['riskRegisterSummary'])

        without_prev = build_ai_context(PROJECT_META, VERSION_META, current, DD)
        self.assertIsNone(without_prev['riskRegisterSummary'])

    def test_falls_back_to_schedule_risk_when_no_previous_version(self):
        current = [make_activity('A1', total_float=-10.0)]
        ctx = build_ai_context(PROJECT_META, VERSION_META, current, DD)
        self.assertFalse(ctx['hasUpdateIntelligence'])
        # Falls back cleanly — still populated, still real data, just from
        # the standalone single-version scorer (score/level shape).
        self.assertIsInstance(ctx['topRiskActivities'], list)
        if ctx['topRiskActivities']:
            self.assertIn('score', ctx['topRiskActivities'][0])

    def test_risk_overall_gauge_is_never_replaced_by_the_register(self):
        previous = [make_activity('A1', total_float=5.0)]
        current = [make_activity('A1', total_float=-3.0, earlyFinish='2026-06-20')]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous, previous_data_date=date(2026, 5, 1))
        self.assertIn('score', ctx['riskOverall'])
        self.assertIn('level', ctx['riskOverall'])

    def test_completed_activity_never_inflates_risk_register_top_risks(self):
        # A1 has a negative imported float but is Complete — must not
        # appear as an actionable top risk via either the register-based or
        # fallback path (activity_analysis.py's completion rule + Step 0's
        # canonical is_activity_complete both apply here).
        previous = [make_activity('A1', total_float=5.0, pct_complete=40.0, start='2026-04-01')]
        current = [make_activity('A1', total_float=-3.0, pct_complete=100.0, start='2026-04-01', finish='2026-05-15')]
        ctx = build_ai_context(
            PROJECT_META, VERSION_META, current, DD, previous_activities=previous, previous_data_date=date(2026, 5, 1))
        risk = next((r for r in ctx['topRiskActivities'] if r['activityId'] == 'A1'), None)
        if risk is not None:
            self.assertIsNone(risk['currentTotalFloat'])
            self.assertTrue(risk['activityComplete'])
