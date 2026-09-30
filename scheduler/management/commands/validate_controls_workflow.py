"""
Multi-version project-controls validation workflow - ScheduleIQ.

Purpose (Phase K, Automated Project Controls Reporting): the calculation
engines (cost_engine.py / trend_engine.py / driver_engine.py /
executive_summary.py / report_service.py) have been validated exhaustively
by unit tests, but as of this writing no REAL project in this database has
two persisted, cost-loaded schedule versions - so the "happy path" (an
actual CPI/SPI trend line, real EAC drift, a real narrative streak
sentence) has never been exercised end-to-end against genuine P6 data.

This command is the checklist for closing that gap once a second real
update becomes available. It creates and modifies NOTHING - every step
either reads existing data or calls the exact same pure functions the API
endpoints call, and prints PASS/FAIL for each check.

Usage:
    python manage.py validate_controls_workflow <project_id>
    python manage.py validate_controls_workflow --list          (show candidate projects)

When a project doesn't yet qualify (fewer than 2 versions, or not
cost-loaded), the command explains exactly what's missing rather than
failing silently - that gap IS the actionable output.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from scheduler import driver_engine, executive_summary, report_service, trend_engine
from scheduler.models import Project


def _p(ok: bool, label: str, detail: str = ''):
    mark = 'PASS' if ok else 'FAIL'
    line = f'  [{mark}] {label}'
    if detail:
        line += f' - {detail}'
    print(line)
    return ok


class Command(BaseCommand):
    help = 'Validates the end-to-end multi-version project-controls workflow for a project, without modifying any data.'

    def add_arguments(self, parser):
        parser.add_argument('project_id', nargs='?', default=None)
        parser.add_argument('--list', action='store_true', help='List projects and their version counts instead of validating one.')

    def handle(self, *args, **options):
        if options['list'] or not options['project_id']:
            self._list_candidates()
            return

        try:
            project = Project.objects.get(pk=options['project_id'])
        except Exception:
            raise CommandError(f'Project {options["project_id"]!r} not found.')

        print(f'\nValidating project-controls workflow for: {project.name} ({project.id})\n')
        all_ok = True

        # ── Step 1/2: version presence and CURRENT/PREVIOUS resolution ────
        versions = list(project.schedule_versions.order_by('upload_timestamp'))
        all_ok &= _p(len(versions) >= 1, 'At least one schedule version imported', f'{len(versions)} version(s) found')
        dated_versions = [v for v in versions if v.data_date]
        all_ok &= _p(
            len(dated_versions) >= 2, 'At least two versions have a data date (required for trend/comparison)',
            f'{len(dated_versions)} of {len(versions)} version(s) have a data date' if len(dated_versions) < 2 else '',
        )
        if len(dated_versions) < 2:
            print('\n  -> Import a second update for this project (same Project, new ScheduleUpload with a later data_date) to proceed.\n')
            self._print_summary(all_ok)
            return

        dated_versions.sort(key=lambda v: v.data_date)
        current, previous = dated_versions[-1], dated_versions[-2]
        _p(True, 'CURRENT resolved', f'{current.version_label or current.original_filename} ({current.data_date})')
        _p(True, 'PREVIOUS resolved', f'{previous.version_label or previous.original_filename} ({previous.data_date})')

        # ── Step 3: cost-loading check ──────────────────────────────────
        current_cost_loaded = any(a.get('isCostLoaded') for a in current.activities_json)
        all_ok &= _p(
            current_cost_loaded, 'Current version is cost-loaded (required for CPI/CV/EAC)',
            'no cost-loaded activities in current version - cost EVM will show Unavailable, not an error' if not current_cost_loaded else '',
        )

        # ── Step 4: Compare Updates ──────────────────────────────────────
        try:
            from scheduler.schedule_comparison import compare_schedules
            comparison_result = compare_schedules(previous.activities_json, current.activities_json)
            _p(True, 'Compare Updates engine runs', f"{comparison_result['activities']['changedActivityCount']} changed activities")
        except Exception as exc:
            all_ok = _p(False, 'Compare Updates engine runs', str(exc))

        # ── Step 5: trend engine ────────────────────────────────────────
        try:
            series, _ = report_service.snapshot_series(project, 'DURATION_PCT_COMPLETE', 'LINEAR_BASELINE_SPREAD')
            comp = trend_engine.build_comparison(series)
            _p(comp['available'], 'CPI/SPI trend comparison computes', f"CPI direction: {comp['metrics']['cpi']['direction']}")
        except Exception as exc:
            all_ok = _p(False, 'CPI/SPI trend comparison computes', str(exc))

        # ── Step 6: EAC drift ────────────────────────────────────────────
        try:
            eac_drift = trend_engine.build_eac_drift(series, report_service.latest_approved_eac(project))
            _p(eac_drift['available'], 'EAC drift computes', f"{len(eac_drift['scenarios'])} scenario(s) tracked")
        except Exception as exc:
            all_ok = _p(False, 'EAC drift computes', str(exc))

        # ── Step 7: driver movement ──────────────────────────────────────
        try:
            drivers = driver_engine.rank_cost_drivers(current.activities_json, 'discipline', current.data_date)
            _p(True, 'Driver ranking computes', f"{len(drivers['drivers'])} ranked group(s), reconciled={drivers['reconciliation'].get('reconciled')}")
        except Exception as exc:
            all_ok = _p(False, 'Driver ranking computes', str(exc))

        # ── Step 8: executive narrative ──────────────────────────────────
        try:
            exec_payload = report_service.build_executive_payload(project)
            has_narrative = any(exec_payload['narrative']['sections'].values())
            _p(has_narrative, 'Executive narrative produces at least one sentence', '' if has_narrative else 'all sections None - check cost-loading')
        except Exception as exc:
            all_ok = _p(False, 'Executive narrative builds', str(exc))

        # ── Step 9: report generation (weekly) ───────────────────────────
        try:
            report_payload = report_service.build_report_payload(project, 'WEEKLY_PROJECT_CONTROLS')
            _p(True, 'Weekly report payload builds', f"sections: {', '.join(report_payload['sections'])}")
        except Exception as exc:
            all_ok = _p(False, 'Weekly report payload builds', str(exc))

        # ── Step 10: PDF/Excel export ─────────────────────────────────────
        try:
            from scheduler.report_export import generate_report_excel, generate_report_pdf
            pdf_bytes = generate_report_pdf(report_payload)
            xlsx_bytes = generate_report_excel(report_payload)
            _p(pdf_bytes.startswith(b'%PDF'), 'PDF export produces a valid PDF', f'{len(pdf_bytes)} bytes')
            _p(xlsx_bytes.startswith(b'PK'), 'Excel export produces a valid workbook', f'{len(xlsx_bytes)} bytes')
        except Exception as exc:
            all_ok = _p(False, 'PDF/Excel export', str(exc))

        print()
        self._print_summary(all_ok)
        print(
            '\nNothing was written to the database by this command - it only reads '
            'existing versions and calls the same pure functions the API uses.\n'
        )

    def _print_summary(self, all_ok: bool):
        if all_ok:
            print('  Overall: READY - this project can validate the full workflow end-to-end.')
        else:
            print('  Overall: NOT YET READY - see FAIL lines above for what to address.')

    def _list_candidates(self):
        print('\nProjects and their schedule-version counts:\n')
        for project in Project.objects.all().order_by('name'):
            versions = list(project.schedule_versions.all())
            dated = [v for v in versions if v.data_date]
            cost_loaded_versions = sum(
                1 for v in versions if any(a.get('isCostLoaded') for a in v.activities_json)
            )
            flag = 'READY' if len(dated) >= 2 else 'needs a 2nd dated version'
            print(f'  {project.name} ({project.id}): {len(versions)} version(s), {len(dated)} dated, {cost_loaded_versions} cost-loaded - {flag}')
        print(
            '\nRun: python manage.py validate_controls_workflow <project_id>\n'
            'once a project shows 2+ dated versions.\n'
        )
