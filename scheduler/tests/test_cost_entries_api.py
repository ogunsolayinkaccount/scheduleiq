import json

from django.test import TestCase

from scheduler.models import CostAccount, ManualCostEntry, Project, ScheduleUpload


class CostEntriesApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='t.xer', sanitized_filename='t.xer',
            file_type='XER', activities_json=[],
        )
        self.base = f'/api/projects/{self.project.id}/cost-entries/'

    def _create(self, **overrides):
        body = {'entryType': 'APPROVED_BUDGET_CHANGE', 'cost': 1000.0, 'description': 'test'}
        body.update(overrides)
        return self.client.post(self.base, data=json.dumps(body), content_type='application/json')

    # ── Create ──────────────────────────────────────────────────────────────

    def test_create_manual_budget_change(self):
        resp = self._create(entryType='APPROVED_BUDGET_CHANGE', cost=50000.0, referenceNumber='CO-004')
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        self.assertEqual(body['entryType'], 'APPROVED_BUDGET_CHANGE')
        self.assertEqual(body['cost'], 50000.0)
        self.assertEqual(body['referenceNumber'], 'CO-004')
        self.assertEqual(ManualCostEntry.objects.count(), 1)

    def test_create_commitment(self):
        resp = self._create(entryType='COMMITMENT', cost=120000.0, description='Steel subcontract', wbsId='WBS1')
        self.assertEqual(resp.status_code, 201)
        entry = ManualCostEntry.objects.get()
        self.assertEqual(entry.entry_type, 'COMMITMENT')
        self.assertEqual(entry.wbs_id, 'WBS1')

    def test_create_approved_eac(self):
        resp = self._create(entryType='APPROVED_EAC', cost=12_400_000.0, description='PM-approved forecast Aug 2026')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(ManualCostEntry.objects.get().entry_type, 'APPROVED_EAC')

    def test_create_hours_only_entry(self):
        resp = self._create(entryType='ORIGINAL_BUDGET_OVERRIDE', cost=None, hours=5000.0)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['hours'], 5000.0)

    def test_create_with_schedule_version(self):
        resp = self._create(entryType='APPROVED_EAC', cost=1_000_000.0, scheduleVersionId=str(self.version.id))
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['scheduleUploadId'], str(self.version.id))

    def test_create_with_cost_account(self):
        acct = CostAccount.objects.create(schedule_upload=self.version, account_code='CA-100', discipline='Electrical')
        resp = self._create(entryType='COMMITMENT', cost=5000.0, costAccountId=str(acct.id))
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['costAccountId'], str(acct.id))

    # ── Validation ──────────────────────────────────────────────────────────

    def test_invalid_entry_type_rejected(self):
        resp = self._create(entryType='NOT_A_REAL_TYPE')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('Invalid entryType', resp.json()['error'])

    def test_missing_entry_type_rejected(self):
        resp = self.client.post(self.base, data=json.dumps({'cost': 100}), content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_negative_cost_rejected(self):
        resp = self._create(cost=-500.0)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('negative', resp.json()['error'])

    def test_negative_hours_rejected(self):
        resp = self._create(cost=None, hours=-10.0)
        self.assertEqual(resp.status_code, 400)

    def test_non_numeric_cost_rejected(self):
        resp = self._create(cost='not-a-number')
        self.assertEqual(resp.status_code, 400)

    def test_neither_cost_nor_hours_rejected(self):
        resp = self._create(cost=None, hours=None)
        self.assertEqual(resp.status_code, 400)
        self.assertIn('At least one', resp.json()['error'])

    def test_invalid_json_returns_400(self):
        resp = self.client.post(self.base, data='not json', content_type='application/json')
        self.assertEqual(resp.status_code, 400)

    def test_schedule_version_from_other_project_rejected(self):
        other = Project.objects.create(name='Other')
        other_version = ScheduleUpload.objects.create(
            project=other, original_filename='o.xer', sanitized_filename='o.xer',
            file_type='XER', activities_json=[],
        )
        resp = self._create(scheduleVersionId=str(other_version.id))
        self.assertEqual(resp.status_code, 404)

    # ── List / filter ───────────────────────────────────────────────────────

    def test_list_entries(self):
        self._create(entryType='COMMITMENT', cost=1.0)
        self._create(entryType='APPROVED_EAC', cost=2.0)
        resp = self.client.get(self.base)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()['entries']), 2)

    def test_list_filtered_by_entry_type(self):
        self._create(entryType='COMMITMENT', cost=1.0)
        self._create(entryType='APPROVED_EAC', cost=2.0)
        resp = self.client.get(self.base, {'entryType': 'COMMITMENT'})
        entries = resp.json()['entries']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['entryType'], 'COMMITMENT')

    def test_entries_scoped_to_project(self):
        other = Project.objects.create(name='Other')
        ManualCostEntry.objects.create(project=other, entry_type='COMMITMENT', cost=999.0)
        self._create(entryType='COMMITMENT', cost=1.0)
        resp = self.client.get(self.base)
        self.assertEqual(len(resp.json()['entries']), 1)

    # ── Update ──────────────────────────────────────────────────────────────

    def test_update_manual_budget_change(self):
        create_resp = self._create(entryType='APPROVED_BUDGET_CHANGE', cost=50000.0)
        entry_id = create_resp.json()['id']
        patch_resp = self.client.patch(
            f'{self.base}{entry_id}/', data=json.dumps({'cost': 75000.0}), content_type='application/json'
        )
        self.assertEqual(patch_resp.status_code, 200)
        self.assertEqual(patch_resp.json()['cost'], 75000.0)
        self.assertEqual(ManualCostEntry.objects.get(pk=entry_id).cost, 75000.0)

    def test_partial_update_leaves_other_fields_unchanged(self):
        create_resp = self._create(entryType='COMMITMENT', cost=1000.0, description='original')
        entry_id = create_resp.json()['id']
        self.client.patch(f'{self.base}{entry_id}/', data=json.dumps({'cost': 2000.0}), content_type='application/json')
        entry = ManualCostEntry.objects.get(pk=entry_id)
        self.assertEqual(entry.cost, 2000.0)
        self.assertEqual(entry.description, 'original')

    def test_update_rejects_invalid_entry_type(self):
        create_resp = self._create()
        entry_id = create_resp.json()['id']
        resp = self.client.patch(
            f'{self.base}{entry_id}/', data=json.dumps({'entryType': 'GARBAGE'}), content_type='application/json'
        )
        self.assertEqual(resp.status_code, 400)

    def test_update_nonexistent_entry_returns_404(self):
        resp = self.client.patch(
            f'{self.base}00000000-0000-0000-0000-000000000000/',
            data=json.dumps({'cost': 1.0}), content_type='application/json',
        )
        self.assertEqual(resp.status_code, 404)

    # ── Delete ──────────────────────────────────────────────────────────────

    def test_delete_manual_budget_change(self):
        create_resp = self._create()
        entry_id = create_resp.json()['id']
        resp = self.client.delete(f'{self.base}{entry_id}/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(ManualCostEntry.objects.count(), 0)

    def test_delete_nonexistent_entry_returns_404(self):
        resp = self.client.delete(f'{self.base}00000000-0000-0000-0000-000000000000/')
        self.assertEqual(resp.status_code, 404)

    def test_delete_scoped_to_project(self):
        other = Project.objects.create(name='Other')
        entry = ManualCostEntry.objects.create(project=other, entry_type='COMMITMENT', cost=1.0)
        resp = self.client.delete(f'{self.base}{entry.id}/')
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(ManualCostEntry.objects.count(), 1)   # untouched


class CostSummaryManualEntryIntegrationTests(TestCase):
    """Verifies manual entries flow through into /cost-summary/ correctly —
    Current Budget logic and approved-vs-calculated forecast separation."""

    def setUp(self):
        self.project = Project.objects.create(name='Test Project')
        self.version = ScheduleUpload.objects.create(
            project=self.project, original_filename='t.xer', sanitized_filename='t.xer',
            file_type='XER', activities_json=[
                {
                    'id': 'A1', 'code': 'A1', 'dur': 10.0, 'remainDur': 5.0, 'pctComplete': 50.0,
                    'bStart': '2026-01-01', 'bFinish': '2026-01-11',
                    'isCostLoaded': True, 'budgetedCost': 1_000_000.0,
                    'actualCost': 400_000.0, 'remainingCost': 600_000.0,
                },
            ],
        )
        self.base = f'/api/projects/{self.project.id}/cost-summary/'

    def test_current_budget_equals_original_plus_approved_changes(self):
        ManualCostEntry.objects.create(
            project=self.project, entry_type='APPROVED_BUDGET_CHANGE', cost=80_000.0,
        )
        resp = self.client.get(self.base, {'version': str(self.version.id)})
        body = resp.json()
        self.assertEqual(body['budget']['originalBudget']['value'], 1_000_000.0)
        self.assertEqual(body['budget']['approvedBudgetChanges']['value'], 80_000.0)
        self.assertEqual(body['budget']['currentBudget']['value'], 1_080_000.0)

    def test_no_approved_changes_current_budget_equals_original(self):
        resp = self.client.get(self.base, {'version': str(self.version.id)})
        body = resp.json()
        self.assertEqual(body['budget']['currentBudget']['value'], body['budget']['originalBudget']['value'])
        self.assertEqual(body['budget']['approvedBudgetChanges']['source'], 'unavailable')

    def test_approved_eac_surfaced_separately_from_calculated_scenarios(self):
        ManualCostEntry.objects.create(
            project=self.project, entry_type='APPROVED_EAC', cost=1_100_000.0, description='PM approved',
        )
        resp = self.client.get(self.base, {'version': str(self.version.id)})
        body = resp.json()
        self.assertEqual(body['forecast']['approvedEac']['value'], 1_100_000.0)
        self.assertEqual(body['forecast']['approvedEac']['source'], 'manual')
        # Calculated scenarios still present and distinct — approved EAC never overwrites them
        calculated_methodologies = {s['methodology'] for s in body['forecast']['scenarios']}
        self.assertIn('CPI_BASED', calculated_methodologies)
        self.assertNotIn('APPROVED_EAC', calculated_methodologies)

    def test_no_approved_eac_is_unavailable(self):
        resp = self.client.get(self.base, {'version': str(self.version.id)})
        body = resp.json()
        self.assertIsNone(body['forecast']['approvedEac']['value'])
        self.assertEqual(body['forecast']['approvedEac']['source'], 'unavailable')

    def test_commitments_reported_separately_from_actual_cost(self):
        ManualCostEntry.objects.create(
            project=self.project, entry_type='COMMITMENT', cost=200_000.0, description='Steel subcontract PO',
        )
        resp = self.client.get(self.base, {'version': str(self.version.id)})
        body = resp.json()
        self.assertEqual(body['commitments']['value'], 200_000.0)
        self.assertEqual(body['actuals']['actualCost']['value'], 400_000.0)   # unaffected by commitment


class CostHistoryApiTests(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='History Project')
        self.base = f'/api/projects/{self.project.id}/cost-history/'

    def _acts(self, budgeted, actual, pct):
        return [{
            'id': 'A1', 'code': 'A1', 'dur': 10.0, 'remainDur': 10.0 * (1 - pct / 100.0), 'pctComplete': pct,
            'bStart': '2026-01-01', 'bFinish': '2026-01-11',
            'isCostLoaded': True, 'budgetedCost': budgeted, 'actualCost': actual, 'remainingCost': budgeted - actual,
        }]

    def test_history_ordered_by_data_date(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v2.xer', sanitized_filename='v2.xer', file_type='XER',
            data_date='2026-03-01', activities_json=self._acts(1000.0, 800.0, 80.0),
        )
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date='2026-01-15', activities_json=self._acts(1000.0, 200.0, 20.0),
        )
        resp = self.client.get(self.base)
        body = resp.json()
        self.assertEqual(body['pointCount'], 2)
        dates = [p['dataDate'] for p in body['points']]
        self.assertEqual(dates, sorted(dates))   # ascending — earlier version first

    def test_version_without_data_date_excluded(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='nodate.xer', sanitized_filename='nodate.xer',
            file_type='XER', data_date=None, activities_json=self._acts(1000.0, 500.0, 50.0),
        )
        resp = self.client.get(self.base)
        self.assertEqual(resp.json()['pointCount'], 0)

    def test_no_versions_returns_empty_not_error(self):
        resp = self.client.get(self.base)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['points'], [])

    def test_non_cost_loaded_version_shows_null_not_zero(self):
        ScheduleUpload.objects.create(
            project=self.project, original_filename='v1.xer', sanitized_filename='v1.xer', file_type='XER',
            data_date='2026-01-15', activities_json=[{'id': 'A1', 'code': 'A1', 'dur': 10.0, 'remainDur': 5.0, 'pctComplete': 50.0}],
        )
        resp = self.client.get(self.base)
        point = resp.json()['points'][0]
        self.assertIsNone(point['bac'])
        self.assertIsNone(point['cpi'])
