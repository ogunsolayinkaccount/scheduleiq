"""
Small, deterministic fixtures shared by the ScheduleIQ test suite.
No live external dependency — the XER fixture is a hand-built minimal export
covering exactly the sections Phase 2's gap-fill parses.
"""

SAMPLE_XER = "\n".join([
    "%T\tPROJECT",
    "%F\tproj_id\tproj_short_name\tlast_recalc_date\tplan_start_date\tscd_end_date\tplan_end_date\tclndr_id",
    "%R\tPROJ1\tTest Project\t2026-08-01\t2026-01-01\t2026-12-31\t2026-12-31\tCAL1",
    "%T\tPROJWBS",
    "%F\twbs_id\twbs_name\twbs_short_name\tparent_wbs_id\tseq_num\tproj_id",
    "%R\tWBS1\tArea A\tAREAA\t\t1\tPROJ1",
    "%T\tCALENDAR",
    "%F\tclndr_id\tclndr_name\tclndr_type\tdefault_flag\tday_hr_cnt\tweek_hr_cnt\tmonth_hr_cnt\tyear_hr_cnt\tclndr_data",
    "%R\tCAL1\tStandard 5 Day Workweek\tCA_Base\tY\t8\t40\t172\t2000\t",
    "%T\tRSRC",
    "%F\trsrc_id\trsrc_name\trsrc_short_name\trsrc_type",
    "%R\tRSRC1\tJohn Smith\tJSMITH\tRT_Labor",
    "%T\tACTVTYPE",
    "%F\tactv_code_type_id\tactv_code_type\tactv_code_type_scope",
    "%R\tACT1\tDiscipline\tAS_Project",
    "%T\tACTVCODE",
    "%F\tactv_code_id\tactv_code_type_id\tshort_name\tactv_code_name\tparent_actv_code_id",
    "%R\tCODE1\tACT1\tElectrical\tElectrical Discipline\t",
    "%T\tTASKACTV",
    "%F\ttask_id\tactv_code_type_id\tactv_code_id",
    "%R\tTASK1\tACT1\tCODE1",
    "%T\tUDFTYPE",
    "%F\tudf_type_id\tudf_type_label\ttable_name\tlogical_data_type",
    "%R\tUDF1\tContract Reference\tTASK\tSD_Text",
    "%T\tUDFVALUE",
    "%F\tudf_type_id\tfk_id\tudf_text",
    "%R\tUDF1\tTASK1\tCR-2024-001",
    "%T\tTASK",
    "%F\ttask_id\ttask_code\ttask_name\tproj_id\twbs_id\ttask_type\tstatus_code\ttarget_drtn_hr_cnt\tremain_drtn_hr_cnt\ttotal_float_hr_cnt\tfree_float_hr_cnt\tearly_start_date\tearly_end_date\tlate_start_date\tlate_end_date\ttarget_start_date\ttarget_end_date\tact_start_date\tact_end_date\trestart_date\treend_date\tclndr_id\tcomplete_pct_type\tphys_complete_pct\tcstr_type\tcstr_date\tdriving_path_flag",
    "%R\tTASK1\tA1000\tMobilize Site\tPROJ1\tWBS1\tTT_Task\tTK_Active\t40\t40\t0\t0\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t2026-01-05\t2026-01-09\t\t\t2026-01-05\t2026-01-09\tCAL1\tCP_Drtn\t0\t\t\tY",
    "%R\tTASK2\tA1010\tExcavate Foundations\tPROJ1\tWBS1\tTT_Task\tTK_NotStart\t80\t80\t5\t5\t2026-01-10\t2026-01-19\t2026-01-15\t2026-01-24\t2026-01-10\t2026-01-19\t\t\t2026-01-10\t2026-01-19\tCAL1\tCP_Drtn\t0\t\t\tN",
    "%T\tTASKPRED",
    "%F\ttask_id\tpred_task_id\tpred_type\tlag_hr_cnt",
    "%R\tTASK2\tTASK1\tPR_FS\t0",
    "%T\tTASKRSRC",
    "%F\ttask_id\trsrc_id\ttarget_qty\tact_reg_qty\tact_ot_qty\tremain_qty\ttarget_cost\tact_reg_cost\tact_ot_cost\tremain_cost\tis_primary_rsrc",
    "%R\tTASK1\tRSRC1\t40\t40\t0\t0\t2000\t2000\t0\t0\tY",
]) + "\n"


def make_activity(
    code, name='Activity', wbs='General', b_start='2026-01-01', b_finish='2026-01-10',
    dur=10.0, total_float=0.0, pct_complete=0.0, is_critical=None, is_milestone=False,
    predecessors=None, **extra,
):
    a = {
        'id': code, 'code': code, 'name': name, 'wbs': wbs,
        'bStart': b_start, 'bFinish': b_finish, 'start': None, 'finish': None,
        'dur': dur, 'remainDur': dur * (1 - pct_complete / 100.0), 'totalFloat': total_float,
        'freeFloat': total_float, 'pctComplete': pct_complete,
        'isCritical': (total_float <= 0) if is_critical is None else is_critical,
        'isMilestone': is_milestone,
        'predecessors': predecessors or [], 'successors': [],
    }
    a.update(extra)
    return a
