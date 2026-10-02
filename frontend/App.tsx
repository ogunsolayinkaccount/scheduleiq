import { useState, useCallback, useMemo, useRef, useEffect, Fragment, createContext, useContext } from "react";
import { idbSave, idbLoadAll, idbDelete } from "./idb";
import { notifyProjectsChanged } from "./projectEvents";
import IntelligenceSync from "./IntelligenceSync";
import ImportCenter from "./ImportCenter";
import UpdateAnalysis from "./UpdateAnalysis";
import ProjectControls from "./ProjectControls";
import BaselineProgress from "./BaselineProgress";
import GanttView from "./GanttView";
import RiskIntelligence from "./RiskIntelligence";
import ActivityAnalysis from "./ActivityAnalysis";
import FloatAnalysis from "./FloatAnalysis";
import Intelligence from "./Intelligence";
import Dashboard from "./Dashboard";
import FieldDashboard from "./FieldDashboard";
import Reports from "./Reports";
import { applyLogicEdits, computeCPM, wouldCreateCycle, linkKey, type RelType, type LogicEdit, type LogicEditMap } from "./cpm";
import { pickDrivingRel, tracePath } from "./pathTrace";
import { legacyFilterToActivityAnalysis, activityJumpFilter } from "./activityNavigation";
import {
  Area, BarChart, Bar, PieChart, Pie, Cell, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
  ReferenceLine, ComposedChart
} from "recharts";

// ─── THEME ────────────────────────────────────────────────────────────────────
const C = {
  bg:"#f5f2ec",      // warm off-white background
  panel:"#ede9df",   // slightly deeper warm off-white
  card:"#ffffff",    // white card surface
  card2:"#f0ece4",   // secondary card surface
  border:"#d4ccc0",  // warm light border
  accent:"#d97000",  // orange — darker for readability on white
  gold:"#b89200",    // darker gold
  green:"#00936b",   // darker green for white bg
  amber:"#c47c00",   // darker amber
  red:"#d93030",     // semantic red
  purple:"#7c3aed",  // darker purple
  orange:"#c85000",  // secondary orange
  text:"#1c1410",    // dark warm text
  muted:"#7a6454",   // readable muted brown
  muted2:"#a08870",  // lighter muted
};
const PROJ_COLORS=["#00c8f0","#00e5a0","#d4a843","#a78bfa","#ff5757","#fb923c","#f472b6","#06b6d4","#84cc16","#f59e0b","#8b5cf6","#ef4444","#14b8a6","#e879f9"];

// Standardised S-curve / histogram series colours
const PLAN_COLOR='#3b82f6';   // blue   — Planned line in all curves
const BGT_COLOR ='#8b5cf6';   // purple — Budgeted line in all curves

// Context that carries the current project/file name into every CC and Sec for print headers
const PrintProjectContext=createContext<string>('');

// Context carrying schedule-logic (predecessor/successor) edits + mutators down to
// LogicPanel, wherever it's rendered, without prop-drilling through every table view.
interface LogicEditCtxValue{
  logicEdits:LogicEditMap;
  editRelationship:(predId:string,succId:string,chg:Partial<LogicEdit>)=>void;
  deleteRelationship:(predId:string,succId:string)=>void;
  addRelationship:(predId:string,succId:string,relType:RelType,lagDays:number)=>boolean;
  allActivities:any[];
}
const LogicEditContext=createContext<LogicEditCtxValue>({
  logicEdits:{},
  editRelationship:()=>{},
  deleteRelationship:()=>{},
  addRelationship:()=>false,
  allActivities:[],
});

// ─── EPC PHASE SYSTEM ─────────────────────────────────────────────────────────
const EPC_PHASES:{[k:string]:{key:string;label:string;short:string;color:string;icon:string;bg:string}}={
  E:  {key:'E',  label:'Engineering',   short:'ENG',  color:'#00c8f0', icon:'📐', bg:'rgba(0,200,240,0.06)' },
  P:  {key:'P',  label:'Procurement',   short:'PROC', color:'#ffd966', icon:'📦', bg:'rgba(255,217,102,0.06)'},
  C:  {key:'C',  label:'Construction',  short:'CONS', color:'#00e5a0', icon:'🏗',  bg:'rgba(0,229,160,0.06)' },
  CS: {key:'CS', label:'Commissioning', short:'CSU',  color:'#c084fc', icon:'⚡',  bg:'rgba(192,132,252,0.06)'},
  PM: {key:'PM', label:'Project Mgmt',  short:'PM',   color:'#d4884a', icon:'📊', bg:'rgba(212,136,74,0.06)' },
  '?':{key:'?',  label:'Unclassified',  short:'',     color:'#6b7280', icon:'📋', bg:'rgba(107,114,128,0.04)'},
};
const EPC_ORDER=['E','P','C','CS','PM','?'];

function getEpcPhase(wbs:string,wbsPath?:string):{key:string;label:string;short:string;color:string;icon:string;bg:string}{
  const t=((wbsPath||'')+' '+wbs).toUpperCase();
  // Commissioning / Startup first (avoid matching "C" for construction)
  if(/COMMISS|CSU\b|START.?UP|PRE.?COMM|HANDOVER|TURNOVER|MECH.?COMP|READY.?FOR.?COMM/.test(t)) return EPC_PHASES.CS;
  // Engineering / Design
  if(/ENGINEE|DESIGN|FEED\b|FRONT.?END|BASIC.?DES|DETAIL.*ENG|DRAFT|DOCUMENT|STUDY|HAZOP|P&ID|MODELL|PROCESS.?ENG|PIPING.?DES/.test(t)) return EPC_PHASES.E;
  // Procurement
  if(/PROCURE|PURCHASE|VENDOR|SUPPLY\b|MATERIAL|BULK.?MAT|EXPEDIT|LOGISTIC|FREIGHT|LONG.?LEAD|SOURCING/.test(t)) return EPC_PHASES.P;
  // Construction / Civil / Installation
  if(/CONSTR|INSTALL|ERECT|FABRICAT|CIVIL|MECHANIC|PIPING\b|ELECTRI|INSTRUMENT|EXCAV|FOUNDATION|SITE.?WORK|SCAFFOLD|CONCRET|WELDING|STRUCTURAL/.test(t)) return EPC_PHASES.C;
  // Project Management
  if(/PROJ.?MAN|PMT?\b|MANAGEMENT|ADMIN|HSE\b|SAFETY\b|QUALITY\b|QA\b|QC\b|CONTROLS|FINANCE|SCHEDULE\b|PLAN(NING)?\b/.test(t)) return EPC_PHASES.PM;
  // Single-letter WBS code at the start (e.g. "E > Civil" or "P-001")
  const firstSeg=t.split(/[\s.\-\/]/)[0].trim();
  if(firstSeg==='E') return EPC_PHASES.E;
  if(firstSeg==='P') return EPC_PHASES.P;
  if(firstSeg==='C') return EPC_PHASES.C;
  return EPC_PHASES['?'];
}

// ─── HELPERS ──────────────────────────────────────────────────────────────────
function hasActualSuffix(v:any):boolean{
  if(!v||v instanceof Date)return false;
  return String(v).trim().toUpperCase().endsWith('A');
}
function parseDate(v:any):Date|null{
  if(!v)return null;
  if(v instanceof Date)return isNaN(v.getTime())?null:v;
  // Strip Primavera P6 "A" suffix (denotes Actual date) before parsing
  let s=String(v).trim();
  if(s.toUpperCase().endsWith('A'))s=s.slice(0,-1).trim();
  const m1=s.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$/);
  if(m1){const mo:any={jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};let yr=parseInt(m1[3]);if(yr<100)yr+=2000;return new Date(yr,mo[m1[2].toLowerCase()],parseInt(m1[1]));}
  // Parse YYYY-MM-DD in local time (avoids UTC offset display issues)
  const iso=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(iso)return new Date(+iso[1],+iso[2]-1,+iso[3]);
  const d=new Date(s);return isNaN(d.getTime())?null:d;
}
const fmtDate=(d:Date|null)=>d?d.toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"}):"—";
const fmtShort=(d:Date|null)=>d?d.toLocaleDateString("en-US",{month:"short",year:"2-digit"}):"—";
const pct=(v:number,t:number)=>t>0?Math.round((v/t)*100):0;
const fmtDateExport=(v:any):string=>{if(!v)return'';const d=v instanceof Date?v:new Date(v);return isNaN(d.getTime())?String(v):d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});};

// ─── EXPORT UTILITIES ─────────────────────────────────────────────────────────
function downloadCSV(rows:any[],cols:[string,string][],filename:string,getCellText:(r:any,k:string)=>string){
  const header=cols.map(([,l])=>`"${l.replace(/"/g,'""')}"`).join(',');
  const lines=rows.map(r=>cols.map(([k])=>{const v=getCellText(r,k)||'';return v.includes(',')||v.includes('"')||v.includes('\n')?`"${v.replace(/"/g,'""')}"`:v;}).join(','));
  const csv='﻿'+[header,...lines].join('\r\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=`${filename}.csv`;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),1500);
}
function printTable(rows:any[],cols:[string,string][],title:string,filename:string,getCellText:(r:any,k:string)=>string){
  const w=window.open('','_blank','width=1200,height=800');
  if(!w)return;
  const safe=filename.replace(/'/g,"\\'");
  const th=cols.map(([,l])=>`<th>${l}</th>`).join('');
  const tbody=rows.map((r,i)=>`<tr class="${i%2===0?'even':'odd'}">${cols.map(([k])=>`<td>${(getCellText(r,k)||'').replace(/</g,'&lt;').replace(/>/g,'&gt;')}</td>`).join('')}</tr>`).join('');
  w.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${title}</title><style>
    *{box-sizing:border-box;margin:0;padding:0}body{font-family:Arial,sans-serif;font-size:11px;color:#111;padding:20px}
    h2{font-size:15px;font-weight:700;margin-bottom:2px}
    .meta{font-size:11px;color:#555;margin-bottom:14px}
    table{border-collapse:collapse;width:100%;table-layout:auto}
    th{background:#1e3a5f;color:#fff;padding:6px 10px;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
    td{padding:5px 10px;border-bottom:1px solid #e2e8f0;vertical-align:top;font-size:11px}
    tr.even td{background:#fff}tr.odd td{background:#f8fafc}
    .btn{display:inline-block;padding:7px 18px;background:#2563eb;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;margin-right:8px}
    .btn-xl{background:#16a34a}
    @media print{.no-print{display:none}@page{margin:15mm;size:landscape}}
  </style></head><body>
  <div class="no-print" style="margin-bottom:14px">
    <button class="btn" onclick="window.print()">Print / Save as PDF</button>
    <button class="btn btn-xl" onclick="exportCSV()">Download Excel / CSV</button>
  </div>
  <h2>${title}</h2>
  <p class="meta">${rows.length.toLocaleString()} activities &nbsp;&middot;&nbsp; Exported ${new Date().toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'})}</p>
  <table id="tbl"><thead><tr>${th}</tr></thead><tbody>${tbody}</tbody></table>
  <script>
  function exportCSV(){
    var r=[...document.querySelectorAll('#tbl tr')].map(function(row){return[...row.querySelectorAll('th,td')].map(function(c){var t=c.innerText||'';return(t.indexOf(',')>=0||t.indexOf('"')>=0)?'"'+t.replace(/"/g,'""')+'"':t;}).join(',');});
    var blob=new Blob(['\\uFEFF'+r.join('\\r\\n')],{type:'text/csv;charset=utf-8'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='${safe}.csv';a.click();
  }
  <\/script>
  </body></html>`);
  w.document.close();
}

// ─── BASELINE DETECTION ───────────────────────────────────────────────────────
function detectBaseline(activities:any[]):{hasBaseline:boolean;coverage:number;isReal:boolean;status:string;color:string}{
  const nonMS=activities.filter(a=>!a.isMilestone);
  if(!nonMS.length)return{hasBaseline:false,coverage:0,isReal:false,status:"No Baseline",color:C.red};

  const withBoth=nonMS.filter(a=>a.bStart&&a.bFinish&&!a._syntheticBL);
  const coverage=Math.round((withBoth.length/nonMS.length)*100);

  // A "real" baseline has dates that diverge from the current plan by at least 1 day
  const getT=(d:any)=>d instanceof Date?d.getTime():d?new Date(d).getTime():null;
  const withDiv=withBoth.filter(a=>{
    const bfT=getT(a.bFinish),fT=getT(a.finish);
    const bsT=getT(a.bStart), sT=getT(a.start);
    return(bfT&&fT&&Math.abs(bfT-fT)>86400000)||(bsT&&sT&&Math.abs(bsT-sT)>86400000);
  });
  const isReal=withDiv.length>withBoth.length*0.05; // >5% of activities show real divergence

  let status:string; let color:string;
  if(coverage>=80&&isReal){status="Baselined";      color=C.green;}
  else if(coverage>=40||(coverage>=20&&isReal)){status="Partial Baseline";color=C.amber;}
  else{status="No Baseline";color=C.red;}

  return{hasBaseline:coverage>=50&&isReal,coverage,isReal,status,color};
}

// ─── P6 DATA DATE PROJECTION ─────────────────────────────────────────────────
// Implements Oracle P6 forecast date methodology:
//
//  Forecast Start
//    • In-progress  → act_start_date  (work already underway)
//    • Not-started  → restart_date (remainStart) if set
//                   → early_start_date (earlyStart) from CPM forward pass
//                   → Data Date  (cannot schedule before DD)
//
//  Forecast Finish
//    • Complete     → act_end_date  (locked)
//    • Otherwise    → reend_date (remainFinish) — P6 remaining early finish
//                   → early_end_date (earlyFinish) — CPM forward-pass finish
//                   → Data Date + Remaining Duration  (fallback)
//
//  Total Float (adjusted)
//    → late_end_date (lateFinish) – Forecast Finish  when CPM late dates available
//    → BL Finish + original float – Forecast Finish  otherwise
// Picks the effective Data Date to drive the toolbar from a list of
// session file objects — the last one (most recently imported/loaded)
// that actually carries an effective dataDate. Never falls back to
// today's date itself; callers decide what to do when this returns null
// (typically: leave the toolbar's current value untouched).
function pickEffectiveDataDate(list:any[]):string|null{
  for(let i=list.length-1;i>=0;i--){
    if(list[i]?.dataDate)return list[i].dataDate;
  }
  return null;
}

function applyDataDate(activities:any[], dataDateStr:string):any[]{
  const dd=parseDate(dataDateStr);
  if(!dd)return activities;

  return activities.map(a=>{
    const isComplete=(a.pctComplete||0)>=100||a.totalFloat==null||a.status==="TK_Complete";
    if(isComplete)return a; // locked — completed work never moves

    const remainDur=a.remainDur??a.dur??0;
    const bStart:Date|null  =a.bStart   instanceof Date?a.bStart   :null;
    const bFinish:Date|null =a.bFinish  instanceof Date?a.bFinish  :null;
    const hasActStart=a.start!=null;

    // ── Forecast Start (P6 methodology) ──────────────────────────────────────
    let projStart:Date;
    if(hasActStart){
      // In-progress: actual start is the forecast start
      projStart=a.start instanceof Date?a.start:new Date(a.start);
    }else{
      // Not-started: P6 uses Remaining Early Start (restart_date) → Early Start → DD
      const rs=a.remainStart instanceof Date?a.remainStart:null;
      const es=a.earlyStart  instanceof Date?a.earlyStart :null;
      const candidate=rs??es??null;
      projStart=candidate&&candidate>dd?candidate:dd;
    }

    // ── Forecast Finish (P6 methodology) ─────────────────────────────────────
    // Priority: reend_date (remainFinish) → early_end_date (earlyFinish) → DD + remainDur
    let projFinish:Date;
    if(a.remainFinish instanceof Date){
      projFinish=a.remainFinish;
    }else if(a.earlyFinish instanceof Date){
      projFinish=a.earlyFinish;
    }else{
      projFinish=new Date(dd.getTime()+remainDur*86400000);
    }

    // ── Variances vs baseline ─────────────────────────────────────────────────
    const finishVar=bFinish?Math.round((projFinish.getTime()-bFinish.getTime())/86400000):null;
    const startVar =bStart ?Math.round((projStart.getTime() -bStart.getTime()) /86400000):null;

    // ── Total Float: Late Finish – Forecast Finish ────────────────────────────
    // P6 definition: TF = Late Finish – Early Finish (CPM)
    // When late_end_date is available use it; otherwise approximate via BL finish + original TF
    let adjFloat:number|null=a.totalFloat;
    const lateF:Date|null=a.lateFinish instanceof Date?a.lateFinish
      :bFinish!=null&&a.totalFloat!=null?new Date(bFinish.getTime()+a.totalFloat*86400000)
      :null;
    if(lateF)adjFloat=Math.round((lateF.getTime()-projFinish.getTime())/86400000);

    return{
      ...a,
      projectedStart:  projStart,
      projectedFinish: projFinish,
      finishVariance:  finishVar,
      startVariance:   startVar,
      adjustedFloat:   adjFloat,
      isCritical:      adjFloat!=null?adjFloat<=0:a.isCritical,
    };
  });
}

// ─── BASELINE FILL ────────────────────────────────────────────────────────────
// When no P6 baseline snapshot exists (target_start_date / target_end_date are
// null), synthesise BL dates so BL columns are never blank:
//
//   Complete   → actual start / actual finish
//   Otherwise  → earlyStart (CPM early_start_date) if available, else projectedStart
//              → earlyFinish (CPM early_end_date)   if available, else projectedFinish
//
// Using CPM early dates as the synthetic baseline preserves P6 semantics: the
// scheduled dates ARE the plan when no separate baseline has been assigned.
// _syntheticBL:true prevents detectBaseline() from counting these as real.
function fillBaselineDates(activities:any[]):any[]{
  return activities.map(a=>{
    if(a.bStart!=null&&a.bFinish!=null)return a;
    const isComp=(a.pctComplete||0)>=100;
    const synBS=isComp
      ?(a.start instanceof Date?a.start:a.start?new Date(a.start):null)
      :(a.earlyStart instanceof Date?a.earlyStart:a.projectedStart instanceof Date?a.projectedStart:null);
    const synBF=isComp
      ?(a.finish instanceof Date?a.finish:a.finish?new Date(a.finish):null)
      :(a.earlyFinish instanceof Date?a.earlyFinish:a.projectedFinish instanceof Date?a.projectedFinish:null);
    if(!synBS&&!synBF)return a;
    return{...a,bStart:synBS??a.bStart,bFinish:synBF??a.bFinish,finishVariance:0,startVariance:0,_syntheticBL:true};
  });
}

// ─── API HELPERS ──────────────────────────────────────────────────────────────
// Empty string = use Vite proxy (recommended). Set to "http://localhost:8000" if not using proxy.
const API = "";

const DATE_FIELDS = ["start","finish","bStart","bFinish","earlyStart","earlyFinish","lateStart","lateFinish","remainStart","remainFinish"] as const;

function deserializeActivity(a:any):any{
  const out={...a};
  // Detect P6 "A" (Actual) suffix before date strings are parsed away
  const actualStart  = hasActualSuffix(a.start);
  const actualFinish = hasActualSuffix(a.finish);
  for(const f of DATE_FIELDS) out[f]=a[f]?parseDate(a[f]):null;
  // Completion signals — activities with ANY float value (neg/zero/positive) are NOT complete.
  // Only two valid P6 completion indicators:
  //   1. null/empty total float  → P6 clears float when an activity finishes
  //   2. "A" suffix on BOTH start AND finish → explicit actual-date markers in raw XER
  const tfEmpty     = out.totalFloat==null || out.totalFloat==="";
  const aActualBoth = actualStart && actualFinish;

  if(tfEmpty || aActualBoth){
    out.pctComplete = 100;
    out.status      = "TK_Complete";
  } else if(actualStart || out.start!==null){
    if(!out.status) out.status="TK_Active";
  } else if(!out.status){
    out.status="TK_NotStart";
  }
  return out;
}

function actToJSON(a:any):any{
  const out={...a};
  for(const f of DATE_FIELDS){
    const v=a[f];
    out[f]=v instanceof Date?`${v.getFullYear()}-${String(v.getMonth()+1).padStart(2,"0")}-${String(v.getDate()).padStart(2,"0")}`:null;
  }
  return out;
}

function deserializeMetrics(m:any):any{
  if(!m)return null;
  const out={...m};
  if(out.negFloatActs)out.negFloatActs=out.negFloatActs.map(deserializeActivity);
  return out;
}

async function processFileViaAPI(file:File):Promise<any>{
  const fd=new FormData();
  fd.append("file",file);
  const ctrl=new AbortController();
  const timer=setTimeout(()=>ctrl.abort(),90_000); // 90-second timeout
  try{
    const r=await fetch(`${API}/api/upload/`,{method:"POST",body:fd,signal:ctrl.signal});
    clearTimeout(timer);
    if(!r.ok){
      let msg=`Server error ${r.status}`;
      try{const j=await r.json();msg=j.error||msg;}catch{}
      throw new Error(msg);
    }
    const data=await r.json();
    if(data.error)throw new Error(data.error);
    data.activities=data.activities.map(deserializeActivity);
    return data;
  }catch(e:any){
    clearTimeout(timer);
    if(e.name==='AbortError')throw new Error('Upload timed out after 90 seconds. Try a smaller file or export a subset of activities.');
    if(e.message==='Failed to fetch'||e.message?.includes('NetworkError')||e.message?.includes('ERR_CONNECTION'))
      throw new Error('Cannot reach the ScheduleIQ server. If you are viewing this as a standalone HTML file, use "Load 5-project demo" to explore without a server.');
    throw e;
  }
}

// ─── MULTI-SELECT DROPDOWN ────────────────────────────────────────────────────
function ProjectSelector({files,selectedIds,onChange,onDelete}:any){
  const [open,setOpen]=useState(false);
  const ref=useRef<HTMLDivElement>(null);
  useEffect(()=>{const h=(e:MouseEvent)=>{if(ref.current&&!ref.current.contains(e.target as Node))setOpen(false);};document.addEventListener("mousedown",h);return()=>document.removeEventListener("mousedown",h);},[]);
  const allIds=files.map((f:any)=>f.id);
  const allSelected=selectedIds.length===allIds.length;
  const label=allSelected?"All Projects":selectedIds.length===0?"No Projects Selected":`${selectedIds.length} Project${selectedIds.length>1?"s":""} Selected`;

  const toggle=(id:any,e:React.MouseEvent)=>{
    if(e.ctrlKey||e.metaKey){onChange(selectedIds.includes(id)?selectedIds.filter((x:any)=>x!==id):[...selectedIds,id]);}
    else{if(selectedIds.length===1&&selectedIds[0]===id){onChange(allIds);}else{onChange([id]);}}
  };
  const selectAll=()=>onChange(allIds);
  const clearAll=()=>onChange([]);

  return(
    <div ref={ref} style={{position:"relative",minWidth:260,userSelect:"none"}}>
      <div onClick={()=>setOpen(o=>!o)} style={{display:"flex",alignItems:"center",gap:8,background:C.card,border:`1px solid ${open?C.accent:C.border}`,borderRadius:9,padding:"8px 14px",cursor:"pointer",transition:"border-color 0.15s"}}>
        <div style={{flex:1}}>
          <div style={{fontSize:10,color:C.muted,textTransform:"uppercase",letterSpacing:"0.07em",marginBottom:1}}>Analyzing</div>
          <div style={{fontSize:13,fontWeight:600,color:allSelected?C.accent:C.amber,whiteSpace:"nowrap",overflow:"hidden",textOverflow:"ellipsis",maxWidth:220}}>{label}</div>
        </div>
        <div style={{color:C.muted,fontSize:12,transition:"transform 0.15s",transform:open?"rotate(180deg)":"none"}}>▾</div>
      </div>
      {open&&(
        <div style={{position:"absolute",top:"calc(100% + 6px)",left:0,right:0,background:C.panel,border:`1px solid ${C.border}`,borderRadius:10,boxShadow:"0 8px 32px rgba(0,0,0,0.5)",zIndex:999,minWidth:300,overflow:"hidden"}}>
          <div style={{display:"flex",gap:6,padding:"10px 12px",borderBottom:`1px solid ${C.border}`,background:C.card}}>
            <button onClick={selectAll} style={{flex:1,background:allSelected?"rgba(0,200,240,0.15)":"transparent",border:`1px solid ${allSelected?C.accent:C.border}`,color:allSelected?C.accent:C.muted2,borderRadius:6,padding:"5px 10px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:600,transition:"all 0.13s"}}>✓ All Projects</button>
            <button onClick={clearAll} style={{flex:1,background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"5px 10px",cursor:"pointer",fontSize:12,fontFamily:"inherit",transition:"all 0.13s"}}>✕ Clear</button>
          </div>
          <div style={{padding:"6px 12px",fontSize:10,color:C.muted,background:C.card2,borderBottom:`1px solid ${C.border}`}}>
            Click = select only this · <kbd style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:3,padding:"1px 4px",fontSize:10,color:C.muted2}}>Ctrl</kbd> + Click = add/remove · <span style={{color:C.accent}}>✓ All</span> = reset to all
          </div>
          <div style={{maxHeight:320,overflowY:"auto"}}>
            {files.map((f:any,i:number)=>{
              const sel=selectedIds.includes(f.id);
              const color=PROJ_COLORS[i%PROJ_COLORS.length];
              const bl=detectBaseline(f.activities||[]);
              return(
                <div key={f.id} onClick={e=>toggle(f.id,e)} style={{display:"flex",alignItems:"center",gap:10,padding:"10px 14px",cursor:"pointer",background:sel?"rgba(0,200,240,0.06)":"transparent",borderBottom:`1px solid ${C.border}`,transition:"background 0.1s"}}>
                  <div style={{width:18,height:18,borderRadius:5,border:`2px solid ${sel?color:C.border}`,background:sel?`${color}22`:"transparent",display:"flex",alignItems:"center",justifyContent:"center",flexShrink:0,transition:"all 0.13s"}}>
                    {sel&&<div style={{width:8,height:8,borderRadius:2,background:color}}/>}
                  </div>
                  <div style={{width:7,height:7,borderRadius:"50%",background:color,flexShrink:0}}/>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{fontSize:13,fontWeight:sel?600:400,color:sel?C.text:C.muted2,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{f.name}</div>
                    <div style={{fontSize:10,color:C.muted,display:"flex",alignItems:"center",gap:5,marginTop:2,flexWrap:"wrap"}}>
                      <span>{f.actCount.toLocaleString()} acts · {f.source.toUpperCase()}</span>
                      <span style={{padding:"1px 6px",borderRadius:8,background:`${bl.color}18`,color:bl.color,fontWeight:700,fontSize:9,border:`1px solid ${bl.color}30`}}
                        title={bl.hasBaseline?`Baseline covers ${bl.coverage}% of activities`:"No baseline detected — forecast dates will be used as baseline"}>
                        {bl.hasBaseline?"✓ Baselined":bl.coverage>0?"~ Partial":bl.status}
                      </span>
                      {f.isCostLoaded&&<span style={{padding:"1px 6px",borderRadius:8,background:"rgba(0,229,160,0.12)",color:C.green,fontWeight:700,fontSize:9,border:"1px solid rgba(0,229,160,0.3)"}}
                        title={`Cost loaded — ${f.costLoadPct??0}% of activities have budgeted cost. EVM uses real dollar values.`}>
                        💰 Cost {f.costLoadPct??0}%
                      </span>}
                      {!f.isCostLoaded&&f.isResourceLoaded&&<span style={{padding:"1px 6px",borderRadius:8,background:"rgba(255,193,7,0.12)",color:C.amber,fontWeight:700,fontSize:9,border:"1px solid rgba(255,193,7,0.3)"}}
                        title={`Resource loaded — ${f.resourceLoadPct??0}% of activities have budgeted hours. EVM uses man-hours.`}>
                        ⚙ MH {f.resourceLoadPct??0}%
                      </span>}
                      {!f.isCostLoaded&&!f.isResourceLoaded&&f.source==="xer"&&<span style={{padding:"1px 6px",borderRadius:8,background:"rgba(107,114,128,0.12)",color:C.muted2,fontWeight:700,fontSize:9,border:`1px solid ${C.border}`}}
                        title="No resource or cost assignments found — EVM will be duration-based">
                        ⏱ Duration only
                      </span>}
                    </div>
                  </div>
                  {sel&&<div style={{fontSize:10,color:color,fontWeight:600}}>✓</div>}
                  {onDelete&&(
                    <button
                      type="button"
                      title="Delete project"
                      onClick={e=>{e.stopPropagation();const msg=f.scheduleUploadId?`Delete "${f.name}"?

This removes this schedule version from ScheduleIQ, including the Intelligence Portal, together with its dependent analysis data. Other versions of the same project are kept.

This cannot be undone.`:`Delete "${f.name}"? This cannot be undone.`;if(window.confirm(msg))onDelete(f.id);}}
                      style={{background:'transparent',border:'none',color:C.muted,cursor:'pointer',
                        fontSize:14,padding:'2px 4px',lineHeight:1,flexShrink:0,
                        borderRadius:4,transition:'color 0.15s'}}
                      onMouseEnter={e=>(e.currentTarget.style.color='#ff5757')}
                      onMouseLeave={e=>(e.currentTarget.style.color=C.muted)}
                    >🗑</button>
                  )}
                </div>
              );
            })}
          </div>
          <div style={{padding:"8px 12px",borderTop:`1px solid ${C.border}`,background:C.card,display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <span style={{fontSize:11,color:C.muted}}>{selectedIds.length} of {files.length} selected</span>
            <button onClick={()=>setOpen(false)} style={{background:C.accent,border:"none",color:"#000",borderRadius:6,padding:"5px 14px",cursor:"pointer",fontSize:12,fontWeight:700,fontFamily:"inherit"}}>Apply</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── GLOBAL SEARCH ────────────────────────────────────────────────────────────
function GlobalSearch({allActivities,onGoToActivity}:any){
  const [query,setQuery]=useState("");
  const [open,setOpen]=useState(false);
  const ref=useRef<HTMLDivElement>(null);

  useEffect(()=>{
    const h=(e:MouseEvent)=>{if(ref.current&&!ref.current.contains(e.target as Node))setOpen(false);};
    document.addEventListener("mousedown",h);
    return()=>document.removeEventListener("mousedown",h);
  },[]);

  const results=useMemo(()=>{
    const q=query.trim().toLowerCase();
    if(!q||q.length<2)return[];
    return allActivities.filter((a:any)=>
      (a.code||"").toLowerCase().includes(q)||
      (a.name||"").toLowerCase().includes(q)||
      (a.wbs||"").toLowerCase().includes(q)||
      (a.projectName||"").toLowerCase().includes(q)
    ).slice(0,60);
  },[query,allActivities]);

  const floatColor=(f:number)=>f<0?C.red:f===0?C.amber:f<=5?C.orange:C.green;

  return(
    <div ref={ref} style={{position:"relative",flex:"1 1 280px",maxWidth:400}}>
      <div style={{display:"flex",alignItems:"center",gap:8,background:C.card,border:`1px solid ${open&&results.length>0?C.accent:C.border}`,borderRadius:9,padding:"7px 12px",transition:"border-color 0.15s"}}>
        <span style={{color:C.muted,fontSize:14}}>🔍</span>
        <input
          value={query}
          onChange={e=>{setQuery(e.target.value);setOpen(true);}}
          onFocus={()=>setOpen(true)}
          placeholder="Search any activity, code, WBS…"
          style={{flex:1,background:"transparent",border:"none",outline:"none",color:C.text,fontSize:13,fontFamily:"inherit"}}
        />
        {query&&<button onClick={()=>{setQuery("");setOpen(false);}} style={{background:"transparent",border:"none",color:C.muted,cursor:"pointer",fontSize:14,padding:0,lineHeight:1}}>✕</button>}
      </div>

      {open&&query.length>=2&&(
        <div style={{position:"absolute",top:"calc(100% + 6px)",left:0,right:0,background:C.panel,border:`1px solid ${C.border}`,borderRadius:10,boxShadow:"0 8px 32px rgba(0,0,0,0.6)",zIndex:999,maxHeight:420,overflow:"hidden",display:"flex",flexDirection:"column"}}>
          <div style={{padding:"8px 14px",borderBottom:`1px solid ${C.border}`,fontSize:11,color:C.muted}}>
            {results.length===0?"No activities found":`${results.length} result${results.length!==1?"s":""} for "${query}"`}
          </div>
          <div style={{overflowY:"auto",flex:1}}>
            {results.map((a:any,i:number)=>(
              <div key={i} onClick={()=>{onGoToActivity(a);setOpen(false);setQuery("");}}
                style={{display:"flex",alignItems:"flex-start",gap:10,padding:"9px 14px",cursor:"pointer",borderBottom:`1px solid ${C.border}`,transition:"background 0.1s"}}
                onMouseEnter={e=>(e.currentTarget as HTMLElement).style.background="rgba(0,200,240,0.06)"}
                onMouseLeave={e=>(e.currentTarget as HTMLElement).style.background="transparent"}>
                {/* Float badge */}
                <div style={{minWidth:36,textAlign:"center",paddingTop:2}}>
                  <span style={{fontSize:10,fontWeight:700,color:a.totalFloat==null?C.muted2:floatColor(a.totalFloat),background:`${a.totalFloat==null?C.muted2:floatColor(a.totalFloat)}15`,padding:"2px 5px",borderRadius:4,fontFamily:"'DM Mono',monospace"}}>{a.totalFloat==null?"—":`${a.totalFloat}d`}</span>
                </div>
                <div style={{flex:1,minWidth:0}}>
                  <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:2}}>
                    <span style={{fontSize:10,color:C.muted,fontFamily:"'DM Mono',monospace",flexShrink:0}}>{a.code}</span>
                    {a.isCritical&&!a.isMilestone&&<span style={{fontSize:9,color:C.red,fontWeight:700}}>CRITICAL</span>}
                    {a.isMilestone&&<span style={{fontSize:9,color:C.purple,fontWeight:700}}>MILESTONE</span>}
                  </div>
                  <div style={{fontSize:13,color:C.text,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{a.name}</div>
                  <div style={{fontSize:10,color:C.muted2,marginTop:1}}>
                    {a.projectName||a.projectId} {a.wbs?`· ${a.wbs}`:""}
                    {a.bFinish?` · BL Finish: ${fmtDate(new Date(a.bFinish))}`:""}
                  </div>
                </div>
                <div style={{fontSize:10,color:a.pctComplete>=100?C.green:C.muted2,flexShrink:0,paddingTop:2}}>{a.pctComplete||0}%</div>
              </div>
            ))}
          </div>
          {results.length===60&&<div style={{padding:"6px 14px",fontSize:10,color:C.muted,borderTop:`1px solid ${C.border}`,textAlign:"center"}}>Showing first 60 — refine your search for more specific results</div>}
        </div>
      )}
    </div>
  );
}

// ─── PRINT CHART UTILITY ──────────────────────────────────────────────────────
function printChartEl(title:string,el:HTMLElement|null,projectLabel=''){
  if(!el)return;
  const w=window.open('','_blank','width=1150,height=850');
  if(!w){alert('Pop-ups are blocked — allow pop-ups for this site to print charts.');return;}
  const now=new Date().toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});
  w.document.write(`<!DOCTYPE html><html><head>
<title>${projectLabel?projectLabel+' — ':''} ${title}</title>
<style>
*{box-sizing:border-box}
body{background:#fff;font-family:-apple-system,"Segoe UI",sans-serif;color:#111;padding:20px 28px}
/* Project headline */
.print-project{font-size:18px;font-weight:800;color:#111;margin:0 0 2px;letter-spacing:-.01em}
/* Chart / section title */
.print-title{font-size:11px;font-weight:700;color:#555;text-transform:uppercase;letter-spacing:.08em;margin:0 0 4px}
/* Print date */
.print-date{font-size:10px;color:#888;margin:0 0 14px}
.print-divider{border:none;border-top:2px solid #e2e8f0;margin:0 0 16px}
/* Kill dark orange/brown container backgrounds */
[style*="#140700"],[style*="#1e0d00"],[style*="#2a1200"],[style*="#341800"]{background:#fff!important;color:#111!important}
/* Dark warm borders → light gray */
[style*="#6b3210"]{border-color:#e2e8f0!important}
/* Recharts axis / legend text → dark */
.recharts-cartesian-axis-tick-value tspan,.recharts-text{fill:#374151!important}
.recharts-legend-item-text{color:#374151!important}
/* Grid lines → light */
.recharts-cartesian-grid-horizontal line,.recharts-cartesian-grid-vertical line{stroke:#e5e7eb!important}
/* Muted orange fills → readable gray */
[fill="#d4884a"],[fill="#e8a870"],[fill="#fff8f0"]{fill:#4b5563!important}
/* Bright green → darker for white paper */
[fill="#00e5a0"],[stroke="#00e5a0"]{fill:#059669!important;stroke:#059669!important}
/* Gold/amber yellow → darker */
[fill="#ffd966"],[stroke="#ffd966"],[fill="#ffb547"],[stroke="#ffb547"]{fill:#d97706!important;stroke:#d97706!important}
/* Hide tooltips and print buttons */
.recharts-tooltip-wrapper,.no-print{display:none!important}
@page{size:A4 landscape;margin:10mm 14mm}
@media print{body{padding:6px}}
</style>
</head><body>
${projectLabel?`<p class="print-project">${projectLabel}</p>`:''}
<p class="print-title">${title}</p>
<p class="print-date">Printed: ${now}</p>
<hr class="print-divider"/>
${el.innerHTML}
<script>setTimeout(function(){window.print();window.close();},500)</script>
</body></html>`);
  w.document.close();
}

// ─── SMALL COMPONENTS ─────────────────────────────────────────────────────────
const TT=({active,payload,label}:any)=>{if(!active||!payload?.length)return null;return<div style={{background:"#060e1c",border:`1px solid ${C.border}`,borderRadius:8,padding:"10px 14px",fontSize:12}}><div style={{color:C.muted2,marginBottom:4}}>{label}</div>{payload.map((p:any,i:number)=><div key={i} style={{color:p.color||C.text}}>{p.name}: <strong>{typeof p.value==="number"?p.value.toLocaleString():p.value}</strong></div>)}</div>;};
const KPI=({label,value,sub,color=C.text,warn,onClick,active}:any)=>(<div onClick={onClick} style={{background:C.card,border:`1px solid ${active||warn?color:C.border}`,borderRadius:12,padding:"14px 18px",display:"flex",flexDirection:"column",gap:3,cursor:onClick?"pointer":"default",boxShadow:active?`0 0 0 2px ${color}30`:"none",transform:active?"translateY(-1px)":"none",transition:"all 0.13s"}}><div style={{fontSize:10,color:C.muted,textTransform:"uppercase",letterSpacing:"0.08em"}}>{label}</div><div style={{fontSize:26,fontWeight:700,color,fontFamily:"'DM Mono',monospace",lineHeight:1.1}}>{value}</div>{sub&&<div style={{fontSize:11,color:C.muted2}}>{sub}</div>}</div>);
function CC({title,children,height=260,flex="1 1 360px",headerExtra}:any){
  const ref=useRef<HTMLDivElement>(null);
  const proj=useContext(PrintProjectContext);
  return(
    <div ref={ref} style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:"16px 18px",flex,minWidth:0}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10,gap:8,flexWrap:"wrap"}}>
        <div style={{fontSize:11,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:"0.07em"}}>{title}</div>
        <div style={{display:"flex",alignItems:"center",gap:8}}>
          {headerExtra}
          <button className="no-print" onClick={()=>printChartEl(title,ref.current,proj)} title="Print this chart"
            style={{background:"transparent",border:"none",color:C.muted2,cursor:"pointer",fontSize:14,padding:"1px 5px",borderRadius:4,lineHeight:1,opacity:0.45,transition:"opacity 0.13s"}}
            onMouseEnter={(e:any)=>e.currentTarget.style.opacity='1'}
            onMouseLeave={(e:any)=>e.currentTarget.style.opacity='0.45'}>🖨</button>
        </div>
      </div>
      <div style={{height}}>{children}</div>
    </div>
  );
}
function Sec({title,icon,children,printable=false}:any){
  const ref=useRef<HTMLDivElement>(null);
  const proj=useContext(PrintProjectContext);
  return(
    <div ref={ref} style={{marginBottom:28}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14,borderBottom:`1px solid ${C.border}`,paddingBottom:8}}>
        {icon&&<span style={{fontSize:16}}>{icon}</span>}
        <h2 style={{margin:0,fontSize:15,fontWeight:600,color:C.text}}>{title}</h2>
        {printable&&<button className="no-print" onClick={()=>printChartEl(title,ref.current,proj)} title="Print this section"
          style={{marginLeft:"auto",background:`${C.accent}12`,border:`1px solid ${C.accent}40`,color:C.accent,cursor:"pointer",fontSize:11,padding:"4px 12px",borderRadius:6,fontFamily:"inherit",fontWeight:600,display:"flex",alignItems:"center",gap:5}}>
          <span>🖨</span><span>Print Section</span>
        </button>}
      </div>
      {children}
    </div>
  );
}

// ─── RESIZABLE COLUMN HOOK ────────────────────────────────────────────────────
function useColResize(defaults:Record<string,number>){
  const [colW,setColW]=useState<Record<string,number>>(defaults);
  const dragRef=useRef<{key:string;x0:number;w0:number}|null>(null);
  const onResizeStart=useCallback((key:string,e:React.MouseEvent)=>{
    e.preventDefault();e.stopPropagation();
    dragRef.current={key,x0:e.clientX,w0:colW[key]};
    const onMove=(ev:MouseEvent)=>{if(!dragRef.current)return;const{key,x0,w0}=dragRef.current;setColW(p=>({...p,[key]:Math.max(40,w0+ev.clientX-x0)}));};
    const onUp=()=>{dragRef.current=null;document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);};
    document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
  },[colW]);
  const reset=useCallback(()=>setColW(defaults),[]);
  return{colW,onResizeStart,reset};
}

// ─── COLUMN ORDER HOOK + P6-STYLE PICKER DIALOG ──────────────────────────────
function useColOrder(allCols:[string,string][],defaultKeys?:string[]){
  const allKeys=useMemo(()=>allCols.map(([k])=>k),[allCols]);
  const initialKeys=useMemo(()=>defaultKeys&&defaultKeys.length?defaultKeys.filter(k=>allKeys.includes(k)):allKeys,[]);
  const [sel,setSel]=useState<string[]>(()=>initialKeys);
  const visible=useMemo(()=>sel.map(k=>allCols.find(([c])=>c===k)).filter((x):x is[string,string]=>!!x),[sel,allCols]);
  const hidden=useMemo(()=>new Set(allKeys.filter(k=>!sel.includes(k))),[allKeys,sel]);
  const available=useMemo(()=>allCols.filter(([k])=>!sel.includes(k)),[allCols,sel]);
  const add=useCallback((keys:string[])=>setSel(p=>[...p,...keys.filter(k=>!p.includes(k))]),[]);
  const remove=useCallback((keys:string[])=>setSel(p=>p.filter(k=>!keys.includes(k))),[]);
  const moveUp=useCallback((key:string)=>setSel(p=>{const i=p.indexOf(key);if(i<=0)return p;const n=[...p];[n[i-1],n[i]]=[n[i],n[i-1]];return n;}),[]);
  const moveDown=useCallback((key:string)=>setSel(p=>{const i=p.indexOf(key);if(i<0||i>=p.length-1)return p;const n=[...p];[n[i+1],n[i]]=[n[i],n[i+1]];return n;}),[]);
  // Drag-and-drop: moves fromKey to sit immediately before toKey.
  const reorder=useCallback((fromKey:string,toKey:string)=>setSel(p=>{
    if(fromKey===toKey||!p.includes(fromKey)||!p.includes(toKey))return p;
    const n=[...p];
    n.splice(n.indexOf(fromKey),1);
    n.splice(n.indexOf(toKey),0,fromKey);
    return n;
  }),[]);
  const reset=useCallback(()=>setSel(initialKeys),[initialKeys]);
  return{visible,hidden,available,add,remove,moveUp,moveDown,reorder,reset};
}
type ColOrder=ReturnType<typeof useColOrder>;

function ColPickerDialog({allCols,cols}:{allCols:[string,string][];cols:ColOrder}){
  const [open,setOpen]=useState(false);
  const [lSel,setLSel]=useState<string[]>([]);
  const [rSel,setRSel]=useState<string[]>([]);
  const {visible,available,add,remove,moveUp,moveDown,reset}=cols;
  const hiddenCount=allCols.length-visible.length;
  const triggerBtn=(
    <button type="button" onClick={()=>{setLSel([]);setRSel([]);setOpen(true)}}
      style={{background:hiddenCount>0?`${C.amber}18`:"transparent",border:`1px solid ${hiddenCount>0?C.amber:C.border}`,color:hiddenCount>0?C.amber:C.muted2,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit",whiteSpace:"nowrap"}}>
      Columns{hiddenCount>0?` (${visible.length}/${allCols.length})`:'...'}
    </button>
  );
  if(!open)return triggerBtn;
  const listSt:React.CSSProperties={width:"100%",background:C.panel,border:`1px solid ${C.border}`,color:C.text,borderRadius:7,fontSize:12,height:224,fontFamily:"inherit",padding:"2px",outline:"none"};
  const iconBtn=(c:string,disabled:boolean)=>({background:"transparent",border:`1px solid ${disabled?C.border:c}`,color:disabled?C.muted2:c,borderRadius:7,padding:"5px 10px",cursor:disabled?"default":"pointer",fontSize:13,opacity:disabled?0.4:1} as React.CSSProperties);
  return(
    <>
      {triggerBtn}
      <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.58)",zIndex:2000,display:"flex",alignItems:"center",justifyContent:"center"}} onClick={()=>setOpen(false)}>
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,minWidth:530,maxWidth:"90vw",boxShadow:"0 24px 64px rgba(0,0,0,0.75)",overflow:"hidden"}} onClick={e=>e.stopPropagation()}>
          {/* ── Title bar ── */}
          <div style={{background:C.card2,borderBottom:`1px solid ${C.border}`,padding:"10px 16px",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
            <span style={{fontWeight:700,fontSize:14,color:C.text,fontFamily:"inherit"}}>Columns</span>
            <button onClick={()=>setOpen(false)} style={{background:"transparent",border:"none",color:C.muted,cursor:"pointer",fontSize:20,lineHeight:1,padding:"0 2px",fontFamily:"inherit"}}>×</button>
          </div>
          {/* ── Two-panel body ── */}
          <div style={{display:"flex",padding:"16px 16px 12px",gap:0,alignItems:"flex-start"}}>
            {/* Left: Available */}
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontSize:10,fontWeight:700,color:C.muted,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>Available Options</div>
              <select multiple value={lSel} onChange={e=>setLSel([...e.target.selectedOptions].map(o=>o.value))} style={listSt}>
                {available.map(([k,l])=><option key={k} value={k} style={{padding:"3px 8px"}}>{l}</option>)}
              </select>
            </div>
            {/* Center: Add / Remove */}
            <div style={{display:"flex",flexDirection:"column",justifyContent:"center",gap:8,padding:"36px 10px 0"}}>
              <button onClick={()=>{add(lSel);setLSel([]);}} disabled={lSel.length===0} title="Add to selected"
                style={iconBtn(C.accent,lSel.length===0)}>▶▶</button>
              <button onClick={()=>{remove(rSel);setRSel([]);}} disabled={rSel.length===0} title="Remove from selected"
                style={iconBtn(C.red,rSel.length===0)}>◀◀</button>
            </div>
            {/* Right: Selected */}
            <div style={{flex:1,minWidth:0}}>
              <div style={{fontSize:10,fontWeight:700,color:C.muted,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>Selected Columns</div>
              <select multiple value={rSel} onChange={e=>setRSel([...e.target.selectedOptions].map(o=>o.value))} style={listSt}>
                {visible.map(([k,l])=><option key={k} value={k} style={{padding:"3px 8px"}}>{l}</option>)}
              </select>
            </div>
            {/* Right: Up / Down */}
            <div style={{display:"flex",flexDirection:"column",justifyContent:"center",gap:6,padding:"36px 0 0 10px"}}>
              <button onClick={()=>rSel.forEach(k=>moveUp(k))} disabled={rSel.length===0} title="Move up"
                style={iconBtn(C.muted2,rSel.length===0)}>↑</button>
              <button onClick={()=>rSel.forEach(k=>moveDown(k))} disabled={rSel.length===0} title="Move down"
                style={iconBtn(C.muted2,rSel.length===0)}>↓</button>
            </div>
          </div>
          {/* ── Footer ── */}
          <div style={{background:C.card2,borderTop:`1px solid ${C.border}`,padding:"10px 16px",display:"flex",justifyContent:"flex-end",gap:8}}>
            <button onClick={()=>{reset();setRSel([]);setLSel([]);}} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:7,padding:"5px 16px",cursor:"pointer",fontSize:12,fontFamily:"inherit"}}>Default</button>
            <button onClick={()=>setOpen(false)} style={{background:`${C.accent}18`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:7,padding:"5px 16px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:600}}>OK</button>
            <button onClick={()=>setOpen(false)} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:7,padding:"5px 16px",cursor:"pointer",fontSize:12,fontFamily:"inherit"}}>Cancel</button>
          </div>
        </div>
      </div>
    </>
  );
}

// Resizable <th> — drag right edge to resize; shows accent line on hover only
function RTh({label,colKey,colW,onResizeStart,style,align,onSort,sortActive,sortDir,onReorder}:{
  label:string;colKey:string;colW:Record<string,number>;
  onResizeStart:(k:string,e:React.MouseEvent)=>void;
  style?:React.CSSProperties;align?:string;
  onSort?:()=>void;sortActive?:boolean;sortDir?:"asc"|"desc";
  onReorder?:(fromKey:string,toKey:string)=>void;
}){
  const [hov,setHov]=useState(false);
  const [dragOver,setDragOver]=useState(false);
  return(
    <th onClick={onSort}
      draggable={!!onReorder}
      onDragStart={onReorder?e=>{e.dataTransfer.setData('text/plain',colKey);e.dataTransfer.effectAllowed='move';}:undefined}
      onDragOver={onReorder?e=>{e.preventDefault();e.dataTransfer.dropEffect='move';if(!dragOver)setDragOver(true);}:undefined}
      onDragLeave={onReorder?()=>setDragOver(false):undefined}
      onDrop={onReorder?e=>{e.preventDefault();setDragOver(false);const from=e.dataTransfer.getData('text/plain');if(from&&from!==colKey)onReorder(from,colKey);}:undefined}
      title={onReorder?`Drag to reorder · ${label}`:undefined}
      style={{position:"relative",width:colW[colKey],padding:"8px 11px",textAlign:(align||"left") as any,color:sortActive?C.accent:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.05em",whiteSpace:"nowrap",overflow:"hidden",userSelect:"none",cursor:onReorder?"grab":onSort?"pointer":"default",transition:"color 0.13s,background 0.13s,box-shadow 0.13s",background:dragOver?`${C.accent}1f`:undefined,boxShadow:dragOver?`inset 2px 0 0 ${C.accent}`:undefined,...style}}>
      {label}
      {onSort&&<span style={{marginLeft:4,fontSize:10,opacity:sortActive?1:0.35,color:sortActive?C.accent:C.muted2}}>{sortActive?(sortDir==="asc"?"↑":"↓"):"↕"}</span>}
      <div draggable={false} onDragStart={e=>e.preventDefault()} onMouseDown={e=>{e.stopPropagation();onResizeStart(colKey,e)}} onMouseEnter={()=>setHov(true)} onMouseLeave={()=>setHov(false)}
        style={{position:"absolute",right:0,top:"15%",bottom:"15%",width:3,cursor:"col-resize",zIndex:2,background:hov?C.accent:"transparent",borderRadius:2,transition:"background 0.15s"}}/>
    </th>
  );
}

// Relationship type styles
const REL_STYLE:Record<string,{bg:string;fg:string}>={
  FS:{bg:`${C.accent}22`,fg:C.accent},
  SS:{bg:`${C.green}22`, fg:C.green},
  FF:{bg:`${C.amber}22`, fg:C.amber},
  SF:{bg:`${C.purple}22`,fg:C.purple},
};

// Inline "+ Add predecessor/successor" search box, used by LogicPanel in edit mode.
// Defined at module scope (not nested inside LogicPanel) so its local state
// (query/relType/lag) survives LogicPanel re-renders while the user is typing.
function LogicAddLink({activity,direction,excludeIds,allActivities,addRelationship,onDone}:{
  activity:any;direction:'pred'|'succ';excludeIds:Set<string>;allActivities:any[];
  addRelationship:(predId:string,succId:string,relType:RelType,lagDays:number)=>boolean;
  onDone:()=>void;
}){
  const actId=activity.id||activity.code;
  const [query,setQuery]=useState("");
  const [relType,setRelType]=useState<RelType>('FS');
  const [lagDays,setLagDays]=useState(0);
  const [error,setError]=useState<string|null>(null);

  const results=useMemo(()=>{
    const q=query.trim().toLowerCase();
    if(q.length<2)return[];
    return allActivities.filter((x:any)=>{
      const xid=x.id||x.code;
      if(xid===actId||excludeIds.has(xid))return false;
      if(x.projectId!==activity.projectId)return false;
      return(x.code||"").toLowerCase().includes(q)||(x.name||"").toLowerCase().includes(q);
    }).slice(0,20);
  },[query,allActivities,actId,excludeIds,activity.projectId]);

  const pick=(other:any)=>{
    const otherId=other.id||other.code;
    const predId=direction==='pred'?otherId:actId;
    const succId=direction==='pred'?actId:otherId;
    const ok=addRelationship(predId,succId,relType,lagDays);
    if(!ok){setError("That link would create a circular dependency — rejected.");return;}
    onDone();
  };

  const inp:React.CSSProperties={fontSize:11,background:C.panel,color:C.text,border:`1px solid ${C.border}`,borderRadius:6,padding:"4px 8px"};

  return(
    <div style={{marginTop:6,padding:8,background:C.card2,borderRadius:8,border:`1px dashed ${C.border}`}} onClick={e=>e.stopPropagation()}>
      <div style={{display:"flex",gap:6,marginBottom:6,flexWrap:"wrap"}}>
        <input autoFocus value={query} onChange={e=>{setQuery(e.target.value);setError(null);}} placeholder="Search activity code or name…" style={{...inp,flex:"1 1 160px"}}/>
        <select value={relType} onChange={e=>setRelType(e.target.value as RelType)} style={inp}>
          {(['FS','SS','FF','SF'] as const).map(rt=><option key={rt} value={rt}>{rt}</option>)}
        </select>
        <input type="number" value={lagDays} onChange={e=>setLagDays(Number(e.target.value)||0)} style={{...inp,width:56}}/>
        <button onClick={onDone} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"4px 10px",fontSize:11,cursor:"pointer",fontFamily:"inherit"}}>Cancel</button>
      </div>
      {error&&<div style={{color:C.red,fontSize:10,marginBottom:6}}>{error}</div>}
      {results.length>0&&(
        <div style={{maxHeight:160,overflowY:"auto",border:`1px solid ${C.border}`,borderRadius:6}}>
          {results.map((x:any,i:number)=>(
            <div key={x.id||x.code||i} onClick={()=>pick(x)}
              style={{padding:"5px 8px",fontSize:11,cursor:"pointer",borderBottom:i<results.length-1?`1px solid ${C.border}`:"none",color:C.text}}
              onMouseEnter={e=>(e.currentTarget as HTMLElement).style.background=`${C.accent}12`}
              onMouseLeave={e=>(e.currentTarget as HTMLElement).style.background="transparent"}>
              <span style={{color:C.muted,fontFamily:"'DM Mono',monospace",marginRight:6}}>{x.code}</span>{x.name}
            </div>
          ))}
        </div>
      )}
      {query.trim().length>=2&&results.length===0&&<div style={{fontSize:10,color:C.muted2}}>No matching activities in this project.</div>}
    </div>
  );
}

// Logic panel rendered as a <tr> — insert after the activity row
function LogicPanel({a,actMap,colSpan,onNavigate}:{a:any;actMap:Record<string,any>;colSpan:number;onNavigate?:(actId:string)=>void}){
  const{editRelationship,deleteRelationship,addRelationship,allActivities}=useContext(LogicEditContext);
  const[editing,setEditing]=useState(false);
  const[addMode,setAddMode]=useState<'pred'|'succ'|null>(null);

  const preds:any[]=a.predecessors||[];
  const succs:any[]=a.successors||[];
  const actId=a.id||a.code;
  const hasLinks=preds.length>0||succs.length>0;

  // actMap is scoped to whatever's currently rendered in the parent table (e.g. filtered
  // by the EPC phase sidebar), so a linked activity in a different phase won't be in it
  // even though it's part of the loaded schedule. Fall back to the full loaded set (from
  // context) so predecessors/successors always resolve to a real name — only navigability
  // (canNav below) stays tied to the parent's actMap, since that's what it can scroll to.
  const fullActMap=useMemo(()=>{
    const m:Record<string,any>={};
    allActivities.forEach((x:any)=>{m[x.id||x.code]=x;});
    return m;
  },[allActivities]);

  const relBadge=(rel:any,onChange:(chg:Partial<LogicEdit>)=>void)=>{
    const st=REL_STYLE[rel.relType]||REL_STYLE.FS;
    const lag=rel.lagDays;
    if(!editing)return(
      <span style={{fontSize:10,padding:"2px 7px",borderRadius:8,background:st.bg,color:st.fg,fontWeight:700,fontFamily:"'DM Mono',monospace",marginRight:3,flexShrink:0}}>
        {rel.relType}{lag!==0?<span style={{fontWeight:400,opacity:0.85}}>{lag>0?`+${lag}d`:`${lag}d`}</span>:null}
      </span>
    );
    return(
      <span style={{display:"flex",alignItems:"center",gap:4,flexShrink:0}} onClick={e=>e.stopPropagation()}>
        <select value={rel.relType} onChange={e=>onChange({relType:e.target.value as RelType})}
          style={{fontSize:10,fontWeight:700,fontFamily:"'DM Mono',monospace",background:st.bg,color:st.fg,border:`1px solid ${st.fg}55`,borderRadius:6,padding:"2px 3px"}}>
          {(['FS','SS','FF','SF'] as const).map(rt=><option key={rt} value={rt}>{rt}</option>)}
        </select>
        <input type="number" value={lag} onChange={e=>onChange({lagDays:Number(e.target.value)||0})}
          style={{width:44,fontSize:10,fontFamily:"'DM Mono',monospace",background:C.panel,color:C.text,border:`1px solid ${C.border}`,borderRadius:6,padding:"2px 4px"}}/>
        <span style={{fontSize:9,color:C.muted2}}>d</span>
      </span>
    );
  };
  const linked=(list:any[],direction:'pred'|'succ')=>list.map(rel=>{
    const localAct=actMap[rel.actId];
    const act=localAct||fullActMap[rel.actId];
    const canNav=!!localAct&&!!onNavigate&&!editing;
    const predId=direction==='pred'?rel.actId:actId;
    const succId=direction==='pred'?actId:rel.actId;
    return(
      <div key={rel.actId} onClick={canNav?()=>onNavigate!(rel.actId):undefined}
        title={canNav?"Click to navigate to this activity":act?"In a different filter/phase — clear it to navigate":"(activity not in loaded data)"}
        style={{display:"flex",alignItems:"center",gap:7,marginBottom:4,borderRadius:6,padding:"3px 6px",cursor:canNav?"pointer":"default",transition:"background 0.12s",background:"transparent"}}
        onMouseEnter={e=>{if(canNav)(e.currentTarget as HTMLElement).style.background=`${C.accent}12`;}}
        onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.background="transparent";}}>
        {relBadge(rel,chg=>editRelationship(predId,succId,chg))}
        <span style={{color:canNav?C.accent:C.muted2,fontSize:11,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",maxWidth:editing?250:340,flex:editing?1:undefined,textDecoration:canNav?"underline":"none",textDecorationStyle:canNav?"dotted":"solid"}}>{act?.name||"(not in current data)"}</span>
        {canNav&&<span style={{fontSize:9,color:C.muted2,flexShrink:0,marginLeft:2}}>↗</span>}
        {editing&&<button onClick={e=>{e.stopPropagation();deleteRelationship(predId,succId);}} title="Delete this link" style={{background:"transparent",border:"none",color:C.red,cursor:"pointer",fontSize:13,padding:"0 4px",flexShrink:0}}>✕</button>}
      </div>
    );
  });

  return(
    <tr>
      <td colSpan={colSpan} style={{padding:"10px 14px 12px",background:C.panel,borderTop:`1px solid ${C.border}`,borderBottom:`1px solid ${C.border}`}} onClick={e=>e.stopPropagation()}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",gap:8,marginBottom:hasLinks||editing?8:4}}>
          <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>
            {a.isLogicEdited&&<span style={{fontSize:10,fontWeight:700,color:C.amber,background:`${C.amber}18`,padding:"2px 7px",borderRadius:6}}>⚡ Logic edited — schedule recalculated</span>}
            {a.cpmError&&<span style={{fontSize:10,fontWeight:700,color:C.red,background:`${C.red}18`,padding:"2px 7px",borderRadius:6}}>{a.cpmError}</span>}
          </div>
          <button onClick={()=>{setEditing(v=>!v);setAddMode(null);}}
            style={{background:editing?`${C.accent}20`:"transparent",border:`1px solid ${editing?C.accent:C.border}`,color:editing?C.accent:C.muted2,borderRadius:6,padding:"3px 10px",fontSize:10,fontWeight:600,cursor:"pointer",fontFamily:"inherit",flexShrink:0}}>
            {editing?"Done":"✎ Edit logic"}
          </button>
        </div>
        {!hasLinks&&!editing&&(
          <div style={{color:C.muted,fontSize:11}}>No logic links recorded for this activity.</div>
        )}
        <div style={{display:"flex",flexWrap:"wrap",gap:18}}>
          {(preds.length>0||editing)&&(
            <div style={{minWidth:280,flex:"1 1 280px"}}>
              <div style={{fontSize:10,fontWeight:700,color:C.muted,letterSpacing:"0.08em",marginBottom:6,textTransform:"uppercase",display:"flex",alignItems:"center",gap:8}}>
                <span>← Predecessors ({preds.length})</span>
                {editing&&<button onClick={()=>setAddMode(m=>m==='pred'?null:'pred')} style={{fontSize:9,fontWeight:700,color:C.accent,background:"transparent",border:`1px solid ${C.accent}55`,borderRadius:5,padding:"1px 6px",cursor:"pointer",textTransform:"none",letterSpacing:0,fontFamily:"inherit"}}>+ Add</button>}
              </div>
              {linked(preds,'pred')}
              {addMode==='pred'&&<LogicAddLink activity={a} direction="pred" excludeIds={new Set(preds.map(r=>r.actId))} allActivities={allActivities} addRelationship={addRelationship} onDone={()=>setAddMode(null)}/>}
            </div>
          )}
          {(succs.length>0||editing)&&(
            <div style={{minWidth:280,flex:"1 1 280px"}}>
              <div style={{fontSize:10,fontWeight:700,color:C.muted,letterSpacing:"0.08em",marginBottom:6,textTransform:"uppercase",display:"flex",alignItems:"center",gap:8}}>
                <span>Successors ({succs.length}) →</span>
                {editing&&<button onClick={()=>setAddMode(m=>m==='succ'?null:'succ')} style={{fontSize:9,fontWeight:700,color:C.accent,background:"transparent",border:`1px solid ${C.accent}55`,borderRadius:5,padding:"1px 6px",cursor:"pointer",textTransform:"none",letterSpacing:0,fontFamily:"inherit"}}>+ Add</button>}
              </div>
              {linked(succs,'succ')}
              {addMode==='succ'&&<LogicAddLink activity={a} direction="succ" excludeIds={new Set(succs.map(r=>r.actId))} allActivities={allActivities} addRelationship={addRelationship} onDone={()=>setAddMode(null)}/>}
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

// ─── DEMO DATA (client-side, metrics fetched from Django) ─────────────────────
function generateDemo(){
  const defs=[
    {id:"PROJ-001",name:"City Hospital Expansion",offset:-6,count:120,h:0.75},
    {id:"PROJ-002",name:"Highway Interchange",offset:-3,count:95,h:0.55},
    {id:"PROJ-003",name:"Corporate HQ Tower",offset:-9,count:80,h:0.9},
    {id:"PROJ-004",name:"Water Treatment Plant",offset:0,count:110,h:0.4},
    {id:"PROJ-005",name:"Airport Terminal",offset:-2,count:90,h:0.65},
  ];
  const wbs=["Civil Works","MEP","Structural","Architectural","Commissioning","Procurement","Testing","Closeout"];
  return defs.map(pd=>{
    const today=new Date(),acts:any[]=[];
    for(let i=0;i<pd.count;i++){
      const s=new Date(today.getFullYear(),today.getMonth()+pd.offset,1);s.setDate(s.getDate()+Math.floor(Math.random()*240));
      const dur=Math.floor(Math.random()*28)+1,bf=new Date(s);bf.setDate(bf.getDate()+dur);
      const tf=Math.floor(Math.random()*25)-(pd.h<0.6?8:2),dp=(today.getTime()-bf.getTime())/86400000;
      const pc=dp>21?100:dp>0?Math.floor(Math.random()*60+20):s<today?Math.floor(Math.random()*80*pd.h):0;
      acts.push({id:`${pd.id}-${i}`,code:`${pd.id.split("-")[1]}${String(i).padStart(4,"0")}`,name:`${wbs[i%8]} – Activity ${i+1}`,projectId:pd.id,projectName:pd.name,sourceFile:`${pd.name}.xer`,wbs:wbs[i%8],type:"TT_Task",status:pc>=100?"TK_Complete":s<today?"TK_Active":"TK_NotStart",start:s<today?s:null,finish:pc>=100?bf:null,bStart:s,bFinish:bf,dur,remainDur:pc>=100?0:dur*(1-pc/100),totalFloat:tf,pctComplete:pc,cost:Math.floor(Math.random()*80000+5000),isCritical:tf<=0,isMilestone:i%25===0});
    }
    for(let m=0;m<4;m++){const d=new Date(today.getFullYear(),today.getMonth()+pd.offset+m*2+1,15);acts.push({id:`${pd.id}-MS${m}`,code:`MS${m}`,name:`Key Milestone ${m+1}`,projectId:pd.id,projectName:pd.name,sourceFile:`${pd.name}.xer`,wbs:"Milestones",type:"TT_Mile",status:d<today?"TK_Complete":"TK_NotStart",start:d,finish:d<today?d:null,bStart:d,bFinish:d,dur:0,remainDur:0,totalFloat:0,pctComplete:d<today?100:0,cost:0,isCritical:true,isMilestone:true});}
    return{id:pd.id,name:pd.name,file:`${pd.name}.xer`,activities:acts,source:"demo",actCount:acts.length};
  });
}

// ─── HEADER ───────────────────────────────────────────────────────────────────
function Header({files,selectedIds,onSelectionChange,onSelectFiles,onReset,view,setView,allActivities,onGoToActivity,dataDate,onDataDateChange,onResetDataDateToToday,onDeleteProject}:any){
  const addRef=useRef<HTMLInputElement>(null);
  const todayStr=new Date().toISOString().slice(0,10);
  const isForward=dataDate>todayStr;
  const isPast=dataDate<todayStr;
  const nav=[
    {id:"dashboard", label:"Dashboard",    icon:"📌", highlight:true},
    {id:"fieldDashboard", label:"Field Dashboard", icon:"🏗️", highlight:true},
    {id:"portfolio", label:"Portfolio",    icon:"🏢"},
    {id:"scurve",    label:"S-Curves",     icon:"📈"},
    {id:"gantt",     label:"Gantt",        icon:"📅", highlight:true},
    {id:"critical",  label:"Critical Path",icon:"🔴"},
    {id:"variance",  label:"Variance",     icon:"📐",highlight:true},
    {id:"evm",       label:"EVM & MH",     icon:"💰",highlight:true},
    {id:"histogram", label:"Histograms",   icon:"📊"},
    {id:"diff",      label:"Schedule Diff",icon:"🔀"},
    {id:"oos",       label:"Out of Sequence",icon:"⚠️", highlight:true},
    {id:"quality",   label:"Open Ends Check",  icon:"🔗"},
    {id:"narrative", label:"Narrative",    icon:"📝"},
    {id:"tia",       label:"Time Impact",  icon:"⏱️", highlight:true},
    {id:"powerbi",   label:"Cross-Filter Dashboard",     icon:"📊", highlight:true},
    {id:"resources", label:"Resources",    icon:"👷", highlight:true},
    {id:"status",    label:"Status",       icon:"🎯", highlight:true},
    {id:"updateAnalysis", label:"Update Analysis", icon:"🔄", highlight:true},
    {id:"activityAnalysis", label:"Activity Analysis", icon:"📋", highlight:true},
    {id:"floatAnalysis", label:"Float Analysis", icon:"🧭", highlight:true},
    {id:"riskIntel",   label:"Risk & Milestones", icon:"🌡️", highlight:true},
    {id:"projectControls", label:"Project Controls", icon:"🧮", highlight:true},
    {id:"baselineProgress", label:"Baseline & Progress", icon:"📐", highlight:true},
    {id:"intelligence", label:"Intelligence",    icon:"🧠", highlight:true},
    {id:"reports",      label:"Reports",          icon:"🖨️", highlight:true},
  ];
  return(
    <div style={{background:C.panel,borderBottom:`1px solid ${C.border}`,position:"sticky",top:0,zIndex:100}}>
      <div style={{display:"flex",alignItems:"center",gap:14,padding:"10px 22px",borderBottom:`1px solid ${C.border}`,flexWrap:"wrap"}}>
        <div onClick={onReset} title="Go to home" style={{display:"flex",alignItems:"center",gap:10,marginRight:8,cursor:"pointer"}}>
          <div style={{width:44,height:44,overflow:"hidden",flexShrink:0,borderRadius:6}}>
            <img src="/tambiq-logo.png" alt="" style={{width:130,height:130,marginLeft:"-43px",marginTop:"-2px"}}/>
          </div>
          <span style={{fontSize:16,fontWeight:800,color:C.text,letterSpacing:"-0.02em",lineHeight:1}}>
            Schedule<span style={{color:C.gold}}>IQ</span>
          </span>
        </div>
        <ProjectSelector files={files} selectedIds={selectedIds} onChange={onSelectionChange} onDelete={onDeleteProject}/>
        <IntelligenceSync files={files}/>

        {/* ── Global Search ── */}
        {allActivities?.length>0&&<GlobalSearch allActivities={allActivities} onGoToActivity={onGoToActivity}/>}

        {/* ── Data Date ── */}
        <div style={{display:"flex",alignItems:"center",gap:6,background:C.card,border:`1px solid ${isForward?C.gold:isPast?C.purple:C.border}`,borderRadius:9,padding:"6px 12px",flexShrink:0}}>
          <div>
            <div style={{fontSize:9,color:isForward?C.gold:isPast?C.purple:C.muted,textTransform:"uppercase",letterSpacing:"0.08em",marginBottom:1}}>
              {isForward?"📅 Forward View":isPast?"📅 Historical":"📅 Data Date"}
            </div>
            <input
              type="date"
              value={dataDate}
              onChange={e=>onDataDateChange(e.target.value)}
              style={{background:"transparent",border:"none",outline:"none",color:isForward?C.gold:isPast?C.purple:C.text,fontSize:12,fontFamily:"inherit",cursor:"pointer",fontWeight:600}}
            />
          </div>
          {dataDate!==todayStr&&(
            <button onClick={()=>onResetDataDateToToday(todayStr)}
              title="Reset to today (session projection only — does not change the schedule's stored Data Date)"
              style={{background:"transparent",border:"none",color:C.muted,cursor:"pointer",fontSize:12,padding:0,lineHeight:1}}>✕</button>
          )}
        </div>

        <input ref={addRef} type="file" accept=".xer,.xlsx,.xls,.csv,.xml,.pdf,.mpp" multiple onChange={e=>{const fl=Array.from(e.target.files||[]);if(fl.length)onSelectFiles(fl);(e.target as HTMLInputElement).value="";}} style={{display:"none"}}/>
        <button onClick={()=>addRef.current?.click()} style={{background:"rgba(0,200,240,0.08)",border:`1px solid ${C.accent}`,color:C.accent,borderRadius:8,padding:"7px 14px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:600,whiteSpace:"nowrap"}}>+ Add Files</button>
        <button onClick={onReset} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:8,padding:"7px 14px",cursor:"pointer",fontSize:12,fontFamily:"inherit",whiteSpace:"nowrap",marginLeft:"auto"}}>↩ New Session</button>
      </div>
      <div style={{display:"flex",padding:"0 14px",gap:0,flexWrap:"wrap"}}>
        {nav.map((v:any)=>(
          <button key={v.id} onClick={()=>setView(v.id)} style={{background:v.highlight&&view!==v.id?"rgba(212,168,67,0.07)":"transparent",border:"none",borderBottom:`2px solid ${view===v.id?C.accent:"transparent"}`,color:view===v.id?C.accent:v.highlight?C.gold:C.muted2,padding:"8px 11px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:view===v.id?800:700,display:"flex",alignItems:"center",gap:4,whiteSpace:"nowrap",transition:"all 0.13s"}}>
            <span style={{fontSize:12}}>{v.icon}</span>{v.label}{v.highlight&&view!==v.id&&<span style={{fontSize:8,background:C.gold,color:"#000",borderRadius:4,padding:"1px 4px",fontWeight:700,marginLeft:2}}>NEW</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

// ─── VIEWS ────────────────────────────────────────────────────────────────────
function PortfolioView({M,files,selectedIds,allActivities,onGoToFilter,onGoToProject}:any){
  // Dashboard Consolidation Phase 1: the former standalone "Comparison" nav
  // item was the same M.projects rollup as this view, just styled as a
  // detailed table — absorbed here as a toggle instead of a second nav
  // item, reusing ComparisonView as-is (zero duplicated calculation).
  const [tableView,setTableView]=useState(false);
  const acts=allActivities||[];
  const isComplete =(a:any)=>(a.pctComplete||0)>=100||a.totalFloat==null||a.status==="TK_Complete";
  const compCount  =useMemo(()=>acts.filter(isComplete).length,[acts]);
  const inProgCount=useMemo(()=>acts.filter((a:any)=>a.start&&a.totalFloat!=null&&!isComplete(a)).length,[acts]);

  // Baseline detection — portfolio-wide and per-project
  const blInfo=useMemo(()=>detectBaseline(acts),[acts]);
  const blByProject=useMemo(()=>{
    const map:Record<string,ReturnType<typeof detectBaseline>>={};
    M.projects.forEach((p:any)=>{
      const pa=acts.filter((a:any)=>a.projectId===p.id);
      map[p.id]=detectBaseline(pa);
    });
    return map;
  },[acts,M.projects]);

  const kpis=[
    {label:"Total Activities",   value:M.total.toLocaleString(), color:C.text,                          filt:"ALL"},
    {label:"Portfolio Complete",  value:`${M.schedPct.toFixed(1)}%`, color:C.green,                    filt:"COMPLETE"},
    {label:"Critical Activities", value:M.critical, color:C.red, warn:true,                            filt:"CRITICAL"},
    {label:"Overdue",             value:M.overdue, color:M.overdue>0?C.amber:C.green, warn:M.overdue>0,filt:"OVERDUE"},
    {label:"Not Started",         value:M.notStarted, color:C.muted2,                                  filt:"NOTSTART"},
    {label:"In Progress",         value:inProgCount,  color:C.accent,                                  filt:"INPROG"},
    {label:"Activities Completed", value:compCount,    color:C.green,                                   filt:"COMPLETE"},
    {label:"Milestones",          value:M.milestones, color:C.purple,                                  filt:"MILESTON"},
  ];

  return(<>
    <div style={{display:"flex",justifyContent:"flex-end",marginBottom:8}}>
      <button onClick={()=>setTableView(v=>!v)}
        style={{background:tableView?C.accent:"transparent",color:tableView?"#fff":C.muted,border:`1px solid ${C.border}`,borderRadius:7,padding:"5px 12px",fontSize:11,fontWeight:700,cursor:"pointer"}}>
        {tableView?"📊 Card View":"📋 Table View"}
      </button>
    </div>
    <Sec title="Portfolio KPIs" icon="🏢">
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(140px,1fr))",gap:9,marginBottom:20}}>
        {kpis.map(k=>(
          <KPI key={k.label} label={k.label} value={k.value} color={k.color} warn={k.warn}
            onClick={onGoToFilter?()=>onGoToFilter(k.filt):undefined}
          />
        ))}
      </div>
      <div style={{display:"flex",flexWrap:"wrap",gap:10}}>
        {M.projects.map((p:any,i:number)=>{
          const fi=files.findIndex((f:any)=>f.id===p.id||f.activities.some((a:any)=>a.projectId===p.id));
          const color=PROJ_COLORS[fi>=0?fi%PROJ_COLORS.length:i%PROJ_COLORS.length];
          const bl=blByProject[p.id]||{status:"No Baseline",color:C.red,coverage:0,hasBaseline:false,isReal:false};
          const targetFileId=fi>=0?files[fi].id:p.id;
          return(<div key={p.id} onClick={onGoToProject?()=>onGoToProject(targetFileId):undefined}
            title={onGoToProject?`View ${p.name}`:undefined}
            style={{flex:"1 1 190px",background:C.card2,border:`1px solid ${C.border}`,borderLeft:`3px solid ${color}`,borderRadius:12,padding:"13px 15px",cursor:onGoToProject?"pointer":"default",transition:"transform 0.12s, box-shadow 0.12s"}}
            onMouseEnter={onGoToProject?(e:any)=>{e.currentTarget.style.transform="translateY(-2px)";e.currentTarget.style.boxShadow="0 6px 16px rgba(0,0,0,0.18)";}:undefined}
            onMouseLeave={onGoToProject?(e:any)=>{e.currentTarget.style.transform="none";e.currentTarget.style.boxShadow="none";}:undefined}
          >
            <div style={{display:"flex",alignItems:"center",gap:6,marginBottom:4}}>
              <div style={{flex:1,fontSize:12,fontWeight:600,color:C.text,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{p.name}</div>
              <span style={{fontSize:10,padding:"2px 6px",borderRadius:20,background:`${p.health.color}20`,color:p.health.color,fontWeight:600,whiteSpace:"nowrap"}}>{p.health.label}</span>
            </div>
            {/* Baseline status badge */}
            <div style={{marginBottom:7}}>
              <span title={`${bl.coverage}% of activities have baseline dates${bl.isReal?"":" (dates equal current plan — no divergence detected)"}`}
                style={{fontSize:9,padding:"2px 7px",borderRadius:10,background:`${bl.color}18`,color:bl.color,fontWeight:700,letterSpacing:"0.04em",border:`1px solid ${bl.color}30`}}>
                {bl.hasBaseline?"✓":"⚠"} {bl.status} {bl.coverage>0?`(${bl.coverage}%)`:""}</span>
            </div>
            <div style={{display:"flex",gap:10,marginBottom:7}}>
              {[{l:"Done",v:`${p.pctComplete}%`,c:C.green},{l:"Critical",v:p.critical,c:C.red},{l:"Overdue",v:p.overdue,c:p.overdue>0?C.amber:C.muted2}].map((k,j)=>(<div key={j} style={{flex:1}}><div style={{fontSize:9,color:C.muted,marginBottom:1}}>{k.l}</div><div style={{fontSize:16,fontWeight:700,color:k.c,fontFamily:"'DM Mono',monospace"}}>{k.v}</div></div>))}
            </div>
            <div style={{background:C.border,borderRadius:4,height:4}}><div style={{width:`${p.pctComplete}%`,background:p.pctComplete>=80?C.green:p.pctComplete>=50?C.amber:C.red,borderRadius:4,height:4}}/></div>
            <div style={{fontSize:9,color:C.muted,marginTop:3}}>{p.total.toLocaleString()} activities</div>
          </div>);
        })}
      </div>
    </Sec>
    <Sec title="Schedule Health Indices" icon="📊">
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(185px,1fr))",gap:9,marginBottom:18}}>
        {[
          {label:"Schedule % Complete (SPC)", value:`${M.schedPct.toFixed(1)}%`,  sub:"Earned / planned duration",        color:M.schedPct>=80?C.green:M.schedPct>=50?C.amber:C.red,   filt:"COMPLETE"},
          {label:"Baseline Execution Index",  value:M.BEI.toFixed(2),             sub:"Target ≥ 0.95",                   color:M.BEI>=0.95?C.green:M.BEI>=0.8?C.amber:C.red,         filt:"OVERDUE"},
          {label:"Critical Path %",           value:`${pct(M.critical,M.total)}%`,sub:"Target < 10%",                    color:pct(M.critical,M.total)<10?C.green:pct(M.critical,M.total)<20?C.amber:C.red, filt:"CRITICAL"},
          {label:"Near-Critical Activities",  value:M.nearCritical,               sub:"Float 1–5 days",                  color:C.amber,                                               filt:"NEARCRIT"},
          {label:"Negative Float Count",      value:M.fb.negative,                sub:"Behind critical path",            color:M.fb.negative===0?C.green:C.red, warn:M.fb.negative>0, filt:"NEGFLOAT"},
          {label:"Overdue Rate",              value:`${pct(M.overdue,M.total)}%`, sub:"Past baseline & incomplete",      color:M.overdue===0?C.green:C.red,     warn:M.overdue>0,     filt:"OVERDUE"},
          {label:"Baseline Status",           value:blInfo.status,                sub:`${blInfo.coverage}% activities baselined`, color:blInfo.color, warn:!blInfo.hasBaseline, filt:"ALL"},
        ].map(k=>(
          <KPI key={k.label} label={k.label} value={k.value} sub={k.sub} color={k.color} warn={k.warn}
            onClick={onGoToFilter?()=>onGoToFilter(k.filt):undefined}
          />
        ))}
      </div>
    </Sec>
    <Sec title="Status & Trends" icon="🎯">
      <div style={{display:"flex",flexWrap:"wrap",gap:12}}>
        <CC title="Activity Status" flex="1 1 200px" height={190}><ResponsiveContainer><PieChart><Pie data={M.statusPie} cx="50%" cy="50%" outerRadius={70} innerRadius={32} dataKey="value" labelLine={false} label={({percent}:any)=>`${(percent*100).toFixed(0)}%`} fontSize={11}>{M.statusPie.map((e:any,i:number)=><Cell key={i} fill={e.fill}/>)}</Pie><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/></PieChart></ResponsiveContainer></CC>
        <CC title="Criticality" flex="1 1 200px" height={190}><ResponsiveContainer><PieChart><Pie data={M.criticalPie} cx="50%" cy="50%" outerRadius={70} innerRadius={32} dataKey="value" labelLine={false} label={({percent}:any)=>`${(percent*100).toFixed(0)}%`} fontSize={11}>{M.criticalPie.map((e:any,i:number)=><Cell key={i} fill={e.fill}/>)}</Pie><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/></PieChart></ResponsiveContainer></CC>
        <CC title="Monthly Activity Trend" flex="2 1 360px" height={190}><ResponsiveContainer><BarChart data={M.monthlyTrend.slice(-18)} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="month" tick={{fill:C.muted,fontSize:9}} interval={2}/><YAxis tick={{fill:C.muted,fontSize:9}}/><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/><Bar dataKey="complete" name="Complete" fill={C.green} stackId="a"/><Bar dataKey="inProgress" name="In Progress" fill={C.accent} stackId="a"/><Bar dataKey="notStarted" name="Not Started" fill={C.muted} stackId="a"/></BarChart></ResponsiveContainer></CC>
      </div>
    </Sec>
    {tableView && <ComparisonView M={M} files={files}/>}
  </>);
}

function SCurveView({M,allActivities,files}:any){
  return(<>
    <Sec title="Portfolio S-Curve — Planned vs Actual" icon="📈" printable={true}>
      <div style={{display:"flex",flexWrap:"wrap",gap:12}}>
        {/* Schedule S-curve — % complete */}
        <CC title="Cumulative % Complete — Schedule" height={340} flex="1 1 440px">
          <ResponsiveContainer><ComposedChart data={M.sCurve} margin={{left:-10}}>
            <defs>
              <linearGradient id="gP" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={PLAN_COLOR} stopOpacity={0.18}/><stop offset="95%" stopColor={PLAN_COLOR} stopOpacity={0}/></linearGradient>
              <linearGradient id="gA" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.green} stopOpacity={0.15}/><stop offset="95%" stopColor={C.green} stopOpacity={0}/></linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis dataKey="month" tick={{fill:C.muted,fontSize:11}} interval={Math.max(0,Math.floor(M.sCurve.length/14))}/>
            <YAxis tick={{fill:C.muted,fontSize:11}} unit="%" domain={[0,100]}/>
            <Tooltip content={<TT/>}/><Legend iconSize={10} wrapperStyle={{fontSize:12}}/>
            <Area type="monotone" dataKey="pctPlanned" name="Planned %" stroke={PLAN_COLOR} fill="url(#gP)" strokeWidth={2.5} dot={false}/>
            <Area type="monotone" dataKey="pctActual"  name="Actual %"  stroke={C.green}   fill="url(#gA)" strokeWidth={2.5} dot={false}/>
          </ComposedChart></ResponsiveContainer>
        </CC>
        {/* Man-hours S-curve — cumulative MH */}
        <CC title={M.isHrLoaded?"Cumulative Man-Hours — Budgeted vs Actual vs Earned":"Man-Hours S-Curve (no resource data)"} height={340} flex="1 1 440px">
          {(M.manHours?.curve||[]).length>1
            ? <ResponsiveContainer><ComposedChart data={M.manHours.curve} margin={{left:-10}}>
                <defs>
                  <linearGradient id="gMHbgt2" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={BGT_COLOR} stopOpacity={0.18}/><stop offset="95%" stopColor={BGT_COLOR} stopOpacity={0}/></linearGradient>
                  <linearGradient id="gMHern2" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.green}   stopOpacity={0.15}/><stop offset="95%" stopColor={C.green}   stopOpacity={0}/></linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
                <XAxis dataKey="month" tick={{fill:C.muted,fontSize:11}} interval={Math.max(0,Math.floor((M.manHours.curve||[]).length/14))}/>
                <YAxis tick={{fill:C.muted,fontSize:11}} tickFormatter={(v:number)=>v>=1000?`${(v/1000).toFixed(0)}K`:String(v)}/>
                <Tooltip content={<TT/>}/><Legend iconSize={10} wrapperStyle={{fontSize:12}}/>
                <Area type="monotone" dataKey="budgeted" name="Budgeted MH" stroke={BGT_COLOR} fill="url(#gMHbgt2)" strokeWidth={2.5} dot={false}/>
                <Area type="monotone" dataKey="earned"   name="Earned MH"   stroke={C.green}  fill="url(#gMHern2)" strokeWidth={2.5} dot={false}/>
                {M.isHrLoaded&&<Area type="monotone" dataKey="actual" name="Actual MH" stroke={C.amber} fill="none" strokeWidth={2} dot={false} strokeDasharray="5 3"/>}
              </ComposedChart></ResponsiveContainer>
            : <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%",color:C.muted2,fontSize:12,flexDirection:"column",gap:8}}>
                <span style={{fontSize:24}}>⚙</span>
                <span>No resource assignments detected</span>
                <span style={{fontSize:11,color:C.muted}}>Upload a resource-loaded XER to see MH curve</span>
              </div>}
        </CC>
      </div>
    </Sec>
    <Sec title="Individual Project S-Curves" icon="📉">
      <div style={{padding:"10px 16px",borderRadius:10,background:`${C.accent}10`,border:`1px solid ${C.accent}30`,display:"flex",alignItems:"center",gap:10}}>
        <span style={{fontSize:16}}>📈</span>
        <div style={{fontSize:12,color:C.muted,lineHeight:1.5}}>
          Per-project S-curves moved to <strong style={{color:C.text}}>Baseline & Progress</strong> — same backend scurve engine, with a configurable metric, milestone markers and a Data Date reference line this page didn't have. The portfolio rollup above stays here.
        </div>
      </div>
    </Sec>
  </>);
}

function HistogramView({M,allActivities}:any){
  const mh=M.manHours||{};
  const fmtH=(v:number)=>v>=1000?`${(v/1000).toFixed(1)}K h`:`${Math.round(v)} h`;
  const cpiCol=(v:number)=>v>=1?C.green:v>=0.9?C.amber:C.red;

  // ── Float trend curve — average float across the schedule timeline, bucketed
  // by baseline finish date at the user's chosen granularity ──────────────────
  const [floatTrendGran,setFloatTrendGran]=useState<'week'|'biweek'|'month'>('week');
  const floatTrendData=useMemo(()=>{
    const withFloat=(allActivities||[]).filter((a:any)=>a.bFinish instanceof Date&&a.totalFloat!=null&&!a.isMilestone);
    if(!withFloat.length)return[];
    const minT=Math.min(...withFloat.map((a:any)=>(a.bFinish as Date).getTime()));
    const bucketMs=floatTrendGran==='biweek'?14*86400000:floatTrendGran==='week'?7*86400000:null;
    const buckets=new Map<string,{sum:number;count:number;crit:number;sortKey:number;label:string}>();
    withFloat.forEach((a:any)=>{
      const d=a.bFinish as Date;
      let key:string,label:string,sortKey:number;
      if(bucketMs===null){
        sortKey=d.getFullYear()*12+d.getMonth();
        key=String(sortKey);
        label=d.toLocaleDateString('en-US',{month:'short',year:'2-digit'});
      }else{
        sortKey=Math.floor((d.getTime()-minT)/bucketMs);
        key=String(sortKey);
        label=new Date(minT+sortKey*bucketMs).toLocaleDateString('en-US',{month:'short',day:'numeric'});
      }
      if(!buckets.has(key))buckets.set(key,{sum:0,count:0,crit:0,sortKey,label});
      const b=buckets.get(key)!;
      b.sum+=a.totalFloat;b.count++;if(a.isCritical)b.crit++;
    });
    return[...buckets.values()].sort((x,y)=>x.sortKey-y.sortKey).map(b=>({
      period:b.label,
      avgFloat:Math.round((b.sum/b.count)*10)/10,
      criticalPct:Math.round((b.crit/b.count)*100),
      count:b.count,
    }));
  },[allActivities,floatTrendGran]);
  return(<>
    {/* ── Schedule Distribution ─────────────────────────────────────────── */}
    <Sec title="Schedule Distribution" icon="📊" printable={true}>
      <div style={{display:"flex",flexWrap:"wrap",gap:12}}>
        <CC title="Total Float Distribution" flex="1 1 300px" height={240}><ResponsiveContainer><BarChart data={M.floatHist} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="range" tick={{fill:C.muted,fontSize:12}}/><YAxis tick={{fill:C.muted,fontSize:11}}/><Tooltip content={<TT/>}/><Bar dataKey="count" name="Activities" radius={[4,4,0,0]}>{M.floatHist.map((e:any,i:number)=><Cell key={i} fill={e.fill}/>)}</Bar></BarChart></ResponsiveContainer></CC>
        <CC title="Duration Distribution" flex="1 1 300px" height={240}><ResponsiveContainer><BarChart data={M.durHist} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="range" tick={{fill:C.muted,fontSize:11}}/><YAxis tick={{fill:C.muted,fontSize:11}}/><Tooltip content={<TT/>}/><Bar dataKey="count" name="Activities" fill={C.accent} radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></CC>
        <CC title="% Complete Distribution" flex="1 1 300px" height={240}><ResponsiveContainer><BarChart data={M.pctHist} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="range" tick={{fill:C.muted,fontSize:11}}/><YAxis tick={{fill:C.muted,fontSize:11}}/><Tooltip content={<TT/>}/><Bar dataKey="count" name="Activities" fill={C.green} radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></CC>
        <CC title="Activities by WBS" flex="1 1 300px" height={240}><ResponsiveContainer><BarChart layout="vertical" data={M.wbsDist} margin={{left:8}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis type="number" tick={{fill:C.muted,fontSize:11}}/><YAxis type="category" dataKey="name" tick={{fill:C.muted,fontSize:10}} width={100}/><Tooltip content={<TT/>}/><Bar dataKey="count" name="Activities" fill={C.purple} radius={[0,4,4,0]}/></BarChart></ResponsiveContainer></CC>
        <CC title="Float Risk by Project" flex="2 1 400px" height={240}><ResponsiveContainer><BarChart data={M.projects.map((p:any)=>({name:p.name.split(" ").slice(0,2).join(" "),Critical:p.critical,NearCritical:p.nearCritical,NegFloat:p.negFloat}))} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="name" tick={{fill:C.muted,fontSize:10}}/><YAxis tick={{fill:C.muted,fontSize:11}}/><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/><Bar dataKey="NegFloat" name="Neg Float" fill={C.red} radius={[4,4,0,0]}/><Bar dataKey="Critical" name="Critical" fill={C.amber} radius={[4,4,0,0]}/><Bar dataKey="NearCritical" name="Near-Crit" fill={C.orange} radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></CC>
        <CC title="Float Trend Curve" flex="3 1 600px" height={260}
          headerExtra={
            <div style={{display:"flex",gap:4}}>
              {(['week','biweek','month'] as const).map(g=>(
                <button key={g} className="no-print" onClick={()=>setFloatTrendGran(g)}
                  style={{background:floatTrendGran===g?`${C.accent}20`:"transparent",border:`1px solid ${floatTrendGran===g?C.accent:C.border}`,color:floatTrendGran===g?C.accent:C.muted2,borderRadius:6,padding:"3px 9px",cursor:"pointer",fontSize:10,fontFamily:"inherit",fontWeight:600}}>
                  {g==='week'?'Weekly':g==='biweek'?'Bi-Weekly':'Monthly'}
                </button>
              ))}
            </div>
          }>
          {floatTrendData.length?(
            <ResponsiveContainer><ComposedChart data={floatTrendData} margin={{left:-10}}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
              <XAxis dataKey="period" tick={{fill:C.muted,fontSize:10}}/>
              <YAxis tick={{fill:C.muted,fontSize:11}} label={{value:'Avg Float (d)',angle:-90,position:'insideLeft',fill:C.muted,fontSize:10}}/>
              <Tooltip content={<TT/>}/>
              <Legend iconSize={9} wrapperStyle={{fontSize:10}}/>
              <ReferenceLine y={0} stroke={C.red} strokeDasharray="4 4"/>
              <Line type="monotone" dataKey="avgFloat" name="Avg Total Float (d)" stroke={C.accent} strokeWidth={2} dot={{r:2}}/>
            </ComposedChart></ResponsiveContainer>
          ):(
            <div style={{display:"flex",alignItems:"center",justifyContent:"center",height:"100%",color:C.muted2,fontSize:12}}>No baseline finish dates available to plot a float trend.</div>
          )}
        </CC>
      </div>
    </Sec>

    {/* ── Manpower / Man-Hours Histograms ────────────────────────────────── */}
    {(()=>{
      // The KPI row below reads M.authoritativeProductivity (cost_engine.py's
      // compute_productivity — the SAME engine Project Controls' Labor
      // Productivity tab uses) instead of the legacy compute_metrics() mh.*
      // fields it used to. That legacy path summed budgetedHours/actualHours
      // etc. across ALL activities unconditionally and defaulted CPI to 1.0
      // when there was no actual data — which reads as "on budget, zero
      // remaining" for a schedule that simply isn't resource-loaded, a real,
      // confirmed defect (see the pre-commit Manpower/MH reconciliation).
      // The curves/histograms/per-project table further below are left on
      // the legacy fields (no authoritative per-project/time-series
      // breakdown is plumbed through M yet) — they're not misleading on
      // their own, since they already render nothing when there's no data.
      const prod=M.authoritativeProductivity?.overall;
      const resourceLoaded=!!prod?.available;
      const pctComplete=resourceLoaded&&prod.budgetedHours>0?Math.round((prod.earnedHours/prod.budgetedHours)*1000)/10:null;
      return(
    <Sec title={resourceLoaded?"Manpower Tracking — Resource Loaded":"Manpower Tracking (not resource loaded)"} icon="👷" printable={true}>
      {!resourceLoaded&&<div style={{background:'rgba(212,168,67,0.07)',border:`1px solid ${C.gold}30`,borderRadius:8,padding:'10px 14px',fontSize:12,color:C.gold,marginBottom:14}}>
        ⚠ Unavailable — Schedule is not resource loaded. Upload a resource-loaded XER from P6 to enable manpower tracking.
      </div>}

      {/* KPI row */}
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(145px,1fr))',gap:9,marginBottom:16}}>
        {[
          {l:'Budgeted MH',   v:resourceLoaded?fmtH(prod.budgetedHours):'Unavailable', d:'Total planned hours',           c:resourceLoaded?C.text:C.muted2},
          {l:'Actual MH',     v:resourceLoaded?fmtH(prod.actualHours):'Unavailable',   d:'Hours expended to date',        c:resourceLoaded?C.amber:C.muted2},
          {l:'Remaining MH',  v:resourceLoaded?fmtH(prod.remainingHours):'Unavailable',d:'Hours still to be performed',   c:resourceLoaded?C.accent:C.muted2},
          {l:'Earned MH',     v:resourceLoaded?fmtH(prod.earnedHours):'Unavailable',   d:'Duration % Complete × Budgeted',c:resourceLoaded?C.green:C.muted2},
          {l:'MH % Complete', v:pctComplete!=null?`${pctComplete}%`:'Unavailable',     d:'Earned ÷ Budgeted',             c:pctComplete==null?C.muted2:(pctComplete>=80?C.green:pctComplete>=50?C.amber:C.red)},
          {l:'MH CPI',        v:M.authoritativeEvm?.hours?.cpi!=null?M.authoritativeEvm.hours.cpi.toFixed(3):'Unavailable',  d:'Productivity (Earned÷Actual)',         c:M.authoritativeEvm?.hours?.cpi==null?C.muted2:cpiCol(M.authoritativeEvm.hours.cpi), warn:M.authoritativeEvm?.hours?.cpi!=null&&M.authoritativeEvm.hours.cpi<0.95},
          {l:'MH SPI',        v:M.authoritativeEvm?.hours?.spi!=null?M.authoritativeEvm.hours.spi.toFixed(3):'Unavailable',  d:'Schedule perf (Earned÷Planned)',       c:M.authoritativeEvm?.hours?.spi==null?C.muted2:(M.authoritativeEvm.hours.spi>=1?C.green:M.authoritativeEvm.hours.spi>=0.9?C.amber:C.red), warn:M.authoritativeEvm?.hours?.spi!=null&&M.authoritativeEvm.hours.spi<0.9},
          {l:'MH EAC',        v:(()=>{const s=M.authoritativeEvm?.hoursForecast?.scenarios?.[0];return s?fmtH(s.eac):'Unavailable';})(),  d:'Estimated hours at completion',       c:C.muted2},
        ].map((k:any,i)=><KPI key={i} label={k.l} value={k.v} sub={k.d} color={k.c} warn={k.warn}/>)}
      </div>

      <div style={{display:'flex',flexWrap:'wrap',gap:12}}>
        {/* Manpower S-Curve */}
        {(mh.curve||[]).length>1&&<CC title="Manpower S-Curve — Cumulative Budgeted vs Actual vs Earned MH" height={300} flex="2 1 500px">
          <ResponsiveContainer><ComposedChart data={mh.curve} margin={{left:-10}}>
            <defs>
              <linearGradient id="gMHBgt" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={BGT_COLOR} stopOpacity={0.18}/><stop offset="95%" stopColor={BGT_COLOR} stopOpacity={0}/></linearGradient>
              <linearGradient id="gMHErn" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.green}   stopOpacity={0.15}/><stop offset="95%" stopColor={C.green}   stopOpacity={0}/></linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis dataKey="month" tick={{fill:C.muted,fontSize:10}} interval={Math.max(0,Math.floor((mh.curve||[]).length/14))}/>
            <YAxis tick={{fill:C.muted,fontSize:10}} tickFormatter={(v:number)=>v>=1000?`${(v/1000).toFixed(0)}K`:String(v)}/>
            <Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:11}}/>
            <Area type="monotone" dataKey="budgeted" name="Budgeted MH" stroke={BGT_COLOR} fill="url(#gMHBgt)" strokeWidth={2.5} dot={false}/>
            <Area type="monotone" dataKey="earned"   name="Earned MH"   stroke={C.green}  fill="url(#gMHErn)" strokeWidth={2.5} dot={false}/>
            {M.isHrLoaded&&<Area type="monotone" dataKey="actual" name="Actual MH" stroke={C.amber} fill="none" strokeWidth={2} dot={false} strokeDasharray="5 3"/>}
          </ComposedChart></ResponsiveContainer>
        </CC>}

        {/* Activities by budgeted hours distribution */}
        {(mh.hist||[]).length>0&&<CC title="Activities by Budgeted Hours Band" height={300} flex="1 1 280px">
          <ResponsiveContainer><BarChart data={mh.hist} margin={{left:-10}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis dataKey="range" tick={{fill:C.muted,fontSize:11}}/>
            <YAxis tick={{fill:C.muted,fontSize:11}}/>
            <Tooltip content={<TT/>}/>
            <Bar dataKey="count" name="Activities" radius={[4,4,0,0]}>
              {(mh.hist||[]).map((e:any,i:number)=><Cell key={i} fill={e.fill}/>)}
            </Bar>
          </BarChart></ResponsiveContainer>
        </CC>}

        {/* MH by WBS — budgeted vs actual vs earned */}
        {(mh.byWBS||[]).length>0&&<CC title="Man-Hours by WBS — Budgeted vs Actual vs Earned" height={300} flex="2 1 440px">
          <ResponsiveContainer><BarChart layout="vertical" data={mh.byWBS} margin={{left:8}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis type="number" tick={{fill:C.muted,fontSize:11}} tickFormatter={(v:number)=>v>=1000?`${(v/1000).toFixed(0)}K`:String(v)}/>
            <YAxis type="category" dataKey="wbs" tick={{fill:C.muted,fontSize:10}} width={120}/>
            <Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/>
            <Bar dataKey="budgeted"  name="Budgeted"  fill={BGT_COLOR} radius={[0,4,4,0]}/>
            <Bar dataKey="actual"    name="Actual"    fill={C.amber}  radius={[0,4,4,0]}/>
            <Bar dataKey="earned"    name="Earned"    fill={C.green}  radius={[0,4,4,0]}/>
          </BarChart></ResponsiveContainer>
        </CC>}

        {/* MH by resource type */}
        {(mh.byType||[]).length>0&&<CC title="Man-Hours by Resource Type" height={300} flex="1 1 260px">
          <ResponsiveContainer><BarChart layout="vertical" data={mh.byType} margin={{left:8}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis type="number" tick={{fill:C.muted,fontSize:11}} tickFormatter={(v:number)=>v>=1000?`${(v/1000).toFixed(0)}K`:String(v)}/>
            <YAxis type="category" dataKey="type" tick={{fill:C.muted,fontSize:11}} width={90}/>
            <Tooltip content={<TT/>}/>
            <Bar dataKey="hours" name="Budgeted Hours" radius={[0,4,4,0]}>
              {(mh.byType||[]).map((_:any,i:number)=>{
                const cols=[C.accent,C.green,C.amber,C.purple,C.muted2];
                return <Cell key={i} fill={cols[i%cols.length]}/>;
              })}
            </Bar>
          </BarChart></ResponsiveContainer>
        </CC>}
      </div>

      {/* Per-project MH table */}
      {M.isHrLoaded&&(M.projects||[]).some((p:any)=>p.bgtHrs>0)&&<div style={{marginTop:16,background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:'auto'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:680}}>
          <thead><tr style={{background:'rgba(0,200,240,0.06)'}}>
            {['Project','Budgeted MH','Actual MH','Remaining MH','Earned MH','MH %','MH CPI'].map(h=>(
              <th key={h} style={{padding:'9px 12px',textAlign:'left',color:C.muted,fontWeight:600,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em',whiteSpace:'nowrap'}}>{h}</th>
            ))}
          </tr></thead>
          <tbody>{(M.projects||[]).filter((p:any)=>p.bgtHrs>0).map((p:any)=>{
            const pCpi=M.authoritativeEvmByProject?.[p.id]?.hours?.cpi;
            return(<tr key={p.id} style={{borderTop:`1px solid ${C.border}`}}>
              <td style={{padding:'9px 12px',color:C.text,fontWeight:600}}>{p.name}</td>
              <td style={{padding:'9px 12px',color:C.text,     fontFamily:"'DM Mono',monospace"}}>{fmtH(p.bgtHrs)}</td>
              <td style={{padding:'9px 12px',color:C.amber,    fontFamily:"'DM Mono',monospace"}}>{fmtH(p.actHrs)}</td>
              <td style={{padding:'9px 12px',color:C.accent,   fontFamily:"'DM Mono',monospace"}}>{fmtH(p.remHrs)}</td>
              <td style={{padding:'9px 12px',color:C.green,    fontFamily:"'DM Mono',monospace"}}>{fmtH(p.ernHrs)}</td>
              <td style={{padding:'9px 12px',minWidth:100}}>
                <div style={{display:'flex',alignItems:'center',gap:5}}>
                  <div style={{width:60,background:C.border,borderRadius:3,height:4}}><div style={{width:`${Math.min(100,p.mhPct||0)}%`,background:C.green,borderRadius:3,height:4}}/></div>
                  <span style={{color:'#111111',fontSize:11}}>{(p.mhPct||0).toFixed(1)}%</span>
                </div>
              </td>
              <td style={{padding:'9px 12px',color:pCpi==null?C.muted2:cpiCol(pCpi),fontWeight:700,fontFamily:"'DM Mono',monospace"}}>{pCpi!=null?pCpi.toFixed(3):'Unavailable'}</td>
            </tr>);
          })}</tbody>
        </table>
      </div>}
    </Sec>
      );
    })()}
  </>);
}

function CriticalView({allActivities}:any){
  // The float-bucket KPIs, distribution charts and sortable float table that
  // used to live here were a straight duplicate of Float Analysis (which
  // reads the same activity_analysis.py rows and additionally tracks
  // Previous-vs-Current float change) — moved there per the Phase 1
  // consolidation. Path Tracing below is genuine path/network analysis
  // (driving-predecessor chain, not a CPM recompute) and stays here.
  const [nearCritDays,setNearCritDays]=useState(5);
  const actMap=useMemo(()=>{const m:Record<string,any>={};(allActivities||[]).forEach((a:any)=>{m[a.id]=a;});return m;},[allActivities]);

  return(<>
    <Sec title="Critical Path" icon="🔴">
      <div style={{margin:"0 0 14px",padding:"10px 16px",borderRadius:10,background:`${C.accent}10`,border:`1px solid ${C.accent}30`,display:"flex",alignItems:"center",gap:10}}>
        <span style={{fontSize:16}}>📊</span>
        <div style={{fontSize:12,color:C.muted,lineHeight:1.5}}>
          Float distribution, Negative/Zero Float KPIs and Critical % by Project moved to <strong style={{color:C.text}}>Float Analysis</strong> — same activity_analysis.py rows, plus Previous-vs-Current float-change tracking this page didn't have.
        </div>
      </div>
      {/* Near-critical threshold control — used below by Path Tracing */}
      <div style={{display:"flex",alignItems:"center",gap:10,flexWrap:"wrap"}}>
        <span style={{fontSize:12,color:C.muted2}}>Near-critical threshold:</span>
        <div style={{display:"flex",alignItems:"center",gap:6,background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:"5px 10px"}}>
          <span style={{fontSize:11,color:C.muted}}>Float</span>
          <span style={{fontSize:12,color:C.muted2}}>1 –</span>
          <input
            type="number" min={1} max={99}
            value={nearCritDays}
            onChange={e=>{const v=Math.max(1,Math.min(99,parseInt(e.target.value)||1));setNearCritDays(v);}}
            style={{width:44,background:"transparent",border:`1px solid ${C.border}`,color:C.amber,borderRadius:5,padding:"2px 6px",fontSize:13,fontFamily:"'DM Mono',monospace",fontWeight:700,textAlign:"center",outline:"none"}}
          />
          <span style={{fontSize:12,color:C.muted2}}>days</span>
        </div>
        <span style={{fontSize:11,color:C.muted}}>Activities with float between 1 and {nearCritDays} days are near-critical in the chain below</span>
      </div>
    </Sec>

    <PathTracePanel allActivities={allActivities} actMap={actMap} nearCritDays={nearCritDays}/>
  </>);
}

// ─── PATH TRACING (Phase 5) ────────────────────────────────────────────────────
// pickDrivingRel/tracePath now live in pathTrace.ts (shared with GanttView.tsx's
// activity detail drawer) — see that file for the "driving neighbour" heuristic
// and why it is explicitly not a recalculated CPM.
function PathTracePanel({allActivities,actMap,nearCritDays}:any){
  const [query,setQuery]=useState("");
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [direction,setDirection]=useState<"predecessors"|"successors">("predecessors");
  const [preset,setPreset]=useState<"any"|"milestones"|"negFloat">("any");

  const candidates=useMemo(()=>{
    const pool=(allActivities||[]);
    const filtered = preset==="milestones" ? pool.filter((a:any)=>a.isMilestone)
      : preset==="negFloat" ? pool.filter((a:any)=>a.totalFloat!=null&&a.totalFloat<0)
      : pool;
    if(!query.trim())return filtered.slice(0,25);
    const q=query.toLowerCase();
    return filtered.filter((a:any)=>(a.code||"").toLowerCase().includes(q)||(a.name||"").toLowerCase().includes(q)).slice(0,25);
  },[allActivities,query,preset]);

  const chain=useMemo(()=>{
    if(!selectedId)return [];
    return tracePath(selectedId, direction, actMap);
  },[selectedId,direction,actMap]);

  const fmtD=(d:any)=>{
    if(!d)return "—";
    const dt=d instanceof Date?d:new Date(d);
    return isNaN(dt.getTime())?"—":dt.toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"});
  };

  return(
    <Sec title="Path Tracing" icon="🔗">
      <div style={{fontSize:12,color:C.muted2,marginBottom:14,lineHeight:1.6}}>
        Relationship trace using each activity's imported total float to pick the driving
        neighbour at every step — <strong>not</strong> a recalculated CPM. (A CPM recalculation
        only happens when you edit schedule logic — see the "Schedule logic edited" banner.)
      </div>

      <div style={{display:"flex",flexWrap:"wrap",gap:10,alignItems:"center",marginBottom:14}}>
        <div style={{display:"flex",gap:6}}>
          {[{id:"any",label:"Any Activity"},{id:"milestones",label:"Milestones"},{id:"negFloat",label:"Negative Float"}].map(p=>(
            <button key={p.id} onClick={()=>{setPreset(p.id as any);setSelectedId(null);setQuery("");}}
              style={{background:preset===p.id?`${C.accent}18`:C.card,border:`1px solid ${preset===p.id?C.accent:C.border}`,color:preset===p.id?C.accent:C.muted2,borderRadius:7,padding:"5px 12px",cursor:"pointer",fontSize:11,fontFamily:"inherit",fontWeight:600}}>
              {p.label}
            </button>
          ))}
        </div>
        <div style={{display:"flex",gap:6,marginLeft:"auto"}}>
          {[{id:"predecessors",label:"↑ Trace Predecessors"},{id:"successors",label:"↓ Trace Successors"}].map(d=>(
            <button key={d.id} onClick={()=>setDirection(d.id as any)}
              style={{background:direction===d.id?C.accent:C.card,border:`1px solid ${direction===d.id?C.accent:C.border}`,color:direction===d.id?"#fff":C.muted2,borderRadius:7,padding:"5px 12px",cursor:"pointer",fontSize:11,fontFamily:"inherit",fontWeight:600}}>
              {d.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{position:"relative",marginBottom:16}}>
        <input
          value={selectedId ? `${actMap[selectedId]?.code} — ${actMap[selectedId]?.name}` : query}
          onChange={e=>{setQuery(e.target.value);setSelectedId(null);}}
          placeholder={preset==="milestones"?"Search milestones…":preset==="negFloat"?"Search negative-float activities…":"Search activity ID or name…"}
          style={{width:"100%",maxWidth:480,background:C.card,border:`1px solid ${C.border}`,borderRadius:8,padding:"8px 12px",fontSize:13,fontFamily:"inherit",color:C.text}}
        />
        {!selectedId&&query.trim()&&candidates.length>0&&(
          <div style={{position:"absolute",top:"calc(100% + 4px)",left:0,width:"100%",maxWidth:480,background:C.panel,border:`1px solid ${C.border}`,borderRadius:8,boxShadow:"0 8px 24px rgba(0,0,0,0.15)",zIndex:50,maxHeight:260,overflowY:"auto"}}>
            {candidates.map((a:any)=>(
              <div key={a.id||a.code} onClick={()=>{setSelectedId(a.id||a.code);setQuery("");}}
                style={{padding:"7px 12px",cursor:"pointer",fontSize:12,borderBottom:`1px solid ${C.border}`}}
                onMouseEnter={e=>(e.currentTarget as HTMLElement).style.background=C.card}
                onMouseLeave={e=>(e.currentTarget as HTMLElement).style.background="transparent"}
              >
                <span style={{fontFamily:"monospace",color:C.accent}}>{a.code}</span> — {a.name}
                {a.totalFloat!=null&&<span style={{marginLeft:8,color:a.totalFloat<0?C.red:C.muted2}}>({a.totalFloat}d float)</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      {!selectedId && <div style={{color:C.muted,fontSize:13,textAlign:"center",padding:"20px 0"}}>Select an activity or milestone above to trace its driving chain.</div>}

      {selectedId && chain.length===0 && <div style={{color:C.muted,fontSize:13}}>No {direction} found for this activity.</div>}

      {selectedId && chain.length>0 && (
        <div style={{display:"flex",flexDirection:"column",gap:0}}>
          {chain.map((node:any,idx:number)=>{
            const a=node.activity;
            const f=a.totalFloat;
            const isNearCrit=f!=null&&f>=1&&f<=nearCritDays;
            const isCrit=f!=null&&f<=0;
            const borderColor=isCrit?C.red:isNearCrit?C.amber:C.border;
            return(
              <div key={a.id||a.code}>
                {idx>0&&(
                  <div style={{display:"flex",alignItems:"center",gap:8,padding:"4px 0 4px 24px",color:C.muted2,fontSize:11}}>
                    <span style={{fontSize:14}}>↓</span>
                    <span style={{fontFamily:"monospace",fontWeight:700}}>{node.relFromPrev?.relType||"FS"}</span>
                    {!!(node.relFromPrev?.lagDays)&&<span>lag {node.relFromPrev.lagDays}d</span>}
                  </div>
                )}
                <div onClick={()=>{setSelectedId(a.id||a.code);setQuery("");}}
                  style={{display:"flex",alignItems:"center",gap:14,background:C.card,border:`1px solid ${borderColor}`,borderLeft:`4px solid ${borderColor}`,borderRadius:8,padding:"10px 14px",cursor:"pointer"}}
                  title="Click to re-trace the driving chain from this activity"
                >
                  <div style={{minWidth:90,fontFamily:"monospace",fontSize:12,color:C.accent,fontWeight:700}}>{a.code}</div>
                  <div style={{flex:"1 1 220px",minWidth:0}}>
                    <div style={{fontSize:13,fontWeight:600,color:C.text,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{a.name}</div>
                    <div style={{fontSize:11,color:C.muted2}}>
                      {a.wbs||"—"}{a.area?` · Area ${a.area}`:""}{a.discipline?` · ${a.discipline}`:""}{a.contractor?` · ${a.contractor}`:""}
                    </div>
                  </div>
                  <div style={{fontSize:11,color:C.muted2,minWidth:170,textAlign:"right"}}>{fmtD(a.bStart)} → {fmtD(a.bFinish)}</div>
                  <div style={{minWidth:60,textAlign:"right"}}>
                    {f==null
                      ?<span style={{background:`${C.green}15`,color:C.green,fontFamily:"monospace",fontWeight:700,fontSize:11,padding:"2px 8px",borderRadius:5}}>DONE</span>
                      :<span style={{background:isCrit?"rgba(255,87,87,0.1)":isNearCrit?"rgba(255,181,71,0.1)":"transparent",color:isCrit?C.red:isNearCrit?C.amber:C.muted2,fontFamily:"monospace",fontWeight:700,fontSize:11,padding:"2px 8px",borderRadius:5}}>{f}d</span>
                    }
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Sec>
  );
}

function ComparisonView({M,files}:any){
  return(<>
    <Sec title="Project Performance Comparison" icon="⚖️">
      <div style={{display:"flex",flexWrap:"wrap",gap:12,marginBottom:16}}>
        <CC title="Schedule % Complete" flex="1 1 300px" height={240}><ResponsiveContainer><BarChart data={M.projects.map((p:any)=>({name:p.name.split(" ").slice(0,2).join(" "),pct:p.pctComplete}))} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="name" tick={{fill:C.muted,fontSize:10}}/><YAxis tick={{fill:C.muted,fontSize:11}} unit="%"/><Tooltip content={<TT/>}/><Bar dataKey="pct" name="% Complete" radius={[4,4,0,0]}>{M.projects.map((_:any,i:number)=><Cell key={i} fill={PROJ_COLORS[i%PROJ_COLORS.length]}/>)}</Bar></BarChart></ResponsiveContainer></CC>
        <CC title="Activity Count Split" flex="1 1 240px" height={240}><ResponsiveContainer><PieChart><Pie data={M.projects.map((p:any)=>({name:p.name,value:p.total}))} cx="50%" cy="50%" outerRadius={85} innerRadius={38} dataKey="value" label={({percent}:any)=>`${(percent*100).toFixed(0)}%`} labelLine={false} fontSize={11}>{M.projects.map((_:any,i:number)=><Cell key={i} fill={PROJ_COLORS[i%PROJ_COLORS.length]}/>)}</Pie><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/></PieChart></ResponsiveContainer></CC>
        <CC title="Complete / Overdue / Critical" flex="2 1 380px" height={240}><ResponsiveContainer><BarChart data={M.projects.map((p:any)=>({name:p.name.split(" ").slice(0,2).join(" "),Complete:p.complete,Overdue:p.overdue,Critical:p.critical}))} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="name" tick={{fill:C.muted,fontSize:10}}/><YAxis tick={{fill:C.muted,fontSize:11}}/><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/><Bar dataKey="Complete" fill={C.green} radius={[4,4,0,0]}/><Bar dataKey="Overdue" fill={C.amber} radius={[4,4,0,0]}/><Bar dataKey="Critical" fill={C.red} radius={[4,4,0,0]}/></BarChart></ResponsiveContainer></CC>
      </div>
    </Sec>
    <Sec title="Full Comparison Table" icon="📋">
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:"auto"}}>
        <table style={{width:"100%",borderCollapse:"collapse",fontSize:12,minWidth:900}}>
          <thead><tr style={{background:"rgba(212,168,67,0.06)"}}>{["Project","Total","Complete","In Prog","Not Start","Critical","Neg Float","Overdue","% Done","BEI","Health"].map(h=><th key={h} style={{padding:"9px 12px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.05em",whiteSpace:"nowrap"}}>{h}</th>)}</tr></thead>
          <tbody>{M.projects.map((p:any,i:number)=><tr key={p.id} style={{borderTop:`1px solid ${C.border}`}}>
            <td style={{padding:"9px 12px"}}><div style={{display:"flex",alignItems:"center",gap:6}}><div style={{width:7,height:7,borderRadius:"50%",background:PROJ_COLORS[i%PROJ_COLORS.length]}}/><span style={{color:C.text,fontWeight:500}}>{p.name}</span></div></td>
            <td style={{padding:"9px 12px",color:C.text}}>{p.total}</td><td style={{padding:"9px 12px",color:C.green}}>{p.complete}</td><td style={{padding:"9px 12px",color:C.accent}}>{p.inProgress}</td><td style={{padding:"9px 12px",color:C.muted2}}>{p.notStarted}</td><td style={{padding:"9px 12px",color:C.red}}>{p.critical}</td>
            <td style={{padding:"9px 12px",color:p.negFloat>0?C.red:C.muted2,fontWeight:p.negFloat>0?700:400}}>{p.negFloat}</td>
            <td style={{padding:"9px 12px",color:p.overdue>0?C.amber:C.muted2}}>{p.overdue}</td>
            <td style={{padding:"9px 12px",minWidth:110}}><div style={{display:"flex",alignItems:"center",gap:5}}><div style={{width:65,background:C.border,borderRadius:3,height:5}}><div style={{width:`${p.pctComplete}%`,background:p.pctComplete>=80?C.green:p.pctComplete>=50?C.amber:C.red,borderRadius:3,height:5}}/></div><span style={{color:'#111111',fontSize:11}}>{p.pctComplete}%</span></div></td>
            <td style={{padding:"9px 12px",color:p.BEI>=0.95?C.green:p.BEI>=0.8?C.amber:C.red,fontFamily:"'DM Mono',monospace"}}>{p.BEI.toFixed(2)}</td>
            <td style={{padding:"9px 12px"}}><span style={{fontSize:11,padding:"3px 8px",borderRadius:20,background:`${p.health.color}18`,color:p.health.color,fontWeight:600}}>{p.health.label}</span></td>
          </tr>)}</tbody>
          <tfoot><tr style={{borderTop:`2px solid ${C.border}`,background:"rgba(212,168,67,0.04)"}}>
            <td style={{padding:"9px 12px",color:C.gold,fontWeight:700}}>PORTFOLIO ({M.projects.length})</td>
            <td style={{padding:"9px 12px",color:C.text,fontWeight:600}}>{M.total}</td><td style={{padding:"9px 12px",color:C.green,fontWeight:600}}>{M.completed}</td><td style={{padding:"9px 12px",color:C.accent,fontWeight:600}}>{M.inProgress}</td><td style={{padding:"9px 12px",color:C.muted2,fontWeight:600}}>{M.notStarted}</td><td style={{padding:"9px 12px",color:C.red,fontWeight:600}}>{M.critical}</td>
            <td style={{padding:"9px 12px",color:M.fb.negative>0?C.red:C.muted2,fontWeight:600}}>{M.fb.negative}</td>
            <td style={{padding:"9px 12px",color:M.overdue>0?C.amber:C.muted2,fontWeight:600}}>{M.overdue}</td>
            <td style={{padding:"9px 12px",color:C.green,fontWeight:600}}>{M.schedPct.toFixed(1)}%</td>
            <td style={{padding:"9px 12px",color:M.BEI>=0.95?C.green:C.amber,fontFamily:"'DM Mono',monospace",fontWeight:600}}>{M.BEI.toFixed(2)}</td><td/>
          </tr></tfoot>
        </table>
      </div>
    </Sec>
  </>);
}

// ─── ACTIVITY EDIT MODAL ──────────────────────────────────────────────────────
function ActivityEditModal({act,onSave,onClose}:{act:any;onSave:(id:string,chg:any)=>void;onClose:()=>void;}){
  const toInput=(v:any)=>{if(!v)return'';const d=v instanceof Date?v:new Date(v);return isNaN(d.getTime())?'':d.toISOString().slice(0,10);};
  const today=new Date().toISOString().slice(0,10);
  const isComplete=(act.pctComplete||0)>=100||act.status==='TK_Complete';
  type Mode='NOT_STARTED'|'IN_PROGRESS'|'COMPLETE';
  const [mode,setMode]=useState<Mode>(isComplete?'COMPLETE':act.start?'IN_PROGRESS':'NOT_STARTED');
  const [aStart,setAStart]=useState(toInput(act.start));
  const [aFinish,setAFinish]=useState(toInput(act.finish));
  const [pct,setPct]=useState<number>(act.pctComplete||0);

  const switchMode=(m:Mode)=>{
    setMode(m);
    if(m!=='NOT_STARTED'&&!aStart)setAStart(today);
    if(m==='COMPLETE'&&!aFinish)setAFinish(today);
  };

  const save=()=>{
    let chg:any={_edited:true};
    if(mode==='NOT_STARTED'){
      chg={...chg,start:null,finish:null,pctComplete:0,status:'TK_NotStart'};
    }else if(mode==='IN_PROGRESS'){
      chg={...chg,start:aStart||today,finish:null,pctComplete:Math.max(1,Math.min(99,pct)),status:'TK_Active'};
    }else{
      chg={...chg,start:aStart||today,finish:aFinish||today,pctComplete:100,status:'TK_Complete'};
    }
    onSave(act.id||act.code,chg);
    onClose();
  };

  const inp:React.CSSProperties={background:C.panel,border:`1px solid ${C.border}`,color:C.text,borderRadius:7,padding:"7px 11px",fontSize:13,fontFamily:"inherit",width:"100%",outline:"none",colorScheme:"dark" as any};
  const fmtBL=(v:any)=>v?new Date(v instanceof Date?v:v).toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'}):'—';

  return(
    <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.62)",zIndex:3000,display:"flex",alignItems:"center",justifyContent:"center"}} onClick={onClose}>
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:14,width:440,maxWidth:"95vw",overflow:"hidden",boxShadow:"0 24px 64px rgba(0,0,0,0.85)"}} onClick={e=>e.stopPropagation()}>
        {/* Header */}
        <div style={{background:C.card2,borderBottom:`1px solid ${C.border}`,padding:"12px 18px",display:"flex",justifyContent:"space-between",alignItems:"flex-start"}}>
          <div>
            <div style={{fontWeight:700,fontSize:14,color:C.text}}>Update Activity</div>
            <div style={{fontSize:11,color:C.muted,marginTop:3,fontFamily:"'DM Mono',monospace",lineHeight:1.4}}>{act.code}{act.code&&' — '}{act.name}</div>
          </div>
          <button onClick={onClose} style={{background:"transparent",border:"none",color:C.muted,cursor:"pointer",fontSize:22,lineHeight:1,padding:"0 2px",marginTop:-2}}>×</button>
        </div>

        {/* Body */}
        <div style={{padding:"18px 18px 6px"}}>
          {/* Status toggle */}
          <div style={{marginBottom:16}}>
            <div style={{fontSize:11,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:".07em",marginBottom:8}}>Status</div>
            <div style={{display:"flex",gap:6}}>
              {([['NOT_STARTED','Not Started',C.muted2],['IN_PROGRESS','In Progress',C.accent],['COMPLETE','Complete',C.green]] as [Mode,string,string][]).map(([k,label,col])=>(
                <button key={k} onClick={()=>switchMode(k)} style={{flex:1,padding:"8px 4px",borderRadius:8,border:`2px solid ${mode===k?col:C.border}`,background:mode===k?`${col}18`:"transparent",color:mode===k?col:C.muted2,fontWeight:mode===k?700:400,fontSize:12,cursor:"pointer",fontFamily:"inherit",transition:"all .13s"}}>
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Actual Start */}
          <div style={{marginBottom:14}}>
            <label style={{display:"block",fontSize:11,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>Actual Start Date</label>
            <input type="date" value={aStart} onChange={e=>setAStart(e.target.value)}
              disabled={mode==='NOT_STARTED'} style={{...inp,opacity:mode==='NOT_STARTED'?0.4:1}}/>
          </div>

          {/* % Complete – In Progress only */}
          {mode==='IN_PROGRESS'&&(
            <div style={{marginBottom:14}}>
              <label style={{display:"block",fontSize:11,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>
                % Complete&nbsp;<span style={{color:C.accent,fontFamily:"'DM Mono',monospace",fontWeight:700}}>{pct}%</span>
              </label>
              <input type="range" min={1} max={99} value={pct} onChange={e=>setPct(Number(e.target.value))}
                style={{width:"100%",accentColor:C.accent}}/>
              <div style={{display:"flex",justifyContent:"space-between",marginTop:2}}>
                <span style={{fontSize:10,color:C.muted2}}>1%</span>
                <span style={{fontSize:10,color:C.muted2}}>99%</span>
              </div>
            </div>
          )}

          {/* Actual Finish – Complete only */}
          {mode==='COMPLETE'&&(
            <div style={{marginBottom:14}}>
              <label style={{display:"block",fontSize:11,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:".07em",marginBottom:6}}>Actual Finish Date</label>
              <input type="date" value={aFinish} onChange={e=>setAFinish(e.target.value)} style={inp}/>
            </div>
          )}

          {/* Baseline reference */}
          <div style={{background:C.card2,borderRadius:8,padding:"8px 12px",fontSize:11,color:C.muted,marginBottom:14,display:"flex",gap:18}}>
            <span>BL Start: <span style={{color:C.muted2,fontFamily:"'DM Mono',monospace"}}>{fmtBL(act.bStart)}</span></span>
            <span>BL Finish: <span style={{color:C.muted2,fontFamily:"'DM Mono',monospace"}}>{fmtBL(act.bFinish)}</span></span>
          </div>
        </div>

        {/* Footer */}
        <div style={{background:C.card2,borderTop:`1px solid ${C.border}`,padding:"12px 18px",display:"flex",justifyContent:"flex-end",gap:8}}>
          <button onClick={onClose} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:8,padding:"7px 18px",cursor:"pointer",fontSize:13,fontFamily:"inherit"}}>Cancel</button>
          <button onClick={save} style={{background:`${C.accent}20`,border:`1px solid ${C.accent}`,color:C.accent,borderRadius:8,padding:"7px 20px",cursor:"pointer",fontSize:13,fontFamily:"inherit",fontWeight:700}}>Save Update</button>
        </div>
      </div>
    </div>
  );
}

function ActivityRegister({allActivities,projects,initialSearch="",onSearchChange,initialFilt="ALL",onUpdateActivity,activityUpdates,dataDate}:any){
  const [search,setSearch]=useState(initialSearch),[proj,setProj]=useState("ALL"),[filt,setFilt]=useState(initialFilt),[page,setPage]=useState(0);
  const [expandedActId,setExpandedActId]=useState<string|null>(null);
  const toggleAct=(id:string)=>setExpandedActId(p=>p===id?null:id);
  const [editingAct,setEditingAct]=useState<any>(null);
  const actMap=useMemo(()=>{const m:Record<string,any>={};(allActivities||[]).forEach((a:any)=>{m[a.id]=a;});return m;},[allActivities]);
  // OVERDUE filter below compares against the active schedule's own
  // effective Data Date — never today's date.
  const ddParsed=parseDate(dataDate);

  // Column resize
  type CK='code'|'name'|'project'|'bStart'|'bFinish'|'fcStart'|'fcFinish'|'sv'|'fv'|'dur'|'float'|'pct'|'status';
  const COL_DEFS:[CK,string][]=[['code','Code'],['name','Activity Name'],['project','Project'],['bStart','BL Start'],['bFinish','BL Finish'],['fcStart','Fcst Start'],['fcFinish','Fcst Finish'],['sv','Start Var'],['fv','Finish Var'],['dur','Dur'],['float','Float'],['pct','% Done'],['status','Status']];
  const DEFAULT_W:Record<CK,number>={code:90,name:220,project:140,bStart:100,bFinish:100,fcStart:100,fcFinish:104,sv:78,fv:78,dur:58,float:65,pct:95,status:105};
  const [colW,setColW]=useState<Record<CK,number>>(DEFAULT_W);
  const dragRef=useRef<{key:CK;x0:number;w0:number}|null>(null);
  const onResizeStart=useCallback((key:CK,e:React.MouseEvent)=>{
    e.preventDefault();e.stopPropagation();
    dragRef.current={key,x0:e.clientX,w0:colW[key]};
    const onMove=(ev:MouseEvent)=>{
      if(!dragRef.current)return;
      const{key,x0,w0}=dragRef.current;
      setColW(p=>({...p,[key]:Math.max(50,w0+ev.clientX-x0)}));
    };
    const onUp=()=>{dragRef.current=null;document.removeEventListener('mousemove',onMove);document.removeEventListener('mouseup',onUp);};
    document.addEventListener('mousemove',onMove);document.addEventListener('mouseup',onUp);
  },[colW]);
  const arCols=useColOrder(COL_DEFS,['code','name','dur','float','fcStart','fcFinish']);
  const {visible:arVisible,hidden:arHidden}=arCols;

  // Sync when parent navigates here from global search or KPI click
  useEffect(()=>{
    if(initialSearch&&initialSearch!==search){setSearch(initialSearch);setPage(0);}
  },[initialSearch]);
  useEffect(()=>{
    if(initialFilt&&initialFilt!==filt){setFilt(initialFilt);setPage(0);}
  },[initialFilt]);
  // EPC phase filter
  const [epcFilt,setEpcFilt]=useState<string>('ALL');
  // WBS grouping (P6-style)
  const [groupByWBS,setGroupByWBS]=useState(false);
  const [collapsedWbs,setCollapsedWbs]=useState<Set<string>>(new Set<string>());
  const [collapsedPhases,setCollapsedPhases]=useState<Set<string>>(new Set<string>());
  const togglePhase=useCallback((k:string)=>setCollapsedPhases(p=>{const n=new Set(p);n.has(k)?n.delete(k):n.add(k);return n;}),[]);
  const toggleWbs=useCallback((k:string)=>setCollapsedWbs(p=>{const n=new Set(p);n.has(k)?n.delete(k):n.add(k);return n;}),[]);

  // WBS column dropdown — hide folder rows with no activities directly assigned
  // to them under the current filter (search/status/EPC phase); their non-empty
  // sub-folders/activities still show underneath.
  const [hideEmptyWbs,setHideEmptyWbs]=useState(false);
  const [wbsMenuOpen,setWbsMenuOpen]=useState(false);
  const wbsMenuRef=useRef<HTMLDivElement>(null);
  useEffect(()=>{
    if(!wbsMenuOpen)return;
    const close=(e:MouseEvent)=>{if(wbsMenuRef.current&&!wbsMenuRef.current.contains(e.target as Node))setWbsMenuOpen(false);};
    document.addEventListener('mousedown',close);
    return()=>document.removeEventListener('mousedown',close);
  },[wbsMenuOpen]);

  // WBS context menu
  const [wbsCtx,setWbsCtx]=useState<{x:number;y:number}|null>(null);
  useEffect(()=>{
    if(!wbsCtx)return;
    const close=()=>setWbsCtx(null);
    document.addEventListener('mousedown',close,{once:true});
    document.addEventListener('keydown',close,{once:true});
    document.addEventListener('scroll',close,{once:true,capture:true});
    return()=>{document.removeEventListener('mousedown',close);document.removeEventListener('keydown',close);};
  },[wbsCtx]);
  const getWbsLevel=(wbs:string)=>{
    if(wbs.includes('.'))return wbs.split('.').length;
    if(wbs.includes('/'))return wbs.split('/').length;
    if(wbs.includes('-'))return wbs.split('-').length;
    return 1;
  };

  const PAGE=999999;
  const shown=useMemo(()=>{
    let a=allActivities;
    if(proj!=="ALL")         a=a.filter((x:any)=>x.projectId===proj);
    if(search)               a=a.filter((x:any)=>x.name.toLowerCase().includes(search.toLowerCase())||x.code.toLowerCase().includes(search.toLowerCase()));
    if(filt==="CRITICAL")    a=a.filter((x:any)=>x.isCritical&&!x.isMilestone);
    if(filt==="OVERDUE")     a=a.filter((x:any)=>x.bFinish&&ddParsed&&x.bFinish<ddParsed&&(x.pctComplete||0)<100);
    if(filt==="NOTSTART")    a=a.filter((x:any)=>!x.start&&(x.pctComplete||0)<100);
    if(filt==="NEGFLOAT")    a=a.filter((x:any)=>x.totalFloat!=null&&x.totalFloat<0);
    if(filt==="COMPLETE")    a=a.filter((x:any)=>(x.pctComplete||0)>=100||x.totalFloat==null||x.status==="TK_Complete");
    if(filt==="INPROG")      a=a.filter((x:any)=>x.start&&x.totalFloat!=null&&!((x.pctComplete||0)>=100||x.totalFloat==null||x.status==="TK_Complete"));
    if(filt==="MILESTON")    a=a.filter((x:any)=>x.isMilestone);
    if(filt==="NEARCRIT")   a=a.filter((x:any)=>x.totalFloat!=null&&x.totalFloat>=1&&x.totalFloat<=5);
    if(epcFilt!=='ALL')     a=a.filter((x:any)=>getEpcPhase(x.wbs||'',x.wbsPath||'').key===epcFilt);
    return a;
  },[allActivities,search,proj,filt,epcFilt,dataDate]);

  // Group by full wbsPath (preserves P6 hierarchy, not just leaf name)
  const wbsGroups=useMemo(()=>{
    const m=new Map<string,any[]>();
    shown.forEach((a:any)=>{
      const k=a.wbsPath||a.wbs||'(No WBS)';
      if(!m.has(k))m.set(k,[]);m.get(k)!.push(a);
    });
    return m;
  },[shown]);

  // Sort by P6 sequence key (wbsSortKey), falling back to wbsCode then alpha
  const sortedWbs=useMemo(()=>[...wbsGroups.keys()].sort((a,b)=>{
    const sA=wbsGroups.get(a)?.[0];const sB=wbsGroups.get(b)?.[0];
    const kA=sA?.wbsSortKey||sA?.wbsCode||a;
    const kB=sB?.wbsSortKey||sB?.wbsCode||b;
    return kA.localeCompare(kB,undefined,{numeric:true,sensitivity:'base'});
  }),[wbsGroups]);

  // Build a full WBS tree from the activity paths (synthesises parent nodes)
  type WbsNode={key:string;code:string;name:string;level:number;sortKey:string;childKeys:string[]};
  const wbsTreeMap=useMemo(()=>{
    const nodes=new Map<string,WbsNode>();
    sortedWbs.forEach(pathKey=>{
      const sample=wbsGroups.get(pathKey)?.[0];
      const nameParts=pathKey.split(' > ');
      const codeParts=(sample?.wbsCode||'').split('.').filter(Boolean);
      const sortParts=(sample?.wbsSortKey||'').split('.').filter(Boolean);
      nameParts.forEach((name,i)=>{
        const nodeKey=nameParts.slice(0,i+1).join(' > ');
        if(!nodes.has(nodeKey)){
          nodes.set(nodeKey,{
            key:nodeKey,
            code:codeParts.slice(0,i+1).join('.'),
            name,
            level:i+1,
            sortKey:sortParts.slice(0,i+1).join('.'),
            childKeys:[],
          });
        }
        if(i>0){
          const parentKey=nameParts.slice(0,i).join(' > ');
          const parent=nodes.get(parentKey);
          if(parent&&!parent.childKeys.includes(nodeKey))parent.childKeys.push(nodeKey);
        }
      });
    });
    return nodes;
  },[sortedWbs,wbsGroups]);

  // Root WBS nodes (no parent) sorted by sequence
  const wbsRoots=useMemo(()=>[...wbsTreeMap.keys()]
    .filter(k=>!k.includes(' > '))
    .sort((a,b)=>(wbsTreeMap.get(a)?.sortKey||a).localeCompare(wbsTreeMap.get(b)?.sortKey||b,undefined,{numeric:true}))
  ,[wbsTreeMap]);

  // Keep wbsByPhase for EPC filter chips only (not structural grouping)
  const wbsByPhase=useMemo(()=>{
    const map=new Map<string,string[]>();
    sortedWbs.forEach(wbs=>{
      const sample=wbsGroups.get(wbs)?.[0];
      const ph=getEpcPhase(wbs,sample?.wbsPath).key;
      if(!map.has(ph))map.set(ph,[]);map.get(ph)!.push(wbs);
    });
    return map;
  },[sortedWbs,wbsGroups]);

  // ── Gantt state + helpers ─────────────────────────────────────────────────
  const [showGantt,setShowGantt]=useState(false);

  const ganttRange=useMemo(()=>{
    if(!showGantt||!shown.length)return null;
    let minT=Infinity,maxT=-Infinity;
    for(const a of shown){
      const ts=[a.bStart,a.bFinish,a.earlyStart,a.earlyFinish,a.start,a.finish,a.remainStart,a.remainFinish]
        .map((d:any)=>d?new Date(d).getTime():NaN).filter(t=>!isNaN(t));
      if(ts.length){minT=Math.min(minT,...ts);maxT=Math.max(maxT,...ts);}
    }
    if(minT===Infinity)return null;
    const gS=new Date(minT);gS.setDate(1);gS.setHours(0,0,0,0);
    const gE=new Date(maxT);gE.setMonth(gE.getMonth()+1);gE.setDate(1);gE.setHours(0,0,0,0);
    const totalDays=Math.max(1,(gE.getTime()-gS.getTime())/86400000);
    const pxPerDay=Math.min(4,Math.max(0.35,880/totalDays));
    return{start:gS,end:gE,totalDays,pxPerDay,width:Math.ceil(totalDays*pxPerDay)};
  },[shown,showGantt]);

  const ganttMonths=useMemo(()=>{
    if(!ganttRange)return [] as {label:string;left:number;width:number}[];
    const months:{label:string;left:number;width:number}[]=[];
    const cur=new Date(ganttRange.start);
    while(cur<ganttRange.end){
      const mS=new Date(cur);
      const mE=new Date(cur.getFullYear(),cur.getMonth()+1,1);
      const left=Math.round((mS.getTime()-ganttRange.start.getTime())/86400000*ganttRange.pxPerDay);
      const mEndClamp=mE<ganttRange.end?mE:ganttRange.end;
      const mW=Math.round((mEndClamp.getTime()-mS.getTime())/86400000*ganttRange.pxPerDay);
      months.push({label:mS.toLocaleDateString('en-US',{month:'short'}),left,width:mW});
      cur.setMonth(cur.getMonth()+1);
    }
    return months;
  },[ganttRange]);

  // Year bands for the top header row (P6 style)
  const ganttYears=useMemo(()=>{
    if(!ganttRange)return [] as {label:string;left:number;width:number}[];
    const years:{label:string;left:number;width:number}[]=[];
    let yr=ganttRange.start.getFullYear();
    while(true){
      const yS=new Date(yr,0,1),yE=new Date(yr+1,0,1);
      if(yS>=ganttRange.end)break;
      const cS=yS<ganttRange.start?ganttRange.start:yS;
      const cE=yE>ganttRange.end?ganttRange.end:yE;
      const left=Math.round((cS.getTime()-ganttRange.start.getTime())/86400000*ganttRange.pxPerDay);
      const width=Math.round((cE.getTime()-cS.getTime())/86400000*ganttRange.pxPerDay);
      years.push({label:String(yr),left,width});
      yr++;
    }
    return years;
  },[ganttRange]);

  // CSS gradient for vertical month gridlines in every Gantt cell
  const ganttGridBg=useMemo(():string=>{
    if(!ganttRange||ganttMonths.length<2)return 'none';
    const stops:string[]=['transparent 0px'];
    ganttMonths.slice(1).forEach(m=>{
      const x=m.left;
      stops.push(`transparent ${x}px`,`${C.border} ${x}px`,`${C.border} ${x+1}px`,`transparent ${x+1}px`);
    });
    stops.push(`transparent ${ganttRange.width}px`);
    return `linear-gradient(to right,${stops.join(',')})`;
  },[ganttMonths,ganttRange]);

  const ganttTodayX=useMemo(()=>{
    if(!ganttRange)return -1;
    const dd=dataDate?new Date(dataDate+"T12:00:00").getTime():Date.now();
    return Math.round((dd-ganttRange.start.getTime())/86400000*ganttRange.pxPerDay);
  },[ganttRange,dataDate]);

  const renderGanttCell=useCallback((a:any)=>{
    if(!ganttRange)return null;
    const isComp=(a.pctComplete||0)>=100;
    const s=a.start||a.earlyStart||a.bStart||a.remainStart;
    const f=(isComp?a.finish:null)||a.earlyFinish||a.bFinish||a.remainFinish;
    if(!s)return null;
    const sT=new Date(s).getTime(),fT=f?new Date(f).getTime():sT+86400000;
    if(isNaN(sT))return null;
    const left=Math.max(0,Math.round((sT-ganttRange.start.getTime())/86400000*ganttRange.pxPerDay));
    const width=Math.max(a.isMilestone?0:3,Math.round((fT-sT)/86400000*ganttRange.pxPerDay));
    const clr=isComp?'#4a9eff':a.isCritical&&!a.isMilestone?C.red:C.green;
    const pct=Math.min(100,a.pctComplete||0);
    // Baseline bar (P6 bracket style)
    const blL=a.bStart?Math.max(0,Math.round((new Date(a.bStart).getTime()-ganttRange.start.getTime())/86400000*ganttRange.pxPerDay)):null;
    const blW=a.bStart&&a.bFinish?Math.max(2,Math.round((new Date(a.bFinish).getTime()-new Date(a.bStart).getTime())/86400000*ganttRange.pxPerDay)):null;
    // Float line extent
    const floatPx=a.totalFloat!=null&&a.totalFloat>0?Math.min(Math.round(a.totalFloat*ganttRange.pxPerDay),ganttRange.width-left-width):0;
    return(
      <td key="gantt" style={{padding:0,position:'relative',overflow:'hidden',width:ganttRange.width,minWidth:ganttRange.width}}>
        <div style={{position:'relative',height:34,width:ganttRange.width,backgroundColor:C.bg}}>
          {/* Data date — P6 dashed vertical line */}
          {ganttTodayX>=0&&ganttTodayX<=ganttRange.width&&(
            <div style={{position:'absolute',top:0,left:ganttTodayX,width:0,height:'100%',
              borderLeft:`2px dashed ${C.accent}`,zIndex:4,pointerEvents:'none'}}/>
          )}
          {/* Baseline bar — P6 bracket: thin border rect with end caps */}
          {blL!=null&&blW!=null&&(
            <div style={{position:'absolute',bottom:4,left:blL,width:blW,height:5,
              background:'transparent',border:'1.5px solid #555',zIndex:1}}>
              <div style={{position:'absolute',left:-1,top:-3,width:3,height:10,background:'#555'}}/>
              <div style={{position:'absolute',right:-1,top:-3,width:3,height:10,background:'#555'}}/>
            </div>
          )}
          {/* Milestone — P6 filled diamond */}
          {a.isMilestone&&(
            <div style={{position:'absolute',top:'50%',left:left-8,width:14,height:14,
              background:clr,border:`2px solid rgba(0,0,0,0.25)`,
              transform:'translateY(-50%) rotate(45deg)',borderRadius:1,zIndex:3,
              boxShadow:`0 1px 3px rgba(0,0,0,0.3)`}}/>
          )}
          {/* Activity bar — full deep solid color */}
          {!a.isMilestone&&(
            <div style={{position:'absolute',top:'17%',left,width,height:'58%',
              background:clr,border:`1.5px solid ${clr}`,borderRadius:2,
              overflow:'hidden',zIndex:2,boxShadow:`inset 0 1px 0 rgba(255,255,255,0.25),inset 0 -1px 0 rgba(0,0,0,0.15)`}}>
              {/* Remaining portion — white overlay so completed vs remaining is distinct */}
              {pct<100&&<div style={{position:'absolute',top:0,left:`${pct}%`,right:0,height:'100%',background:'rgba(255,255,255,0.38)'}}/>}
              {/* Progress boundary tick */}
              {pct>0&&pct<100&&(
                <div style={{position:'absolute',top:0,left:`${pct}%`,width:2,height:'100%',
                  background:'rgba(0,0,0,0.35)',transform:'translateX(-1px)'}}/>
              )}
            </div>
          )}
        </div>
      </td>
    );
  },[ganttRange,ganttTodayX,ganttGridBg]);
  // ─────────────────────────────────────────────────────────────────────────

  const tdBase:React.CSSProperties={padding:"6px 11px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",color:'#111111'};

  // Per-row derived values + per-column cell renderer, shared by both the WBS-tree
  // and flat render paths below so a single {arVisible.map(...)} drives column
  // order/visibility in both — keeping header, body and column-picker in sync.
  const computeRowDerived=(a:any)=>{
    // "today" here is the active schedule's own effective Data Date (never
    // the real-world date) — used below for the "should have finished"
    // amber flag on the Baseline Finish column.
    const today=ddParsed;
    const bf=a.bFinish?new Date(a.bFinish):null,af=a.finish?new Date(a.finish):null;
    const bs=a.bStart?new Date(a.bStart):null,as_=a.start?new Date(a.start):null;
    const isComp=(a.pctComplete||0)>=100;
    const fcS=isComp?(as_||null):a.projectedStart instanceof Date?a.projectedStart:null;
    const fcF=isComp?(af||null):a.projectedFinish instanceof Date?a.projectedFinish:null;
    const fv=bf&&af?Math.round((af.getTime()-bf.getTime())/86400000):null;
    const sv=bs&&as_?Math.round((as_.getTime()-bs.getTime())/86400000):null;
    return{today,bf,af,bs,as_,fcS,fcF,fv,sv};
  };
  const arFmtV=(v:number|null)=>v===null?'—':`${v>0?'+':''}${v}d`;
  const arVarC=(v:number|null)=>!v?'#111111':v>0?C.red:C.green;
  const renderArCell=(key:CK,a:any,d:ReturnType<typeof computeRowDerived>,codeStyle?:React.CSSProperties):React.ReactNode=>{
    const{today,bf,af,bs,as_,fcS,fcF,fv,sv}=d;
    switch(key){
      case'code':return <td key={key} style={{...(codeStyle||tdBase),color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10}}>{a.code}</td>;
      case'name':return <td key={key} style={{...tdBase}} title={a.name}>{a.isCritical&&!a.isMilestone&&<span style={{color:C.red,marginRight:3,fontSize:9}}>●</span>}{a.isMilestone&&<span style={{color:C.purple,marginRight:3,fontSize:9}}>◆</span>}<span style={{color:'#111111'}}>{a.name}</span></td>;
      case'project':return <td key={key} style={{...tdBase,color:'#111111',fontSize:11}} title={a.projectName||a.projectId}>{a.projectName||a.projectId}</td>;
      case'bStart':return <td key={key} style={{...tdBase,color:'#111111',fontSize:11}}>{fmtDate(bs)}</td>;
      case'bFinish':return <td key={key} style={{...tdBase,color:bf&&today&&bf<today&&(a.pctComplete||0)<100?C.amber:'#111111',fontSize:11}}>{fmtDate(bf)}</td>;
      case'fcStart':return <td key={key} style={{...tdBase,fontSize:11,color:fcS&&bs?fcS<bs?C.green:fcS>bs?C.amber:'#111111':'#111111'}}>{fmtDate(fcS)}</td>;
      case'fcFinish':return <td key={key} style={{...tdBase,fontSize:11,fontWeight:fcF&&bf&&fcF>bf?600:400,color:fcF&&bf?fcF<bf?C.green:fcF>bf?C.red:'#111111':'#111111'}}>{fmtDate(fcF)}</td>;
      case'sv':return <td key={key} style={{...tdBase,color:arVarC(sv),fontFamily:"'DM Mono',monospace",fontWeight:sv&&sv>0?700:400,textAlign:"right"}}>{arFmtV(sv)}</td>;
      case'fv':return <td key={key} style={{...tdBase,color:arVarC(fv),fontFamily:"'DM Mono',monospace",fontWeight:fv&&fv>0?700:400,textAlign:"right"}}>{arFmtV(fv)}</td>;
      case'dur':return <td key={key} style={{...tdBase,color:'#111111',textAlign:"right"}}>{a.dur||"—"}</td>;
      case'float':return <td key={key} style={{...tdBase,color:a.totalFloat==null?'#111111':a.totalFloat<0?C.red:a.totalFloat===0?C.amber:a.totalFloat<=5?C.amber:C.green,textAlign:"right",fontFamily:"'DM Mono',monospace",fontWeight:a.totalFloat<0?700:400}}>{a.totalFloat==null?"—":a.totalFloat}</td>;
      case'pct':return <td key={key} style={{...tdBase}}><div style={{display:"flex",alignItems:"center",gap:5}}><div style={{flex:1,background:C.border,borderRadius:3,height:4}}><div style={{width:`${a.pctComplete||0}%`,background:a.pctComplete>=100?C.green:C.accent,borderRadius:3,height:4}}/></div><span style={{color:'#111111',fontSize:10}}>{a.pctComplete||0}%</span></div></td>;
      case'status':return <td key={key} style={{...tdBase}}><span style={{fontSize:10,padding:"2px 7px",borderRadius:4,background:a.pctComplete>=100?"rgba(0,229,160,0.12)":a.start?"rgba(0,200,240,0.12)":"rgba(100,116,139,0.12)",color:a.pctComplete>=100?C.green:a.start?C.accent:C.muted2}}>{a.pctComplete>=100?"Complete":a.start?"Active":"Not Started"}</span></td>;
      default:return null;
    }
  };

  const navigateToAct=useCallback((actId:string)=>{
    const idx=shown.findIndex((a:any)=>(a.id||a.code)===actId);
    if(idx>=0){setPage(Math.floor(idx/PAGE));setExpandedActId(actId);setTimeout(()=>document.querySelector(`[data-actid="${CSS.escape(actId)}"]`)?.scrollIntoView({behavior:"smooth",block:"center"}),60);}
    else{setSearch("");setFilt("ALL");setPage(0);setExpandedActId(actId);}
  },[shown]);

  return(<Sec title="Activity Register" icon="📋">
    <div style={{display:"flex",gap:7,marginBottom:10,flexWrap:"wrap"}}>
      <input value={search} onChange={e=>{setSearch(e.target.value);setPage(0);if(onSearchChange)onSearchChange(e.target.value);}} placeholder="Search by name or ID…" style={{flex:"2 1 180px",background:C.card,border:`1px solid ${C.border}`,color:C.text,borderRadius:8,padding:"7px 11px",fontSize:13,fontFamily:"inherit",outline:"none"}}/>
      <select value={proj} onChange={e=>{setProj(e.target.value);setPage(0);}} style={{flex:"1 1 140px",background:C.card,border:`1px solid ${C.border}`,color:C.text,borderRadius:8,padding:"7px 11px",fontSize:13,fontFamily:"inherit",cursor:"pointer"}}>
        <option value="ALL">All Projects</option>{projects.map((p:any)=><option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
      {(["ALL","CRITICAL","OVERDUE","NOTSTART","NEGFLOAT","NEARCRIT","COMPLETE","INPROG","MILESTON"]).map(f=>{
        const LABELS:any={ALL:"All",CRITICAL:"Critical",OVERDUE:"Overdue",NOTSTART:"Not Started",NEGFLOAT:"Neg Float",NEARCRIT:"Near-Critical",COMPLETE:"Activities Completed",INPROG:"In Progress",MILESTON:"Milestones"};
        return(<button key={f} onClick={()=>{setFilt(f);setPage(0);}} style={{background:filt===f?"rgba(0,200,240,0.11)":"transparent",border:`1px solid ${filt===f?C.accent:C.border}`,color:filt===f?C.accent:C.muted2,borderRadius:8,padding:"7px 11px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:700,whiteSpace:"nowrap"}}>{LABELS[f]}</button>);
      })}
      {/* EPC phase filter chips */}
      <div style={{display:"flex",alignItems:"center",gap:5,borderLeft:`1px solid ${C.border}`,paddingLeft:10}}>
        <span style={{fontSize:10,color:C.muted,whiteSpace:"nowrap"}}>EPC:</span>
        {(['ALL',...EPC_ORDER]).map(k=>{
          const ph=k==='ALL'?null:EPC_PHASES[k];
          // Only show chip if phase has activities (or ALL)
          if(k!=='ALL'&&!allActivities.some((a:any)=>getEpcPhase(a.wbs||'',a.wbsPath||'').key===k))return null;
          const active=epcFilt===k;
          return(<button key={k} onClick={()=>{setEpcFilt(k);setPage(0);}}
            style={{background:active?`${ph?ph.color:C.accent}18`:"transparent",border:`1px solid ${active?ph?ph.color:C.accent:C.border}`,color:active?ph?ph.color:C.accent:C.muted2,borderRadius:7,padding:"5px 9px",cursor:"pointer",fontSize:11,fontFamily:"inherit",fontWeight:700,whiteSpace:"nowrap",display:"flex",alignItems:"center",gap:4}}
            title={ph?ph.label:'All phases'}>
            {ph&&<span style={{fontSize:11}}>{ph.icon}</span>}
            <span>{ph?ph.short:'All'}</span>
          </button>);
        })}
      </div>
      <div style={{padding:"7px 11px",color:C.muted2,fontSize:12,alignSelf:"center"}}>{shown.length.toLocaleString()}</div>
      <button onClick={()=>setColW(DEFAULT_W)} title="Reset column widths" style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:8,padding:"7px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>↔ Reset</button>
      <button onClick={()=>{setGroupByWBS(p=>!p);setCollapsedWbs(new Set());setCollapsedPhases(new Set());}} style={{background:groupByWBS?`${C.accent}18`:"transparent",border:`1px solid ${groupByWBS?C.accent:C.border}`,color:groupByWBS?C.accent:C.muted2,borderRadius:8,padding:"7px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit",whiteSpace:"nowrap"}} title="Group activities by WBS with EPC phases">⊞ WBS</button>
      {groupByWBS&&(
        <div ref={wbsMenuRef} style={{position:"relative"}}>
          <button onClick={()=>setWbsMenuOpen(o=>!o)} title="WBS column options"
            style={{background:hideEmptyWbs?`${C.accent}18`:"transparent",border:`1px solid ${hideEmptyWbs||wbsMenuOpen?C.accent:C.border}`,color:hideEmptyWbs?C.accent:C.muted2,borderRadius:8,padding:"7px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit",whiteSpace:"nowrap",display:"flex",alignItems:"center",gap:5}}>
            WBS options <span style={{fontSize:9,opacity:0.8}}>▾</span>
          </button>
          {wbsMenuOpen&&(
            <div style={{position:"absolute",top:"calc(100% + 5px)",left:0,zIndex:500,background:C.card2,border:`1px solid ${C.border}`,borderRadius:9,padding:"8px 10px",boxShadow:"0 8px 32px rgba(0,0,0,0.55)",whiteSpace:"nowrap"}}>
              <label style={{display:"flex",alignItems:"center",gap:7,cursor:"pointer",fontSize:12,color:C.text}}>
                <input type="checkbox" checked={hideEmptyWbs} onChange={e=>setHideEmptyWbs(e.target.checked)}/>
                Hide empty WBS
              </label>
              <div style={{fontSize:10,color:C.muted2,marginTop:4,maxWidth:220,whiteSpace:"normal"}}>Hides WBS folders with no activities of their own under the current filter — their sub-folders still show.</div>
            </div>
          )}
        </div>
      )}
      <button onClick={()=>setShowGantt(p=>!p)} style={{background:showGantt?`${C.accent}18`:"transparent",border:`1px solid ${showGantt?C.accent:C.border}`,color:showGantt?C.accent:C.muted2,borderRadius:8,padding:"7px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit",whiteSpace:"nowrap"}} title="Toggle Gantt chart">📅 Gantt</button>
      {(()=>{
        const arCell=(a:any,k:string)=>{
          const bs=a.bStart?new Date(a.bStart):null,bf=a.bFinish?new Date(a.bFinish):null,as_=a.start?new Date(a.start):null,af=a.finish?new Date(a.finish):null;
          const isComp=(a.pctComplete||0)>=100;
          const fcS=isComp?(as_||null):a.projectedStart instanceof Date?a.projectedStart:null;
          const fcF=isComp?(af||null):a.projectedFinish instanceof Date?a.projectedFinish:null;
          const sv=bs&&as_?Math.round((as_.getTime()-bs.getTime())/86400000):null;
          const fv=bf&&af?Math.round((af.getTime()-bf.getTime())/86400000):null;
          const fmtV=(v:number|null)=>v===null?'':v>0?`+${v}d`:`${v}d`;
          switch(k){case'code':return a.code||'';case'name':return a.name||'';case'project':return a.projectName||a.projectId||'';case'bStart':return fmtDateExport(bs);case'bFinish':return fmtDateExport(bf);case'fcStart':return fmtDateExport(fcS);case'fcFinish':return fmtDateExport(fcF);case'sv':return fmtV(sv);case'fv':return fmtV(fv);case'dur':return String(a.dur||'');case'float':return a.totalFloat==null?'':String(a.totalFloat);case'pct':return`${a.pctComplete||0}%`;case'status':return a.pctComplete>=100?'Complete':a.start?'Active':'Not Started';default:return'';}
        };
        const arTitle=`Activity Register${filt!=='ALL'?' — '+filt:''}`;
        return(<>
          <button type="button" onClick={()=>printTable(shown,arVisible,arTitle,'activity-register',arCell)} style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:7,padding:"5px 11px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>🖨 PDF</button>
          <button type="button" onClick={()=>downloadCSV(shown,arVisible,'activity-register',arCell)} style={{background:`${C.green}14`,border:`1px solid ${C.green}40`,color:C.green,borderRadius:7,padding:"5px 11px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>📊 Excel</button>
        </>);
      })()}
      <ColPickerDialog allCols={COL_DEFS} cols={arCols}/>
    </div>
    {editingAct&&onUpdateActivity&&<ActivityEditModal act={editingAct} onSave={onUpdateActivity} onClose={()=>setEditingAct(null)}/>}
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:"auto",maxHeight:"calc(100vh - 230px)"}}>
      <table style={{tableLayout:"fixed",borderCollapse:"collapse",fontSize:12,width:arVisible.reduce((s,[k])=>s+(colW[k as CK]||0),0)+36}}>
        <colgroup><col style={{width:36}}/>{arVisible.map(([k])=><col key={k} style={{width:colW[k as CK]}}/>)}{showGantt&&ganttRange&&<col key="gantt-col" style={{width:ganttRange.width}}/>}</colgroup>
        <thead style={{position:"sticky",top:0,zIndex:1,background:C.card}}>
          <tr style={{background:"rgba(212,168,67,0.05)"}}>
            <th style={{width:36,padding:"8px 4px"}}></th>
            {arVisible.map(([key,label])=>(
              <RTh key={key} colKey={key} label={label} colW={colW} onResizeStart={onResizeStart} onReorder={arCols.reorder}/>
            ))}
            {showGantt&&ganttRange&&(
              <th key="gantt-hdr" style={{padding:0,position:'relative',minWidth:ganttRange.width,width:ganttRange.width,background:C.card,overflow:'hidden',verticalAlign:'bottom',borderLeft:`1px solid ${C.border}`}}>
                <div style={{position:'relative',height:50,width:ganttRange.width}}>
                  {/* Year row — top 20px */}
                  {ganttYears.map((y,i)=>(
                    <div key={`y${i}`} style={{position:'absolute',left:y.left,width:y.width,top:0,height:20,borderRight:`1px solid ${C.border}`,borderBottom:`1px solid ${C.border}`,padding:'0 6px',display:'flex',alignItems:'center',overflow:'hidden',background:`${C.panel}70`}}>
                      <span style={{fontSize:11,fontWeight:800,color:C.text,fontFamily:"'DM Mono',monospace",letterSpacing:'0.04em'}}>{y.label}</span>
                    </div>
                  ))}
                  {/* Month row — bottom 30px */}
                  {ganttMonths.map((m,i)=>(
                    <div key={`m${i}`} style={{position:'absolute',left:m.left,width:m.width,top:20,height:30,borderRight:`1px solid ${C.border}`,padding:'0 4px',display:'flex',alignItems:'center',overflow:'hidden',whiteSpace:'nowrap',backgroundImage:ganttGridBg}}>
                      <span style={{fontSize:9,fontWeight:700,color:C.muted,fontFamily:"'DM Mono',monospace"}}>{m.label}</span>
                    </div>
                  ))}
                  {/* Data date — P6 dashed vertical line */}
                  {ganttTodayX>=0&&ganttTodayX<=ganttRange.width&&(
                    <div style={{position:'absolute',top:0,left:ganttTodayX,width:0,height:'100%',borderLeft:`2px dashed ${C.accent}`,zIndex:5}}/>
                  )}
                </div>
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {groupByWBS ? (
            // ── Native P6 WBS hierarchy tree (matches file structure exactly) ──
            (()=>{
              const colSpanAll=arVisible.length+1+(showGantt&&ganttRange?1:0);
              const renderActRow=(a:any)=>{
                const d=computeRowDerived(a);
                const actId=a.id||a.code;const isExp=expandedActId===actId;
                const isEdited=!!(activityUpdates?.[actId]?._edited);
                const tdI:React.CSSProperties={...tdBase,paddingLeft:22};
                return(
                  <Fragment key={actId}>
                    <tr data-actid={actId} onClick={()=>toggleAct(actId)} style={{borderTop:`1px solid ${C.border}`,cursor:"pointer",background:isExp?`${C.accent}0a`:isEdited?`${C.amber}07`:a.isCritical&&!a.isMilestone?"rgba(255,87,87,0.03)":"transparent"}} title="Click to view predecessors & successors">
                      <td style={{padding:"0 4px",textAlign:"center",width:36}} onClick={e=>e.stopPropagation()}>
                        {onUpdateActivity&&<button onClick={()=>setEditingAct(a)} title="Update activity" style={{background:isEdited?`${C.amber}20`:"transparent",border:`1px solid ${isEdited?C.amber:C.border}`,color:isEdited?C.amber:C.muted2,borderRadius:5,padding:"2px 5px",cursor:"pointer",fontSize:12,lineHeight:1}}>✎</button>}
                      </td>
                      {arVisible.map(([key])=>renderArCell(key as CK,a,d,key==='code'?tdI:undefined))}
                      {showGantt&&renderGanttCell(a)}
                    </tr>
                    {isExp&&<LogicPanel a={a} actMap={actMap} colSpan={colSpanAll} onNavigate={navigateToAct}/>}
                  </Fragment>
                );
              };
              const renderNode=(nodeKey:string):React.ReactNode=>{
                const node=wbsTreeMap.get(nodeKey);
                if(!node)return null;
                const acts=wbsGroups.get(nodeKey)||[];
                if(hideEmptyWbs&&acts.length===0){
                  // Skip this folder's own row, but keep its non-empty sub-folders/activities.
                  const childKeys=[...node.childKeys].sort((ca,cb)=>(wbsTreeMap.get(ca)?.sortKey||ca).localeCompare(wbsTreeMap.get(cb)?.sortKey||cb,undefined,{numeric:true,sensitivity:'base'}));
                  return <Fragment key={`tree-${nodeKey}`}>{childKeys.map(ck=>renderNode(ck))}</Fragment>;
                }
                const isCollapsed=collapsedWbs.has(nodeKey);
                const hasCrit=acts.some((a:any)=>a.isCritical&&!a.isMilestone);
                const done=acts.filter((a:any)=>(a.pctComplete||0)>=100).length;
                const donePct=acts.length?Math.round(done/acts.length*100):null;
                const indentPx=(node.level-1)*20+10;
                const isRoot=node.level===1;
                const sortedChildKeys=[...node.childKeys].sort((ca,cb)=>(wbsTreeMap.get(ca)?.sortKey||ca).localeCompare(wbsTreeMap.get(cb)?.sortKey||cb,undefined,{numeric:true,sensitivity:'base'}));
                const borderAccent=isRoot?C.accent:node.level===2?C.muted:C.border;
                return(
                  <Fragment key={`tree-${nodeKey}`}>
                    <tr onClick={()=>toggleWbs(nodeKey)} onContextMenu={e=>{e.preventDefault();setWbsCtx({x:e.clientX,y:e.clientY});}}
                      style={{background:isRoot?C.panel:node.level===2?`${C.panel}80`:`${C.panel}40`,borderTop:isRoot?`2px solid ${C.border}`:`1px solid ${C.border}`,cursor:'pointer'}}>
                      <td colSpan={colSpanAll} style={{padding:'5px 10px',paddingLeft:indentPx,borderLeft:`${isRoot?4:3}px solid ${borderAccent}`}}>
                        <div style={{display:'flex',alignItems:'center',gap:8}}>
                          <span style={{fontSize:9,color:C.muted,display:'inline-block',transform:isCollapsed?'rotate(-90deg)':'rotate(0deg)',transition:'transform .15s',flexShrink:0}}>▼</span>
                          {node.code&&<span style={{fontSize:10,color:C.muted,fontFamily:"'DM Mono',monospace",background:C.border,padding:'1px 6px',borderRadius:3,flexShrink:0}}>{node.code}</span>}
                          <span style={{fontWeight:isRoot?800:node.level===2?700:600,fontSize:isRoot?13:node.level===2?12:11,color:C.text}}>{node.name}</span>
                          {acts.length>0&&<span style={{fontSize:10,color:C.muted,flexShrink:0}}>({acts.length})</span>}
                          {hasCrit&&<span style={{fontSize:9,padding:'1px 5px',borderRadius:3,background:`${C.red}15`,color:C.red,fontWeight:700,flexShrink:0}}>Critical</span>}
                          {donePct!==null&&donePct>0&&<><div style={{width:50,background:C.border,borderRadius:3,height:4,flexShrink:0}}><div style={{width:`${donePct}%`,background:donePct===100?C.green:C.accent,borderRadius:3,height:4}}/></div><span style={{fontSize:10,color:C.muted}}>{donePct}%</span></>}
                        </div>
                      </td>
                    </tr>
                    {!isCollapsed&&acts.map((a:any)=>renderActRow(a))}
                    {!isCollapsed&&sortedChildKeys.map(ck=>renderNode(ck))}
                  </Fragment>
                );
              };
              return wbsRoots.map(k=>renderNode(k));
            })()
          ) : (
          // ── Flat paginated view ──
            shown.slice(page*PAGE,(page+1)*PAGE).map((a:any)=>{
              const d=computeRowDerived(a);
              const actId=a.id||a.code;const isExp=expandedActId===actId;
              const isEdited=!!(activityUpdates?.[actId]?._edited);
              return(<Fragment key={actId}>
                <tr data-actid={actId} onClick={()=>toggleAct(actId)} style={{borderTop:`1px solid ${C.border}`,cursor:"pointer",background:isExp?`${C.accent}0a`:isEdited?`${C.amber}07`:a.isCritical&&!a.isMilestone?"rgba(255,87,87,0.03)":"transparent"}} title="Click to view predecessors & successors">
                  <td style={{padding:"0 4px",textAlign:"center",width:36}} onClick={e=>e.stopPropagation()}>
                    {onUpdateActivity&&<button onClick={()=>setEditingAct(a)} title="Update activity" style={{background:isEdited?`${C.amber}20`:"transparent",border:`1px solid ${isEdited?C.amber:C.border}`,color:isEdited?C.amber:C.muted2,borderRadius:5,padding:"2px 5px",cursor:"pointer",fontSize:12,lineHeight:1}}>✎</button>}
                  </td>
                  {arVisible.map(([key])=>renderArCell(key as CK,a,d))}
                  {showGantt&&renderGanttCell(a)}
                </tr>
                {isExp&&<LogicPanel a={a} actMap={actMap} colSpan={arVisible.length+1+(showGantt&&ganttRange?1:0)} onNavigate={navigateToAct}/>}
              </Fragment>);
            })
          )}
        </tbody>
      </table>
    </div>
    {!groupByWBS&&shown.length>PAGE&&<div style={{display:"flex",gap:8,justifyContent:"center",marginTop:10,alignItems:"center"}}>
      <button onClick={()=>setPage(p=>Math.max(0,p-1))} disabled={page===0} style={{background:C.card,border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"5px 13px",cursor:"pointer",fontSize:12}}>← Prev</button>
      <span style={{color:C.muted2,fontSize:12}}>Page {page+1} of {Math.ceil(shown.length/PAGE)}</span>
      <button onClick={()=>setPage(p=>Math.min(Math.ceil(shown.length/PAGE)-1,p+1))} disabled={(page+1)*PAGE>=shown.length} style={{background:C.card,border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"5px 13px",cursor:"pointer",fontSize:12}}>Next →</button>
    </div>}

    {/* WBS context menu */}
    {wbsCtx&&(()=>{
      const maxLvl=Math.min(8,Math.max(1,...sortedWbs.map(getWbsLevel)));
      const item=(label:string,action:()=>void,sep=false)=>(
        <div key={label}>
          {sep&&<div style={{height:1,background:C.border,margin:"3px 0"}}/>}
          <div onMouseDown={e=>{e.stopPropagation();action();setWbsCtx(null);}}
            style={{padding:"7px 14px",cursor:"pointer",fontSize:12,color:C.text,borderRadius:5,whiteSpace:"nowrap"}}
            onMouseEnter={e=>(e.currentTarget.style.background=`${C.accent}18`)}
            onMouseLeave={e=>(e.currentTarget.style.background="transparent")}>
            {label}
          </div>
        </div>
      );
      const levels=Array.from({length:maxLvl},(_,i)=>i+1);
      return(
        <div onMouseDown={e=>e.stopPropagation()} style={{
          position:"fixed",left:wbsCtx.x,top:wbsCtx.y,zIndex:9000,
          background:C.card2,border:`1px solid ${C.border}`,borderRadius:9,
          padding:"4px",boxShadow:"0 8px 32px rgba(0,0,0,0.55)",minWidth:190,
        }}>
          <div style={{padding:"5px 14px 3px",fontSize:10,fontWeight:700,color:C.muted,textTransform:"uppercase",letterSpacing:".07em"}}>WBS View</div>
          {item("▼ Expand All",()=>setCollapsedWbs(new Set()))}
          {item("▲ Collapse All",()=>setCollapsedWbs(new Set(sortedWbs)))}
          <div style={{height:1,background:C.border,margin:"3px 0"}}/>
          <div style={{padding:"5px 14px 3px",fontSize:10,fontWeight:700,color:C.muted,textTransform:"uppercase",letterSpacing:".07em"}}>Collapse to Level</div>
          {levels.map(lvl=>item(`Level ${lvl}${lvl===1?' (top)':''}`,()=>setCollapsedWbs(new Set(sortedWbs.filter((w:string)=>getWbsLevel(w)>lvl)))))}
        </div>
      );
    })()}
  </Sec>);
}

// ─── SCHEDULE DIFF (unchanged logic, uses processFileViaAPI) ──────────────────
function ScheduleDiff({files}:any){
  const [slotA,setSlotA]=useState<any>(null);
  const [slotB,setSlotB]=useState<any>(null);
  const [loadingA,setLoadingA]=useState(false);
  const [loadingB,setLoadingB]=useState(false);
  const [errA,setErrA]=useState("");
  const [errB,setErrB]=useState("");
  const refA=useRef<HTMLInputElement>(null),refB=useRef<HTMLInputElement>(null);

  const loadFromFile=async(file:File,setSlot:any,setLoading:any,setErr:any)=>{
    setLoading(true);setErr("");
    try{const r=await processFileViaAPI(file);if(r)setSlot(r);else setErr("Unsupported file type");}
    catch(e:any){setErr(e.message);}
    setLoading(false);
  };
  const loadFromExisting=(fileId:any,setSlot:any)=>{const f=files.find((x:any)=>x.id===fileId);if(f)setSlot(f);};

  const diff=useMemo(()=>{
    if(!slotA||!slotB)return null;
    const actsA=slotA.activities,actsB=slotB.activities;
    const mapA:any={},mapB:any={};
    actsA.forEach((a:any)=>mapA[a.code]=a);
    actsB.forEach((a:any)=>mapB[a.code]=a);
    const allCodes=new Set([...Object.keys(mapA),...Object.keys(mapB)]);
    const added:any[]=[],removed:any[]=[],changed:any[]=[],unchanged:any[]=[];
    allCodes.forEach(code=>{
      const a=mapA[code],b=mapB[code];
      if(!a){added.push(b);return;}if(!b){removed.push(a);return;}
      const changes:any[]=[];
      const finA=a.bFinish||a.finish,finB=b.bFinish||b.finish,startA=a.bStart||a.start,startB=b.bStart||b.start;
      if(finA&&finB&&Math.abs(finA-finB)>86400000){const dd=Math.round((finB-finA)/86400000);changes.push({field:"Finish Date",from:finA,to:finB,daysDiff:dd,type:dd>0?"delay":"advance"});}
      if(startA&&startB&&Math.abs(startA-startB)>86400000){const dd=Math.round((startB-startA)/86400000);changes.push({field:"Start Date",from:startA,to:startB,daysDiff:dd,type:dd>0?"delay":"advance"});}
      if(Math.abs((a.dur||0)-(b.dur||0))>0.1)changes.push({field:"Duration",from:a.dur,to:b.dur,delta:(b.dur||0)-(a.dur||0),type:(b.dur||0)>(a.dur||0)?"increase":"decrease"});
      if(Math.abs((a.pctComplete||0)-(b.pctComplete||0))>=1)changes.push({field:"% Complete",from:a.pctComplete,to:b.pctComplete,delta:(b.pctComplete||0)-(a.pctComplete||0),type:(b.pctComplete||0)>(a.pctComplete||0)?"increase":"decrease"});
      if(Math.abs((a.totalFloat||0)-(b.totalFloat||0))>=0.1)changes.push({field:"Total Float",from:a.totalFloat,to:b.totalFloat,delta:(b.totalFloat||0)-(a.totalFloat||0),type:(b.totalFloat||0)<(a.totalFloat||0)?"worsen":"improve"});
      if(a.isCritical!==b.isCritical)changes.push({field:"Critical Status",from:a.isCritical?"Critical":"Non-Critical",to:b.isCritical?"Critical":"Non-Critical",type:b.isCritical?"became_critical":"became_noncritical"});
      if(changes.length>0)changed.push({code,nameA:a.name,nameB:b.name,a,b,changes});
      else unchanged.push(code);
    });
    const totalA=actsA.length,totalB=actsB.length;
    const critA=actsA.filter((a:any)=>a.isCritical&&!a.isMilestone).length,critB=actsB.filter((a:any)=>a.isCritical&&!a.isMilestone).length;
    const durA=actsA.reduce((s:number,a:any)=>s+(a.dur||0),0),durB=actsB.reduce((s:number,a:any)=>s+(a.dur||0),0);
    const earnA=actsA.reduce((s:number,a:any)=>s+(a.dur||0)*((a.pctComplete||0)/100),0),earnB=actsB.reduce((s:number,a:any)=>s+(a.dur||0)*((a.pctComplete||0)/100),0);
    const pctA=durA>0?(earnA/durA)*100:0,pctB=durB>0?(earnB/durB)*100:0;
    const negA=actsA.filter((a:any)=>a.totalFloat<0).length,negB=actsB.filter((a:any)=>a.totalFloat<0).length;
    // "Overdue" = should-have-finished, evaluated against EACH slot's own
    // effective Data Date — never today's date, and never one slot's date
    // applied to the other. Unavailable (not zero) when a slot has no
    // detected/confirmed Data Date (e.g. a raw upload here that never went
    // through Import Preview).
    const ddA=parseDate(slotA.dataDate),ddB=parseDate(slotB.dataDate);
    const overdueA=ddA?actsA.filter((a:any)=>{const f=a.bFinish||a.finish;return f&&f<ddA&&a.pctComplete<100&&!a.isMilestone;}).length:null;
    const overdueB=ddB?actsB.filter((a:any)=>{const f=a.bFinish||a.finish;return f&&f<ddB&&a.pctComplete<100&&!a.isMilestone;}).length:null;
    const compA=actsA.filter((a:any)=>a.pctComplete>=100).length,compB=actsB.filter((a:any)=>a.pctComplete>=100).length;
    const delayBuckets:any={"Advance >14d":0,"Advance 1-14d":0,"No Change":0,"Delay 1-14d":0,"Delay 15-30d":0,"Delay >30d":0};
    changed.forEach(c=>{const fd=c.changes.find((x:any)=>x.field==="Finish Date");if(!fd){delayBuckets["No Change"]++;return;}const d=fd.daysDiff;if(d<-14)delayBuckets["Advance >14d"]++;else if(d<0)delayBuckets["Advance 1-14d"]++;else if(d===0)delayBuckets["No Change"]++;else if(d<=14)delayBuckets["Delay 1-14d"]++;else if(d<=30)delayBuckets["Delay 15-30d"]++;else delayBuckets["Delay >30d"]++;});
    unchanged.forEach(()=>delayBuckets["No Change"]++);
    const delayHist=Object.entries(delayBuckets).map(([range,count])=>({range,count,fill:range.includes("Advance")?C.green:range==="No Change"?C.muted2:range.includes(">30")?C.red:C.amber}));
    const wbsChanges:any={};changed.forEach(c=>{const w=c.a.wbs||c.b.wbs||"Unassigned";if(!wbsChanges[w])wbsChanges[w]={wbs:w,changed:0,delayed:0,advanced:0};wbsChanges[w].changed++;const fd=c.changes.find((x:any)=>x.field==="Finish Date");if(fd){if(fd.daysDiff>0)wbsChanges[w].delayed++;else if(fd.daysDiff<0)wbsChanges[w].advanced++;}});
    const wbsData=Object.values(wbsChanges).sort((a:any,b:any)=>b.changed-a.changed).slice(0,10);
    const floatShift=changed.filter(c=>c.changes.some((x:any)=>x.field==="Total Float")).slice(0,20).map(c=>{const fc=c.changes.find((x:any)=>x.field==="Total Float");return{name:c.nameA.slice(0,22)+(c.nameA.length>22?"…":""),before:Math.round(c.a.totalFloat||0),after:Math.round(c.b.totalFloat||0),delta:Math.round(fc.delta)};}).sort((a:any,b:any)=>a.delta-b.delta);
    const pctShiftByWbs:any={};
    actsA.forEach((a:any)=>{const w=a.wbs||"Unassigned";if(!pctShiftByWbs[w])pctShiftByWbs[w]={wbs:w,before:0,after:0,n:0};pctShiftByWbs[w].before+=a.pctComplete||0;pctShiftByWbs[w].n++;});
    actsB.forEach((a:any)=>{const w=a.wbs||"Unassigned";if(!pctShiftByWbs[w])pctShiftByWbs[w]={wbs:w,before:0,after:0,n:0};pctShiftByWbs[w].after+=a.pctComplete||0;});
    const pctShiftData=Object.values(pctShiftByWbs).filter((x:any)=>x.n>0).map((x:any)=>({wbs:x.wbs,before:Math.round(x.before/x.n),after:Math.round(x.after/x.n)})).sort((a:any,b:any)=>b.after-a.after).slice(0,10);
    const becameCritical=changed.filter(c=>c.changes.some((x:any)=>x.type==="became_critical"));
    const becameNonCritical=changed.filter(c=>c.changes.some((x:any)=>x.type==="became_noncritical"));
    return{added,removed,changed,unchanged,totalA,totalB,critA,critB,pctA,pctB,negA,negB,overdueA,overdueB,compA,compB,delayHist,wbsData,floatShift,pctShiftData,becameCritical,becameNonCritical};
  },[slotA,slotB]);

  const Slot=({label,color,slot,setSlot,loading,err,inputRef,setLoading,setErr}:any)=>(
    <div style={{flex:"1 1 300px",background:C.card,border:`2px solid ${slot?color:C.border}`,borderRadius:14,padding:20,minWidth:0}}>
      <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:14}}>
        <div style={{width:10,height:10,borderRadius:"50%",background:color}}/>
        <div style={{fontSize:13,fontWeight:700,color,textTransform:"uppercase",letterSpacing:"0.08em"}}>{label}</div>
        {slot&&<button onClick={()=>setSlot(null)} style={{marginLeft:"auto",background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"3px 9px",cursor:"pointer",fontSize:11}}>✕ Clear</button>}
      </div>
      {!slot?(
        <div>
          {files.length>0&&(<><div style={{fontSize:11,color:C.muted,marginBottom:6,textTransform:"uppercase",letterSpacing:"0.06em"}}>From loaded files</div>
            <div style={{display:"flex",flexDirection:"column",gap:5,marginBottom:14}}>
              {files.map((f:any,i:number)=><button key={f.id} onClick={()=>loadFromExisting(f.id,setSlot)} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:8,padding:"7px 12px",cursor:"pointer",fontSize:12,fontFamily:"inherit",textAlign:"left",display:"flex",alignItems:"center",gap:8,transition:"all 0.13s"}} onMouseEnter={e=>{(e.currentTarget as HTMLElement).style.borderColor=color;(e.currentTarget as HTMLElement).style.color=C.text;}} onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.borderColor=C.border;(e.currentTarget as HTMLElement).style.color=C.muted2;}}>
                <div style={{width:6,height:6,borderRadius:"50%",background:PROJ_COLORS[i%PROJ_COLORS.length],flexShrink:0}}/>
                <span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{f.name}</span>
                <span style={{fontSize:10,color:C.muted}}>{f.actCount} acts</span>
              </button>)}
            </div>
            <div style={{fontSize:11,color:C.muted,marginBottom:6,textTransform:"uppercase",letterSpacing:"0.06em"}}>Or upload a new file</div>
          </>)}
          <input ref={inputRef} type="file" accept=".xer,.xlsx,.xls,.csv,.xml,.pdf" onChange={e=>{if(e.target.files?.[0])loadFromFile(e.target.files[0],setSlot,setLoading,setErr);(e.target as HTMLInputElement).value="";}} style={{display:"none"}}/>
          <button onClick={()=>inputRef.current?.click()} style={{width:"100%",background:`${color}10`,border:`1px dashed ${color}`,color,borderRadius:8,padding:"10px",cursor:"pointer",fontSize:13,fontFamily:"inherit",fontWeight:600}}>{loading?"Loading…":"+ Upload File"}</button>
          {err&&<div style={{marginTop:8,color:C.amber,fontSize:11}}>{err}</div>}
        </div>
      ):(
        <div>
          <div style={{fontSize:15,fontWeight:700,color:C.text,marginBottom:4}}>{slot.name}</div>
          <div style={{fontSize:11,color:C.muted2,marginBottom:10}}>{slot.actCount.toLocaleString()} activities · {slot.source.toUpperCase()}</div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:8}}>
            {[{l:"Complete",v:`${slot.activities.filter((a:any)=>a.pctComplete>=100).length}`},{l:"Critical",v:`${slot.activities.filter((a:any)=>a.isCritical&&!a.isMilestone).length}`},{l:"Neg Float",v:`${slot.activities.filter((a:any)=>a.totalFloat<0).length}`}].map((k,i)=><div key={i} style={{background:C.card2,borderRadius:8,padding:"8px 10px"}}><div style={{fontSize:9,color:C.muted,textTransform:"uppercase",letterSpacing:"0.06em"}}>{k.l}</div><div style={{fontSize:17,fontWeight:700,color:C.text,fontFamily:"'DM Mono',monospace"}}>{k.v}</div></div>)}
          </div>
        </div>
      )}
    </div>
  );

  const DeltaBadge=({val,unit="",invertGood}:any)=>{
    if(val===0||val===undefined||val===null)return<span style={{color:'#111111',fontSize:11}}>—</span>;
    const isGood=invertGood?val<0:val>0;const sign=val>0?"+":"";
    return<span style={{fontSize:11,fontWeight:700,color:isGood?C.green:C.red,background:isGood?"rgba(0,229,160,0.1)":"rgba(255,87,87,0.1)",padding:"1px 6px",borderRadius:4}}>{sign}{val}{unit}</span>;
  };

  return(
    <div>
      <Sec title="Schedule Diff — Compare Two Versions" icon="🔀">
        <p style={{color:C.muted2,fontSize:13,marginBottom:16,marginTop:-8}}>Load two versions of the same project to see exactly what changed — added/removed activities, date shifts, float changes, and progress deltas.</p>
        <div style={{display:"flex",gap:14,flexWrap:"wrap",marginBottom:24}}>
          <Slot label="Version A — Baseline / Before" color={C.accent} slot={slotA} setSlot={setSlotA} loading={loadingA} err={errA} inputRef={refA} setLoading={setLoadingA} setErr={setErrA}/>
          <Slot label="Version B — Updated / After" color={C.gold} slot={slotB} setSlot={setSlotB} loading={loadingB} err={errB} inputRef={refB} setLoading={setLoadingB} setErr={setErrB}/>
        </div>
        {slotA&&slotB&&<div style={{display:"flex",justifyContent:"center",marginBottom:20}}><button onClick={()=>{const tmp=slotA;setSlotA(slotB);setSlotB(tmp);}} style={{background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:8,padding:"6px 16px",cursor:"pointer",fontSize:12,fontFamily:"inherit"}}>⇄ Swap A ↔ B</button></div>}
        {(!slotA||!slotB)?(
          <div style={{textAlign:"center",padding:"40px 20px",color:C.muted2,fontSize:14}}><div style={{fontSize:36,marginBottom:10}}>🔀</div>Load both Version A and Version B above to see the diff analysis</div>
        ):(diff&&<>
          <Sec title="What Changed — Summary" icon="📋">
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(155px,1fr))",gap:9,marginBottom:20}}>
              {[{l:"Activities Added",v:diff.added.length,c:C.green,warn:diff.added.length>0},{l:"Activities Removed",v:diff.removed.length,c:C.red,warn:diff.removed.length>0},{l:"Activities Changed",v:diff.changed.length,c:C.amber,warn:diff.changed.length>0},{l:"Unchanged",v:diff.unchanged.length,c:C.muted2},{l:"Became Critical",v:diff.becameCritical.length,c:C.red,warn:diff.becameCritical.length>0},{l:"Left Critical Path",v:diff.becameNonCritical.length,c:C.green}].map((k,i)=><KPI key={i} label={k.l} value={k.v} color={k.c} warn={k.warn}/>)}
            </div>
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:"hidden",marginBottom:8}}>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr 1fr",background:"rgba(0,200,240,0.04)"}}>{["Metric","Version A","Version B","Delta","Direction"].map(h=><div key={h} style={{padding:"10px 14px",fontSize:10,fontWeight:600,color:C.muted,textTransform:"uppercase",letterSpacing:"0.06em"}}>{h}</div>)}</div>
              {[{label:"Total Activities",a:diff.totalA,b:diff.totalB,invertGood:false},{label:"% Complete (SPC)",a:diff.pctA.toFixed(1)+"%",b:diff.pctB.toFixed(1)+"%",delta:parseFloat((diff.pctB-diff.pctA).toFixed(1)),unit:"%",invertGood:false},{label:"Critical Activities",a:diff.critA,b:diff.critB,delta:diff.critB-diff.critA,invertGood:true},{label:"Negative Float",a:diff.negA,b:diff.negB,delta:diff.negB-diff.negA,invertGood:true},{label:"Overdue Activities",a:diff.overdueA??"Unavailable",b:diff.overdueB??"Unavailable",invertGood:true},{label:"Completed Activities",a:diff.compA,b:diff.compB,delta:diff.compB-diff.compA,invertGood:false}].map((r:any,i)=>{
                const delta=r.delta!==undefined?r.delta:typeof r.a==="number"&&typeof r.b==="number"?r.b-r.a:null;
                const dir=delta===null||delta===0?"—":delta>0?r.invertGood?"↑ Worse":"↑ Better":r.invertGood?"↓ Better":"↓ Worse";
                const dirColor=delta===0||delta===null?C.muted2:delta>0?(r.invertGood?C.red:C.green):(r.invertGood?C.green:C.red);
                return<div key={i} style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr 1fr 1fr",borderTop:`1px solid ${C.border}`}}>
                  <div style={{padding:"9px 14px",color:C.text,fontSize:12,fontWeight:500}}>{r.label}</div>
                  <div style={{padding:"9px 14px",color:C.accent,fontSize:12,fontFamily:"'DM Mono',monospace"}}>{r.a}</div>
                  <div style={{padding:"9px 14px",color:C.gold,fontSize:12,fontFamily:"'DM Mono',monospace"}}>{r.b}</div>
                  <div style={{padding:"9px 14px"}}>{delta!==null&&delta!==0?<DeltaBadge val={delta} unit={r.unit} invertGood={r.invertGood}/>:<span style={{color:'#111111',fontSize:11}}>No change</span>}</div>
                  <div style={{padding:"9px 14px",color:dirColor,fontSize:12,fontWeight:600}}>{dir}</div>
                </div>;
              })}
            </div>
          </Sec>
          <Sec title="Visual Diff — Charts" icon="📊">
            <div style={{display:"flex",flexWrap:"wrap",gap:14,marginBottom:14}}>
              <CC title="Change Breakdown" flex="1 1 220px" height={200}><ResponsiveContainer><PieChart><Pie data={[{name:"Added",value:diff.added.length,fill:C.green},{name:"Removed",value:diff.removed.length,fill:C.red},{name:"Changed",value:diff.changed.length,fill:C.amber},{name:"Unchanged",value:diff.unchanged.length,fill:C.muted}].filter(d=>d.value>0)} cx="50%" cy="50%" outerRadius={75} innerRadius={35} dataKey="value" labelLine={false} label={({name,percent}:any)=>percent>0.05?`${name} ${(percent*100).toFixed(0)}%`:""} fontSize={11}>{[C.green,C.red,C.amber,C.muted].map((f,i)=><Cell key={i} fill={f}/>)}</Pie><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/></PieChart></ResponsiveContainer></CC>
              <CC title="Finish Date Shifts" flex="2 1 340px" height={200}><ResponsiveContainer><BarChart data={diff.delayHist} margin={{left:-10}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis dataKey="range" tick={{fill:C.muted,fontSize:10}}/><YAxis tick={{fill:C.muted,fontSize:10}}/><Tooltip content={<TT/>}/><Bar dataKey="count" name="Activities" radius={[4,4,0,0]}>{diff.delayHist.map((e:any,i:number)=><Cell key={i} fill={e.fill}/>)}</Bar></BarChart></ResponsiveContainer></CC>
              <CC title="% Complete: Before vs After (by WBS)" flex="2 1 380px" height={200}><ResponsiveContainer><BarChart data={diff.pctShiftData} layout="vertical" margin={{left:8}}><CartesianGrid strokeDasharray="3 3" stroke={C.border}/><XAxis type="number" tick={{fill:C.muted,fontSize:10}} unit="%"/><YAxis type="category" dataKey="wbs" tick={{fill:C.muted,fontSize:10}} width={110}/><Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/><Bar dataKey="before" name="Version A" fill={C.accent} radius={[0,4,4,0]}/><Bar dataKey="after" name="Version B" fill={C.gold} radius={[0,4,4,0]}/></BarChart></ResponsiveContainer></CC>
            </div>
          </Sec>
          {diff.becameCritical.length>0&&<Sec title={`⚠ Became Critical in Version B (${diff.becameCritical.length})`} icon="🔴">
            <div style={{background:C.card,border:`1px solid ${C.red}40`,borderRadius:12,overflow:"auto"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:12,minWidth:600}}>
                <thead><tr style={{background:"rgba(255,87,87,0.08)"}}>{["Code","Activity Name","WBS","Float A","Float B","Finish A","Finish B"].map(h=><th key={h} style={{padding:"8px 12px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.05em",whiteSpace:"nowrap"}}>{h}</th>)}</tr></thead>
                <tbody>{diff.becameCritical.map((c:any,i:number)=><tr key={i} style={{borderTop:`1px solid ${C.border}`}}>
                  <td style={{padding:"7px 12px",color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10}}>{c.code}</td>
                  <td style={{padding:"7px 12px",color:C.text,maxWidth:220,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{c.nameB}</td>
                  <td style={{padding:"7px 12px",color:'#111111',fontSize:11}}>{c.b.wbs||"—"}</td>
                  <td style={{padding:"7px 12px",color:C.green,fontFamily:"'DM Mono',monospace"}}>{Math.round(c.a.totalFloat||0)}</td>
                  <td style={{padding:"7px 12px",color:C.red,fontFamily:"'DM Mono',monospace",fontWeight:700}}>{Math.round(c.b.totalFloat||0)}</td>
                  <td style={{padding:"7px 12px",color:C.muted2,whiteSpace:"nowrap",fontSize:11}}>{fmtDate(c.a.bFinish||c.a.finish)}</td>
                  <td style={{padding:"7px 12px",color:C.amber,whiteSpace:"nowrap",fontSize:11}}>{fmtDate(c.b.bFinish||c.b.finish)}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </Sec>}
          {diff.changed.length>0&&<Sec title={`Detailed Change Log (${diff.changed.length} activities)`} icon="📋">
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:"auto"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:12,minWidth:700}}>
                <thead><tr style={{background:"rgba(255,181,71,0.06)"}}>{["Code","Activity Name","Field","Version A","Version B","Delta"].map(h=><th key={h} style={{padding:"8px 12px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.05em",whiteSpace:"nowrap"}}>{h}</th>)}</tr></thead>
                <tbody>{diff.changed.slice(0,60).flatMap((c:any,i:number)=>c.changes.map((ch:any,j:number)=>{
                  const isDate=ch.field.includes("Date");
                  const fmtVal=(v:any)=>isDate?fmtDate(v):typeof v==="boolean"?String(v):v;
                  const deltaEl=ch.daysDiff!==undefined?<DeltaBadge val={ch.daysDiff} unit="d" invertGood={true}/>:ch.delta!==undefined?<DeltaBadge val={typeof ch.delta==="number"?Math.round(ch.delta):ch.delta} unit={ch.field==="% Complete"?"%":ch.field==="Duration"?"d":""} invertGood={ch.field==="Total Float"||ch.field==="Critical Status"}/>:null;
                  return<tr key={`${i}-${j}`} style={{borderTop:`1px solid ${C.border}`,background:j===0?"rgba(255,181,71,0.02)":"transparent"}}>
                    {j===0?<td rowSpan={c.changes.length} style={{padding:"7px 12px",color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10,verticalAlign:"top",borderRight:`1px solid ${C.border}`}}>{c.code}</td>:null}
                    {j===0?<td rowSpan={c.changes.length} style={{padding:"7px 12px",color:C.text,maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",verticalAlign:"top",borderRight:`1px solid ${C.border}`}}>{c.nameB}</td>:null}
                    <td style={{padding:"7px 12px",color:C.muted2,whiteSpace:"nowrap"}}>{ch.field}</td>
                    <td style={{padding:"7px 12px",color:C.accent,fontFamily:"'DM Mono',monospace",fontSize:11,whiteSpace:"nowrap"}}>{fmtVal(ch.from)}</td>
                    <td style={{padding:"7px 12px",color:C.gold,fontFamily:"'DM Mono',monospace",fontSize:11,whiteSpace:"nowrap"}}>{fmtVal(ch.to)}</td>
                    <td style={{padding:"7px 12px"}}>{deltaEl}</td>
                  </tr>;
                }))}{diff.changed.length>60&&<tr><td colSpan={6} style={{padding:"8px 12px",color:'#111111',fontSize:11,textAlign:"center"}}>…{diff.changed.length-60} more changed activities</td></tr>}</tbody>
              </table>
            </div>
          </Sec>}
        </>)}
      </Sec>
    </div>
  );
}

// ─── VARIANCE VIEW ────────────────────────────────────────────────────────────
function VarianceView({M,allActivities}:any){
  const fmt=(v:number|null)=>v===null||v===undefined?'—':`${v>0?'+':''}${v}d`;
  const varColor=(v:number|null)=>!v?C.muted2:v>14?C.red:v>0?C.amber:v<0?C.green:C.muted2;
  const [activeFilter,setActiveFilter]=useState<string|null>(null);
  const blStatus=useMemo(()=>detectBaseline(allActivities||[]),[allActivities]);

  const nonMilestone=(allActivities||[]).filter((a:any)=>!a.isMilestone);
  const delayedAll  =useMemo(()=>nonMilestone.filter((a:any)=>(a.finishVariance??0)>0).sort((a:any,b:any)=>(b.finishVariance??0)-(a.finishVariance??0)),[allActivities]);
  const advancedAll =useMemo(()=>nonMilestone.filter((a:any)=>(a.finishVariance??0)<0).sort((a:any,b:any)=>(a.finishVariance??0)-(b.finishVariance??0)),[allActivities]);
  const baselineAll =useMemo(()=>nonMilestone.filter((a:any)=>a.bFinish&&((a.finishVariance??null)===0||(a.finishVariance??null)===null&&a.bFinish)),[allActivities]);
  const allVar      =useMemo(()=>nonMilestone.filter((a:any)=>a.bFinish),[allActivities]);

  const toggle=(f:string)=>setActiveFilter(p=>p===f?null:f);

  const filteredRows=activeFilter==="DELAYED"?delayedAll:activeFilter==="ADVANCED"?advancedAll:activeFilter==="BASELINE"?baselineAll:activeFilter==="ALL"?allVar:[];

  const onBaseline=M.total-(M.delayedActs?.length||0)-(M.advancedActs?.length||0);

  const [expandedActId,setExpandedActId]=useState<string|null>(null);
  const toggleAct=(id:string)=>setExpandedActId(p=>p===id?null:id);
  const actMap=useMemo(()=>{const m:Record<string,any>={};(allActivities||[]).forEach((a:any)=>{m[a.id]=a;});return m;},[allActivities]);
  const navigateToAct=useCallback((actId:string)=>{
    setExpandedActId(actId);
    setTimeout(()=>document.querySelector(`[data-actid="${CSS.escape(actId)}"]`)?.scrollIntoView({behavior:"smooth",block:"center"}),60);
  },[]);
  const {colW:vColW,onResizeStart:vResize,reset:vReset}=useColResize({code:90,name:220,project:130,wbs:110,bFinish:100,forecast:100,finishVar:80,bStart:100,actStart:100,startVar:80,pct:85});
  const VAR_COLS:[string,string][]=[['code','Code'],['name','Activity Name'],['project','Project'],['wbs','WBS'],['bFinish','BL Finish'],['forecast','Forecast Finish'],['finishVar','Finish Var'],['bStart','BL Start'],['actStart','Act/Proj Start'],['startVar','Start Var'],['pct','% Done']];
  const varCols=useColOrder(VAR_COLS);
  const {visible:varVisible,hidden:varHidden}=varCols;

  const VarRow=({a}:{a:any})=>{
    const bf=a.bFinish instanceof Date?a.bFinish:a.bFinish?new Date(a.bFinish):null;
    const pf=a.projectedFinish instanceof Date?a.projectedFinish:a.projectedFinish?new Date(a.projectedFinish):a.finish?new Date(a.finish):null;
    const bs=a.bStart instanceof Date?a.bStart:a.bStart?new Date(a.bStart):null;
    const as_=a.start instanceof Date?a.start:a.start?new Date(a.start):null;
    const fv=a.finishVariance??null;
    const sv=a.startVariance??null;
    const fvColor=fv==null?C.muted2:fv>14?C.red:fv>0?C.amber:fv<0?C.green:C.muted2;
    const actId=a.id||a.code;
    const isExp=expandedActId===actId;
    const td:React.CSSProperties={padding:'7px 11px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'};
    return(<Fragment key={actId}>
      <tr data-actid={actId} onClick={()=>toggleAct(actId)} style={{borderTop:`1px solid ${C.border}`,cursor:"pointer",background:isExp?`${C.accent}0a`:"transparent"}} title="Click to view predecessors & successors">
        {!varHidden.has('code')&&<td style={{...td,color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10}}>{a.code}</td>}
        {!varHidden.has('name')&&<td style={{...td}} title={a.name}>{a.name}</td>}
        {!varHidden.has('project')&&<td style={{...td,color:'#111111',fontSize:11}} title={a.projectName||a.projectId}>{a.projectName||a.projectId}</td>}
        {!varHidden.has('wbs')&&<td style={{...td,color:'#111111',fontSize:11}} title={a.wbs||''}>{a.wbs||'—'}</td>}
        {!varHidden.has('bFinish')&&<td style={{...td,color:'#111111',fontSize:11}}>{fmtDate(bf)}</td>}
        {!varHidden.has('forecast')&&<td style={{...td,color:fv&&fv>0?C.amber:C.green,fontSize:11}}>{fmtDate(pf)}</td>}
        {!varHidden.has('finishVar')&&<td style={{...td,color:fvColor,fontWeight:700,fontFamily:"'DM Mono',monospace"}}>{fmt(fv)}</td>}
        {!varHidden.has('bStart')&&<td style={{...td,color:'#111111',fontSize:11}}>{fmtDate(bs)}</td>}
        {!varHidden.has('actStart')&&<td style={{...td,color:sv&&sv>0?C.amber:C.muted2,fontSize:11}}>{fmtDate(as_)}</td>}
        {!varHidden.has('startVar')&&<td style={{...td,color:sv&&sv>0?C.amber:C.muted2,fontFamily:"'DM Mono',monospace"}}>{fmt(sv)}</td>}
        {!varHidden.has('pct')&&<td style={{...td}}><div style={{display:'flex',alignItems:'center',gap:5}}><div style={{flex:1,background:C.border,borderRadius:3,height:4}}><div style={{width:`${a.pctComplete||0}%`,background:C.green,borderRadius:3,height:4}}/></div><span style={{color:'#111111',fontSize:10}}>{a.pctComplete||0}%</span></div></td>}
      </tr>
      {isExp&&<LogicPanel a={a} actMap={actMap} colSpan={varVisible.length} onNavigate={navigateToAct}/>}
    </Fragment>);
  };

  return(<>
    {!blStatus.hasBaseline&&(
      <div style={{margin:'0 0 14px',padding:'10px 16px',borderRadius:10,background:`${C.amber}15`,border:`1px solid ${C.amber}40`,display:'flex',alignItems:'center',gap:10}}>
        <span style={{fontSize:16}}>⚠️</span>
        <div>
          <span style={{fontWeight:700,color:C.amber,fontSize:12}}>
            {blStatus.status === "Partial Baseline" ? "Partial Baseline Detected" : "No Baseline Detected"}
          </span>
          <span style={{color:C.muted,fontSize:11,marginLeft:8}}>
            {blStatus.coverage>0
              ? `Only ${blStatus.coverage}% of activities have baseline dates${!blStatus.isReal?" — dates match current plan (no divergence)":""}.`
              : "No baseline dates found in this schedule."
            } Variance figures may be unreliable.
          </span>
        </div>
      </div>
    )}
    <Sec title="Schedule Variance Summary" icon="📐">
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(160px,1fr))',gap:9,marginBottom:18}}>
        <KPI label="Avg Finish Variance"  value={fmt(M.avgFinishVar)} color={varColor(M.avgFinishVar)} warn={M.avgFinishVar>0}  onClick={()=>toggle("ALL")}      active={activeFilter==="ALL"}/>
        <KPI label="Avg Start Variance"   value={fmt(M.avgStartVar)}  color={varColor(M.avgStartVar)}  warn={M.avgStartVar>0}   onClick={()=>toggle("ALL")}      active={activeFilter==="ALL"}/>
        <KPI label="% Activities Delayed" value={`${M.pctDelayed}%`}  color={M.pctDelayed>20?C.red:M.pctDelayed>5?C.amber:C.green} warn={M.pctDelayed>0} onClick={()=>toggle("DELAYED")} active={activeFilter==="DELAYED"}/>
        <KPI label="Delayed Activities"   value={delayedAll.length}   color={C.red}    warn={delayedAll.length>0}  onClick={()=>toggle("DELAYED")}  active={activeFilter==="DELAYED"}/>
        <KPI label="Advanced Activities"  value={advancedAll.length}  color={C.green}                              onClick={()=>toggle("ADVANCED")} active={activeFilter==="ADVANCED"}/>
        <KPI label="On Baseline"          value={onBaseline}          color={C.accent}                             onClick={()=>toggle("BASELINE")} active={activeFilter==="BASELINE"}/>
      </div>
    </Sec>

    <Sec title="Finish Variance Distribution" icon="📊">
      <div style={{display:'flex',flexWrap:'wrap',gap:12}}>
        <CC title="Variance Histogram (calendar days)" flex="2 1 420px" height={240}>
          <ResponsiveContainer><BarChart data={M.varHist} margin={{left:-10}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis dataKey="range" tick={{fill:C.muted,fontSize:11}}/>
            <YAxis tick={{fill:C.muted,fontSize:11}}/>
            <Tooltip content={<TT/>}/>
            <Bar dataKey="count" name="Activities" radius={[4,4,0,0]}>{(M.varHist||[]).map((e:any,i:number)=><Cell key={i} fill={e.fill}/>)}</Bar>
          </BarChart></ResponsiveContainer>
        </CC>
        <CC title="Avg Finish Variance by WBS (days)" flex="2 1 380px" height={240}>
          <ResponsiveContainer><BarChart layout="vertical" data={M.varByWBS} margin={{left:8}}>
            <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
            <XAxis type="number" tick={{fill:C.muted,fontSize:11}}/>
            <YAxis type="category" dataKey="wbs" tick={{fill:C.muted,fontSize:10}} width={110}/>
            <Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/>
            <ReferenceLine x={0} stroke={C.border}/>
            <Bar dataKey="delayed"  name="Delayed"   fill={C.red}   radius={[0,4,4,0]}/>
            <Bar dataKey="advanced" name="Advanced"  fill={C.green} radius={[0,4,4,0]}/>
          </BarChart></ResponsiveContainer>
        </CC>
      </div>
    </Sec>

    {activeFilter&&filteredRows.length>0&&(
      <Sec title={`${activeFilter==="DELAYED"?"Delayed":activeFilter==="ADVANCED"?"Advanced":activeFilter==="BASELINE"?"On Baseline":"All Variance"} Activities (${filteredRows.length})`} icon={activeFilter==="DELAYED"?"⚠️":activeFilter==="ADVANCED"?"✅":activeFilter==="BASELINE"?"🎯":"📋"}>
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:'auto',maxHeight:520}}>
          <div style={{display:"flex",justifyContent:"flex-end",alignItems:"center",padding:"4px 8px 0",gap:6}}>
            {(()=>{
              const varCell=(a:any,k:string)=>{
                const bf=a.bFinish?new Date(a.bFinish):null,pf=a.projectedFinish?new Date(a.projectedFinish):a.finish?new Date(a.finish):null;
                const bs=a.bStart?new Date(a.bStart):null,as_=a.start?new Date(a.start):null;
                const fv=a.finishVariance??null,sv=a.startVariance??null;
                switch(k){case'code':return a.code||'';case'name':return a.name||'';case'project':return a.projectName||a.projectId||'';case'wbs':return a.wbs||'';case'bFinish':return fmtDateExport(bf);case'forecast':return fmtDateExport(pf);case'finishVar':return fv==null?'':fv>0?`+${fv}d`:`${fv}d`;case'bStart':return fmtDateExport(bs);case'actStart':return fmtDateExport(as_);case'startVar':return sv==null?'':sv>0?`+${sv}d`:`${sv}d`;case'pct':return`${a.pctComplete||0}%`;default:return'';}
              };
              const varTitle=`Variance — ${activeFilter||'All'}`;
              return(<>
                <button type="button" onClick={()=>printTable(filteredRows,varVisible,varTitle,'variance-report',varCell)} style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>🖨 PDF</button>
                <button type="button" onClick={()=>downloadCSV(filteredRows,varVisible,'variance-report',varCell)} style={{background:`${C.green}14`,border:`1px solid ${C.green}40`,color:C.green,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>📊 Excel</button>
              </>);
            })()}
            <ColPickerDialog allCols={VAR_COLS} cols={varCols}/>
            <button type="button" onClick={vReset} style={{background:"transparent",border:"none",color:C.muted2,cursor:"pointer",fontSize:11,padding:"2px 6px"}}>↔ Reset</button>
          </div>
          <table style={{tableLayout:"fixed",borderCollapse:'collapse',fontSize:12,width:varVisible.reduce((s,[k])=>s+vColW[k],0)}}>
            <colgroup>{varVisible.map(([k])=><col key={k} style={{width:vColW[k]}}/>)}</colgroup>
            <thead style={{position:'sticky',top:0,zIndex:1}}>
              <tr style={{background:C.card2}}>
                {VAR_COLS.map(([key,label])=>{
                  if(varHidden.has(key))return null;
                  return(<RTh key={key} colKey={key} label={label} colW={vColW} onResizeStart={vResize}/>);
                })}
              </tr>
            </thead>
            <tbody>{filteredRows.map((a:any)=><VarRow key={a.id||a.code} a={a}/>)}</tbody>
          </table>
        </div>
      </Sec>
    )}
    {activeFilter&&filteredRows.length===0&&(
      <div style={{textAlign:'center',padding:'32px',color:C.muted2,fontSize:13}}>No activities in this category.</div>
    )}
  </>);
}

// ─── EVM VIEW ─────────────────────────────────────────────────────────────────
// EVM figures below come from the authoritative cost_engine.py (same engine
// as the Project Controls tab), computed on this session's activities by
// /api/metrics/'s `authoritativeEvm`/`authoritativeEvmByProject` fields —
// NOT from the legacy M.evm/M.manHours block, which can default CPI to 1.0
// or use activity duration as a dollar proxy when a schedule isn't actually
// cost-loaded. M.evm/M.manHours are left computing untouched for any other
// consumer, but this view no longer reads their EVM index/forecast fields.
function EVMView({M}:any){
  const mh=M.manHours||{};   // still used below for the honest raw MH curve/histogram (not its CPI/EAC fields)
  const aevm=M.authoritativeEvm||{};
  const cost=aevm.cost||{};
  const hours=aevm.hours||{};
  const forecastScenarios=aevm.forecast?.scenarios||[];
  const hoursForecastScenarios=aevm.hoursForecast?.scenarios||[];
  const primaryForecast=forecastScenarios[0];
  const fmt$=(v:number)=>v>=1e6?`$${(v/1e6).toFixed(2)}M`:v>=1e3?`$${(v/1e3).toFixed(1)}K`:`$${v.toFixed(0)}`;
  const fmtH=(v:number)=>v>=1000?`${(v/1000).toFixed(1)}K h`:`${Math.round(v)} h`;
  const spiColor=(v:number)=>v>=0.95?C.green:v>=0.85?C.amber:C.red;
  const cpiColor=(v:number)=>v>=0.95?C.green:v>=0.85?C.amber:C.red;
  const money=(v:number|null|undefined)=>v==null?'Unavailable':fmt$(v);
  const idx=(v:number|null|undefined)=>v==null?'Unavailable':v.toFixed(3);

  return(<>
    {/* EVM KPIs */}
    <Sec title={
      M.evmDataQuality==='cost_loaded'     ? `Earned Value Management — Cost Loaded (${M.costLoadPct??0}% of activities)` :
      M.evmDataQuality==='resource_loaded' ? `Earned Value Management — Resource Loaded / Man-Hours (${M.resourceLoadPct??0}% of activities)` :
      'Earned Value Management — Duration Based (no cost or resource data)'
    } icon="💰" printable={true}>
      {/* Cost-loaded: green success banner */}
      {M.evmDataQuality==='cost_loaded'&&<div style={{background:'rgba(0,229,160,0.07)',border:'1px solid rgba(0,229,160,0.25)',borderRadius:8,padding:'10px 14px',fontSize:12,color:C.green,marginBottom:14,display:'flex',alignItems:'center',gap:8}}>
        <span style={{fontSize:16}}>💰</span>
        <span><strong>Cost loaded</strong> — EV, PV and AC are calculated from P6 budgeted cost (target_cost), actual cost (act_reg_cost + act_ot_cost) and remaining cost fields. All EVM indices are dollar-accurate.</span>
      </div>}
      {/* Resource-loaded (hours, no cost): amber info banner */}
      {M.evmDataQuality==='resource_loaded'&&<div style={{background:'rgba(255,193,7,0.07)',border:'1px solid rgba(255,193,7,0.25)',borderRadius:8,padding:'10px 14px',fontSize:12,color:C.amber,marginBottom:14,display:'flex',alignItems:'center',gap:8}}>
        <span style={{fontSize:16}}>⚙</span>
        <span><strong>Resource loaded (man-hours)</strong> — Budgeted Units (target_qty), Actual Regular/OT Units and Remaining Units are loaded. Hours-based EVM (below, "MH" tiles) is available; cost EVM requires a cost-loaded XER and shows <strong>Unavailable</strong> until one is imported.</span>
      </div>}
      {/* Duration-only: warning banner */}
      {M.evmDataQuality==='duration_only'&&<div style={{background:'rgba(212,168,67,0.07)',border:`1px solid ${C.gold}30`,borderRadius:8,padding:'10px 14px',fontSize:12,color:C.gold,marginBottom:14,display:'flex',alignItems:'center',gap:8}}>
        <span style={{fontSize:16}}>⚠</span>
        <span><strong>Duration only</strong> — No TASKRSRC assignments found. Cost and hours EVM both show <strong>Unavailable</strong> below rather than a duration-derived proxy. For accurate EVM, upload a resource-loaded or cost-loaded XER from P6.</span>
      </div>}
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(155px,1fr))',gap:9,marginBottom:18}}>
        {[
          {l:'BAC',            v:money(cost.bac),  d:'Budget at Completion',       c:cost.bac!=null?C.text:C.muted2},
          {l:'PV (Planned)',   v:money(cost.pv),   d:'Planned Value',              c:cost.pv!=null?PLAN_COLOR:C.muted2},
          {l:'EV (Earned)',    v:money(cost.ev),   d:'Earned Value',               c:cost.ev!=null?C.green:C.muted2},
          {l:'AC (Actual)',    v:money(cost.ac),   d:'Actual Cost',                c:cost.ac!=null?C.amber:C.muted2},
          {l:'EAC',            v:primaryForecast?money(primaryForecast.eac):'Unavailable', d:primaryForecast?primaryForecast.label:'No cost-loaded activities', c:!primaryForecast?C.muted2:(cost.bac!=null&&primaryForecast.eac>cost.bac?C.red:C.green)},
          {l:'ETC',            v:(()=>{const bu=forecastScenarios.find((s:any)=>s.methodology==='BOTTOM_UP');return bu?money(bu.etc):'Unavailable';})(),    d:"P6's own remaining-cost estimate", c:C.muted2},
          {l:'VAC',            v:primaryForecast?money(primaryForecast.vac):'Unavailable',    d:primaryForecast?`Using ${primaryForecast.label.split(' (')[0]}`:'No forecast available',c:!primaryForecast?C.muted2:(primaryForecast.vac<0?C.red:C.green)},
        ].map((k,i)=><div key={i} style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'13px 15px'}}>
          <div style={{fontSize:10,color:C.muted,textTransform:'uppercase',letterSpacing:'0.07em',marginBottom:3}}>{k.l}</div>
          <div style={{fontSize:22,fontWeight:700,color:k.c,fontFamily:"'DM Mono',monospace"}}>{k.v}</div>
          <div style={{fontSize:10,color:C.muted2,marginTop:2}}>{k.d}</div>
        </div>)}
      </div>
      {forecastScenarios.length>1&&<div style={{fontSize:11,color:C.muted2,marginBottom:14}}>
        Other forecast scenarios: {forecastScenarios.slice(1).map((s:any)=>`${s.label.split(' (')[0]} ${fmt$(s.eac)}`).join(' · ')}
        {' · '}see <strong>Project Controls</strong> for the full breakdown.
      </div>}
      {/* Performance indices */}
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(155px,1fr))',gap:9,marginBottom:18}}>
        {[
          {l:'SPI',  v:idx(cost.spi),  d:'Schedule Performance Index',  c:cost.spi==null?C.muted2:spiColor(cost.spi), warn:cost.spi!=null&&cost.spi<0.95},
          {l:'CPI',  v:idx(cost.cpi),  d:'Cost Performance Index',      c:cost.cpi==null?C.muted2:cpiColor(cost.cpi), warn:cost.cpi!=null&&cost.cpi<0.95},
          {l:'SV',   v:money(cost.sv), d:'Schedule Variance',           c:cost.sv==null?C.muted2:(cost.sv>=0?C.green:C.red), warn:cost.sv!=null&&cost.sv<0},
          {l:'CV',   v:money(cost.cv), d:'Cost Variance',               c:cost.cv==null?C.muted2:(cost.cv>=0?C.green:C.red), warn:cost.cv!=null&&cost.cv<0},
          {l:'TCPI', v:idx(cost.tcpi),d:'To-Complete Perf. Index',      c:cost.tcpi==null?C.muted2:(cost.tcpi>1.1?C.red:cost.tcpi>1.0?C.amber:C.green)},
        ].map((k,i)=><KPI key={i} label={k.l} value={k.v} sub={k.d} color={k.c} warn={k.warn}/>)}
      </div>
    </Sec>

    {/* EVM S-Curve */}
    <Sec title="EVM S-Curve — BCWS vs BCWP vs ACWP" icon="📈" printable={true}>
      <CC title="Cumulative Earned Value" height={340} flex="1 1 100%">
        <ResponsiveContainer><ComposedChart data={M.evmCurve||[]} margin={{left:-10}}>
          <defs>
            <linearGradient id="gBCWS" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={PLAN_COLOR} stopOpacity={0.18}/><stop offset="95%" stopColor={PLAN_COLOR} stopOpacity={0}/></linearGradient>
            <linearGradient id="gBCWP" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.green} stopOpacity={0.15}/><stop offset="95%" stopColor={C.green} stopOpacity={0}/></linearGradient>
            <linearGradient id="gACWP" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.amber} stopOpacity={0.15}/><stop offset="95%" stopColor={C.amber} stopOpacity={0}/></linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
          <XAxis dataKey="month" tick={{fill:C.muted,fontSize:11}} interval={Math.max(0,Math.floor((M.evmCurve||[]).length/14))}/>
          <YAxis tick={{fill:C.muted,fontSize:11}} unit="%" domain={[0,100]}/>
          <Tooltip content={<TT/>}/><Legend iconSize={10} wrapperStyle={{fontSize:12}}/>
          <Area type="monotone" dataKey="pctBCWS" name="BCWS (Planned %)" stroke={PLAN_COLOR} fill="url(#gBCWS)" strokeWidth={2.5} dot={false}/>
          <Area type="monotone" dataKey="pctBCWP" name="BCWP (Earned %)"  stroke={C.green} fill="url(#gBCWP)" strokeWidth={2.5} dot={false}/>
          {M.isCostLoaded&&<Area type="monotone" dataKey="pctACWP" name="ACWP (Actual %)" stroke={C.amber} fill="url(#gACWP)" strokeWidth={2} dot={false} strokeDasharray="5 3"/>}
        </ComposedChart></ResponsiveContainer>
      </CC>
    </Sec>

    {/* Per-project EVM table */}
    <Sec title="EVM by Project" icon="📋">
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:'auto'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:900}}>
          <thead><tr style={{background:'rgba(0,200,240,0.06)'}}>{['Project','BAC','PV','EV','AC','SPI','CPI','SV','CV','EAC','VAC'].map(h=><th key={h} style={{padding:'9px 12px',textAlign:'left',color:C.muted,fontWeight:600,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em',whiteSpace:'nowrap'}}>{h}</th>)}</tr></thead>
          <tbody>{(M.projects||[]).map((p:any,i:number)=>{
            const pc=M.authoritativeEvmByProject?.[p.id]?.cost||{};
            const pf=M.authoritativeEvmByProject?.[p.id]?.forecast?.scenarios?.[0];
            return(
            <tr key={p.id} style={{borderTop:`1px solid ${C.border}`}}>
              <td style={{padding:'9px 12px',color:C.text,fontWeight:600}}>{p.name}</td>
              <td style={{padding:'9px 12px',color:C.muted2,fontFamily:"'DM Mono',monospace",fontSize:11}}>{money(pc.bac)}</td>
              <td style={{padding:'9px 12px',color:pc.pv!=null?C.accent:C.muted,fontFamily:"'DM Mono',monospace",fontSize:11}}>{money(pc.pv)}</td>
              <td style={{padding:'9px 12px',color:pc.ev!=null?C.green:C.muted,fontFamily:"'DM Mono',monospace",fontSize:11}}>{money(pc.ev)}</td>
              <td style={{padding:'9px 12px',color:pc.ac!=null?C.amber:C.muted,fontFamily:"'DM Mono',monospace",fontSize:11}}>{money(pc.ac)}</td>
              <td style={{padding:'9px 12px',color:pc.spi==null?C.muted:spiColor(pc.spi),fontWeight:700,fontFamily:"'DM Mono',monospace"}}>{idx(pc.spi)}</td>
              <td style={{padding:'9px 12px',color:pc.cpi==null?C.muted:cpiColor(pc.cpi),fontFamily:"'DM Mono',monospace"}}>{idx(pc.cpi)}</td>
              <td style={{padding:'9px 12px',color:pc.sv==null?C.muted:(pc.sv>=0?C.green:C.red),fontFamily:"'DM Mono',monospace",fontSize:11}}>{money(pc.sv)}</td>
              <td style={{padding:'9px 12px',color:pc.cv==null?C.muted:(pc.cv>=0?C.green:C.red),fontFamily:"'DM Mono',monospace",fontSize:11}}>{money(pc.cv)}</td>
              <td style={{padding:'9px 12px',color:pf&&pc.bac!=null&&pf.eac>pc.bac?C.red:C.green,fontFamily:"'DM Mono',monospace",fontSize:11}}>{pf?money(pf.eac):'Unavailable'}</td>
              <td style={{padding:'9px 12px',color:pf&&pf.vac<0?C.red:C.green,fontFamily:"'DM Mono',monospace",fontSize:11}}>{pf?money(pf.vac):'Unavailable'}</td>
            </tr>
          );})}</tbody>
        </table>
      </div>
    </Sec>

    {/* Man-hours section */}
    <Sec title={M.isHrLoaded?'Man-Hour Analysis — Resource Loaded':'Man-Hour Analysis'} icon="👷" printable={true}>
      {!M.isHrLoaded&&<div style={{background:'rgba(212,168,67,0.08)',border:`1px solid ${C.gold}30`,borderRadius:8,padding:'10px 14px',fontSize:12,color:C.gold,marginBottom:14}}>
        ⚠ No man-hour data detected. Upload a resource-loaded XER file to see man-hour tracking.
      </div>}
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(155px,1fr))',gap:9,marginBottom:18}}>
        {[
          {l:'Budgeted MH',    v:fmtH(mh.budgeted||0),   d:'Total planned hours',    c:C.text},
          {l:'Actual MH',      v:fmtH(mh.actual||0),     d:'Hours spent to date',    c:M.isHrLoaded?C.amber:C.muted2},
          {l:'Remaining MH',   v:fmtH(mh.remaining||0),  d:'Hours left to complete', c:C.accent},
          {l:'Earned MH',      v:fmtH(mh.earned||0),     d:'Budget × % complete',    c:C.green},
          {l:'MH % Complete',  v:`${mh.pctComplete||0}%`, d:'Earned / Budgeted',     c:M.isHrLoaded?((mh.pctComplete||0)>=80?C.green:(mh.pctComplete||0)>=50?C.amber:C.red):C.muted2},
          {l:'MH CPI',         v:hours.cpi!=null?hours.cpi.toFixed(3):'Unavailable', d:'Productivity index',     c:hours.cpi==null?C.muted2:cpiColor(hours.cpi), warn:hours.cpi!=null&&hours.cpi<0.95},
          {l:'MH EAC',         v:hoursForecastScenarios[0]?fmtH(hoursForecastScenarios[0].eac):'Unavailable',        d:'Est. hours at completion',c:C.muted2},
        ].map((k:any,i)=><KPI key={i} label={k.l} value={k.v} sub={k.d} color={k.c} warn={k.warn}/>)}
      </div>
      {M.isHrLoaded&&(mh.byWBS||[]).length>0&&<CC title="Man-Hours by WBS — Budgeted vs Actual vs Earned" height={260} flex="1 1 100%">
        <ResponsiveContainer><BarChart layout="vertical" data={mh.byWBS||[]} margin={{left:8}}>
          <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
          <XAxis type="number" tick={{fill:C.muted,fontSize:11}}/>
          <YAxis type="category" dataKey="wbs" tick={{fill:C.muted,fontSize:10}} width={120}/>
          <Tooltip content={<TT/>}/><Legend iconSize={9} wrapperStyle={{fontSize:10}}/>
          <Bar dataKey="budgeted"  name="Budgeted"  fill={C.accent} radius={[0,4,4,0]}/>
          <Bar dataKey="actual"    name="Actual"    fill={C.amber}  radius={[0,4,4,0]}/>
          <Bar dataKey="earned"    name="Earned"    fill={C.green}  radius={[0,4,4,0]}/>
        </BarChart></ResponsiveContainer>
      </CC>}
      {M.isHrLoaded&&<Sec title="Man-Hours by Project" icon="📋">
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,overflow:'auto'}}>
          <table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:700}}>
            <thead><tr style={{background:'rgba(0,200,240,0.06)'}}>{['Project','Budgeted MH','Actual MH','Remaining MH','Earned MH','MH %','MH CPI'].map(h=><th key={h} style={{padding:'9px 12px',textAlign:'left',color:C.muted,fontWeight:600,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em',whiteSpace:'nowrap'}}>{h}</th>)}</tr></thead>
            <tbody>{(M.projects||[]).filter((p:any)=>p.bgtHrs>0).map((p:any,i:number)=>{
              const pMhCpi=M.authoritativeEvmByProject?.[p.id]?.hours?.cpi;
              return(<tr key={p.id} style={{borderTop:`1px solid ${C.border}`}}>
                <td style={{padding:'9px 12px',color:C.text,fontWeight:600}}>{p.name}</td>
                <td style={{padding:'9px 12px',color:C.text,fontFamily:"'DM Mono',monospace"}}>{fmtH(p.bgtHrs||0)}</td>
                <td style={{padding:'9px 12px',color:C.amber,fontFamily:"'DM Mono',monospace"}}>{fmtH(p.actHrs||0)}</td>
                <td style={{padding:'9px 12px',color:C.accent,fontFamily:"'DM Mono',monospace"}}>{fmtH(p.remHrs||0)}</td>
                <td style={{padding:'9px 12px',color:C.green,fontFamily:"'DM Mono',monospace"}}>{fmtH(p.ernHrs||0)}</td>
                <td style={{padding:'9px 12px',minWidth:100}}><div style={{display:'flex',alignItems:'center',gap:5}}><div style={{width:60,background:C.border,borderRadius:3,height:4}}><div style={{width:`${p.mhPct||0}%`,background:C.green,borderRadius:3,height:4}}/></div><span style={{color:'#111111',fontSize:11}}>{(p.mhPct||0).toFixed(1)}%</span></div></td>
                <td style={{padding:'9px 12px',color:pMhCpi==null?C.muted:cpiColor(pMhCpi),fontWeight:700,fontFamily:"'DM Mono',monospace"}}>{pMhCpi!=null?pMhCpi.toFixed(3):'Unavailable'}</td>
              </tr>);
            })}</tbody>
          </table>
        </div>
      </Sec>}
    </Sec>
  </>);
}

// ─── NARRATIVE MODULE ────────────────────────────────────────────────────────

/* -------------------------------------------------------------------
   NOTE: The old buildNarrative / narrativeToHTML functions have been
   replaced by a backend API call (POST /api/narrative/) that runs the
   evidence-based narrative engine in scheduler/narrative_engine.py.
   The old functions are removed to avoid confusion with the new logic.
------------------------------------------------------------------- */

// Status palette used by both NarrativeView and export helpers
const NAR_STATUS_COLOR:Record<string,string>={
  ON_TRACK:'#00936b', AT_RISK:'#c47c00', OFF_TRACK:'#d93030', UNDETERMINED:'#7a6454',
};
const NAR_STATUS_LABEL:Record<string,string>={
  ON_TRACK:'ON TRACK', AT_RISK:'AT RISK', OFF_TRACK:'OFF TRACK', UNDETERMINED:'UNDETERMINED',
};
const NAR_STATUS_ICON:Record<string,string>={
  ON_TRACK:'✅', AT_RISK:'⚠️', OFF_TRACK:'🔴', UNDETERMINED:'❓',
};
const NAR_SEV_COLOR:Record<string,string>={
  CRITICAL:'#8b0000', HIGH:'#d93030', MEDIUM:'#c47c00', LOW:'#00936b', INFO:'#7a6454',
};

/* ---------- export helpers ---------- */

function buildNarrativeHTML(n:any, edited:Record<string,string>):string{
  const s=(id:string)=>edited[id]||(n[id]||'');
  const dateStr=n.data_date?new Date(n.data_date+'T12:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'}):n.report_date;
  const statusColor=NAR_STATUS_COLOR[n.status]||'#7a6454';
  const statusLabel=NAR_STATUS_LABEL[n.status]||n.status;
  const today=new Date(n.report_date+'T12:00:00').toLocaleDateString('en-US',{month:'long',day:'numeric',year:'numeric'});

  const finding=(f:any,i:number)=>`
<div style="margin:10px 0;border-left:4px solid ${NAR_SEV_COLOR[f.severity]||'#555'};padding:10px 14px;background:#f9f9f9;page-break-inside:avoid">
  <div style="margin-bottom:5px"><span style="background:${NAR_SEV_COLOR[f.severity]||'#555'};color:#fff;padding:2px 7px;border-radius:3px;font-size:9pt;font-weight:bold">${f.severity}</span>
    <strong style="margin-left:8px;font-size:11pt">${i+1}. ${f.title}</strong></div>
  <p style="margin:4px 0"><strong>Finding:</strong> ${f.finding}</p>
  ${f.impact?`<p style="margin:4px 0"><strong>Impact:</strong> ${f.impact}</p>`:''}
  <p style="margin:4px 0"><strong>Recommended Action:</strong> ${f.recommendation}</p>
</div>`;

  const row=(label:string,value:string)=>`
<tr><td style="padding:5px 10px;border:1px solid #ccc;color:#555;font-size:10pt">${label}</td>
    <td style="padding:5px 10px;border:1px solid #ccc;font-weight:bold;font-size:10pt">${value}</td></tr>`;

  const st=n.summary_stats||{};
  return `
<h1 style="text-align:center;color:#1a3a5c;font-size:20pt;margin:0 0 4px">${n.report_title||'Schedule Performance Narrative'}</h1>
<p style="text-align:center;color:#555;font-size:10pt;margin:0">Auto-generated by ${n.prepared_by||'ScheduleIQ'} &nbsp;|&nbsp; ${today}${n.company?` &nbsp;|&nbsp; ${n.company}`:''}</p>
<table width="100%" style="border-collapse:collapse;margin:16px 0">
  <tr><td style="background:${statusColor};color:#fff;text-align:center;padding:10px;font-size:14pt;font-weight:bold;border-radius:6px">
    ${NAR_STATUS_ICON[n.status]||''} Overall Status: ${statusLabel}
    &nbsp;·&nbsp; Confidence: ${n.confidence_level||'—'}
    ${n.risk_score?`&nbsp;·&nbsp; Risk Score: ${n.risk_score}/100`:''}
  </td></tr>
</table>
<table width="100%" style="border-collapse:collapse;margin:10px 0;font-size:10pt">
  ${row('Project',n.project_name||'—')}
  ${row('Data Date',dateStr||'—')}
  ${row('Report Date',today)}
  ${n.contract_finish_date?row('Contract Completion',n.contract_finish_date):''}
  ${n.project_forecast_finish?row('Current Forecast Completion',n.project_forecast_finish):''}
  ${n.contract_finish_variance_days!=null?row('Contract Variance (calendar days)',n.contract_finish_variance_days>0?`+${n.contract_finish_variance_days} days (LATE)`:n.contract_finish_variance_days<0?`${n.contract_finish_variance_days} days (EARLY)`:'0 (On Date)'):''}
  ${n.confidence_score!=null?row('Confidence Score',`${n.confidence_score}/100 (${n.confidence_level||'—'})`+''):''}
</table>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">1. Executive Summary</h2>
<p>${s('executive_summary').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">2. Current Schedule Position</h2>
<p>${s('current_position').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">3. Critical Path and Near-Critical Activities</h2>
<p>${s('critical_path_narrative').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">4. Negative Float Analysis</h2>
<p>${s('negative_float_narrative').replace(/\n/g,'</p><p>')}</p>

${n.worst_neg_float_acts?.length?`
<table width="100%" style="border-collapse:collapse;margin:10px 0;font-size:10pt">
  <thead><tr style="background:#1a3a5c;color:#fff">
    <th style="padding:5px 8px;text-align:left">Activity ID</th>
    <th style="padding:5px 8px;text-align:left">Activity Name</th>
    <th style="padding:5px 8px;text-align:left">WBS</th>
    <th style="padding:5px 8px;text-align:right">Float (days)</th>
  </tr></thead>
  <tbody>${(n.worst_neg_float_acts||[]).map((a:any,i:number)=>`
    <tr style="background:${i%2===0?'#f8f9fa':'#fff'}">
      <td style="padding:5px 8px;border:1px solid #ddd;font-family:monospace">${a.code||'—'}</td>
      <td style="padding:5px 8px;border:1px solid #ddd">${a.name||'—'}</td>
      <td style="padding:5px 8px;border:1px solid #ddd">${a.wbs||'—'}</td>
      <td style="padding:5px 8px;border:1px solid #ddd;text-align:right;color:#d93030;font-weight:bold">${a.float!=null?a.float.toFixed(1):'—'}</td>
    </tr>`).join('')}
  </tbody>
</table>`:''}

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">5. Progress Performance</h2>
<p>${s('progress_narrative').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">6. Schedule Quality Findings</h2>
<p>${s('quality_narrative').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">7. Changes Since Previous Update</h2>
<p>${s('trend_narrative').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">8. Key Findings and Recommendations</h2>
${(n.findings||[]).length===0?'<p style="color:#16a34a;font-weight:bold">No schedule alerts were triggered at the current configured thresholds.</p>':
  (n.findings||[]).map(finding).join('')}

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">9. Conclusion</h2>
<p>${s('conclusion').replace(/\n/g,'</p><p>')}</p>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">10. Data Limitations</h2>
<ul>${(n.limitations||[]).map((l:string)=>`<li style="margin:4px 0;font-size:10pt">${l}</li>`).join('')||'<li>No material data limitations were identified.</li>'}</ul>

<h2 style="color:#1a3a5c;border-bottom:2px solid #1a3a5c;padding-bottom:4px">Appendix — Key Schedule Metrics</h2>
<table width="100%" style="border-collapse:collapse;margin:10px 0;font-size:10pt">
  <thead><tr style="background:#1a3a5c;color:#fff">
    <th style="padding:5px 10px;text-align:left">Metric</th>
    <th style="padding:5px 10px;text-align:right">Value</th>
  </tr></thead>
  <tbody>
    ${row('Total Activities (incl. milestones)',String(st.total||0))}
    ${row('Complete Activities',String(st.complete_count||0))}
    ${row('In Progress',String(st.in_progress_count||0))}
    ${row('Not Started',String(st.not_started_count||0))}
    ${row('Milestone Activities',String(st.milestone_count||0))}
    ${row('P6-Critical Activities',`${st.critical_count||0} (${st.critical_pct||0}%)`)}
    ${row('Near-Critical Activities',String(st.near_critical_count||0))}
    ${row('Negative-Float Activities',String(st.neg_float_count||0))}
    ${st.min_float!=null?row('Minimum Total Float',`${(st.min_float||0).toFixed(1)} days`):''}
    ${row('Overdue Activities',`${st.overdue_count||0} (${st.overdue_pct_of_incomplete||0}% of incomplete)`)}
    ${st.quality_score!=null?row('Schedule Quality Score',`${st.quality_score.toFixed(0)}/100`):''}
    ${st.bei_valid?row('Baseline Execution Index (BEI)',`${(st.bei||0).toFixed(2)} (${st.bei_actual_count||0} of ${st.bei_planned_count||0} planned)`):`<tr><td colspan="2" style="padding:5px 10px;font-size:9.5pt;color:#555;border:1px solid #ddd">${n.summary_stats?.bei_valid===false&&n.bei_limitation?n.bei_limitation:'BEI not available.'}</td></tr>`}
  </tbody>
</table>
<p style="color:#555;font-size:9pt;margin-top:24px;border-top:1px solid #e0e0e0;padding-top:8px">
  Auto-generated by ScheduleIQ on ${today}. Results should be reviewed by the project scheduling team before formal issuance.
</p>`;
}

function exportToWordDoc(n:any, edited:Record<string,string>={}){
  const html=`<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  body{font-family:Calibri,Arial,sans-serif;font-size:11pt;color:#111;margin:0;padding:0}
  h1,h2{font-family:Calibri,Arial,sans-serif}
  table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:5px 8px;font-size:10pt}
  th{background:#1a3a5c;color:#fff}
  ol,ul{margin:4px 0 0 20px}li{margin:2px 0}
</style></head><body>${buildNarrativeHTML(n,edited)}</body></html>`;
  const blob=new Blob([html],{type:'application/vnd.openxmlformats-officedocument.wordprocessingml.document;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;
  a.download=`schedule-narrative-${n.report_date||new Date().toISOString().slice(0,10)}.docx`;
  document.body.appendChild(a);a.click();document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function exportToPrintPDF(n:any, edited:Record<string,string>={}){
  const html=`<!DOCTYPE html><html><head><meta charset="UTF-8">
<style>
  @media print{@page{margin:2cm;size:A4}body{font-size:10pt}}
  body{font-family:Calibri,Arial,sans-serif;font-size:11pt;color:#111;padding:20px}
  h1,h2{font-family:Calibri,Arial,sans-serif}
  table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:5px 8px;font-size:9.5pt}
  th{background:#1a3a5c;color:#fff;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  tr:nth-child(even){background:#f8f9fa;-webkit-print-color-adjust:exact;print-color-adjust:exact}
</style></head><body>${buildNarrativeHTML(n,edited)}
<script>window.onload=function(){window.print();}<\/script></body></html>`;
  const win=window.open('','_blank','width=900,height=700');
  if(!win)return;win.document.write(html);win.document.close();
}

// ─── NARRATIVE SECTION EDITOR ─────────────────────────────────────────────────
function NarrativeSectionCard({id,title,icon,text,edited,onEdit,onRestore,disabled}:any){
  const [editing,setEditing]=useState(false);
  const [draft,setDraft]=useState(text);
  const isEdited=edited[id]!==undefined;
  const displayText=edited[id]??text;

  const startEdit=()=>{setDraft(edited[id]??text);setEditing(true);};
  const save=()=>{onEdit(id,draft);setEditing(false);};
  const cancel=()=>{setDraft(edited[id]??text);setEditing(false);};
  const restore=()=>{onRestore(id);setEditing(false);};

  return(
    <Sec title={title} icon={icon}>
      {editing?(
        <div style={{display:'flex',flexDirection:'column',gap:8}}>
          <textarea
            value={draft}
            onChange={e=>setDraft(e.target.value)}
            disabled={disabled}
            style={{width:'100%',minHeight:120,padding:'10px 12px',border:`1px solid ${C.accent}`,
              borderRadius:8,fontSize:13,fontFamily:'inherit',color:C.text,background:C.bg,
              resize:'vertical',outline:'none',boxSizing:'border-box'}}
          />
          <div style={{display:'flex',gap:8}}>
            <button onClick={save} style={{background:C.accent,color:'#fff',border:'none',borderRadius:7,padding:'6px 16px',cursor:'pointer',fontSize:12,fontFamily:'inherit',fontWeight:700}}>Save</button>
            <button onClick={cancel} style={{background:'transparent',border:`1px solid ${C.border}`,color:C.muted,borderRadius:7,padding:'6px 14px',cursor:'pointer',fontSize:12,fontFamily:'inherit'}}>Cancel</button>
            {isEdited&&<button onClick={restore} style={{background:'transparent',border:`1px solid ${C.amber}`,color:C.amber,borderRadius:7,padding:'6px 14px',cursor:'pointer',fontSize:12,fontFamily:'inherit'}}>Restore Original</button>}
          </div>
        </div>
      ):(
        <div style={{position:'relative'}}>
          <div style={{fontSize:13,color:C.muted2,lineHeight:1.8,whiteSpace:'pre-wrap'}}>
            {displayText||<span style={{color:C.muted,fontStyle:'italic'}}>No content generated.</span>}
          </div>
          {!disabled&&(
            <div style={{display:'flex',gap:8,marginTop:8}}>
              <button onClick={startEdit}
                style={{background:`${C.accent}10`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:6,padding:'3px 12px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
                ✏️ Edit
              </button>
              {isEdited&&<span style={{fontSize:10,color:C.amber,alignSelf:'center'}}>✦ User-edited</span>}
            </div>
          )}
        </div>
      )}
    </Sec>
  );
}

// ─── LOGIC / QUALITY CHECK VIEW ───────────────────────────────────────────────
function QualityView({allActivities}:any){
  const [pageNP,setPageNP]=useState(0);
  const [pageNS,setPageNS]=useState(0);
  const [pageI, setPageI] =useState(0);
  const [pageAll,setPageAll]=useState(0);
  const [activeFilter,setActiveFilter]=useState<string|null>(null);
  const PAGE=999999;

  const getPredCount=(a:any):number|null=>{
    if(typeof a.predCount==="number")  return a.predCount;
    if(typeof a.pred_count==="number") return a.pred_count;
    if(Array.isArray(a.predecessors))  return a.predecessors.length;
    return null;
  };
  const getSuccCount=(a:any):number|null=>{
    if(typeof a.succCount==="number")  return a.succCount;
    if(typeof a.succ_count==="number") return a.succ_count;
    if(Array.isArray(a.successors))    return a.successors.length;
    return null;
  };

  const hasData =useMemo(()=>(allActivities||[]).some((a:any)=>getPredCount(a)!==null||getSuccCount(a)!==null),[allActivities]);
  const noPred  =useMemo(()=>(allActivities||[]).filter((a:any)=>getPredCount(a)===0),[allActivities]);
  const noSucc  =useMemo(()=>(allActivities||[]).filter((a:any)=>getSuccCount(a)===0),[allActivities]);
  const isolated=useMemo(()=>(allActivities||[]).filter((a:any)=>getPredCount(a)===0&&getSuccCount(a)===0),[allActivities]);

  const handleFilter=(id:string)=>{
    setActiveFilter(prev=>prev===id?null:id);
    setPageNP(0);setPageNS(0);setPageI(0);setPageAll(0);
  };

  const kpis=[
    {id:"ALL",      label:"Total Activities",   value:(allActivities||[]).length.toLocaleString(), color:C.text,                                   warn:false},
    {id:"NO_PRED",  label:"No Predecessor",      value:noPred.length,   color:noPred.length>0?C.red:C.green,      warn:noPred.length>0},
    {id:"NO_SUCC",  label:"No Successor",        value:noSucc.length,   color:noSucc.length>0?C.amber:C.green,    warn:noSucc.length>0},
    {id:"ISOLATED", label:"Isolated (No Both)",  value:isolated.length, color:isolated.length>0?C.red:C.green,    warn:isolated.length>0},
  ];

  const ActTable=({title,icon,acts,page,setPage,accentColor,emptyMsg}:any)=>(
    <Sec title={`${title} (${acts.length})`} icon={icon}>
      {acts.length===0?(
        <div style={{background:`${C.green}0a`,border:`1px solid ${C.green}30`,borderRadius:10,padding:"16px 20px",color:C.green,fontSize:13,fontWeight:600}}>{emptyMsg}</div>
      ):(
        <>
          <div style={{background:C.card,border:`1px solid ${accentColor}30`,borderRadius:12,overflow:"auto"}}>
            <table style={{width:"100%",borderCollapse:"collapse",fontSize:12,minWidth:760}}>
              <thead>
                <tr style={{background:`${accentColor}08`}}>
                  {["Code","Activity Name","Project","WBS","Type","Duration","BL Start","BL Finish","% Done","Status"].map(h=>(
                    <th key={h} style={{padding:"8px 11px",textAlign:"left",color:C.muted,fontWeight:600,fontSize:10,textTransform:"uppercase",letterSpacing:"0.05em",whiteSpace:"nowrap"}}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {acts.slice(page*PAGE,(page+1)*PAGE).map((a:any,i:number)=>{
                  const bs=a.bStart?new Date(a.bStart):null;
                  const bf=a.bFinish?new Date(a.bFinish):null;
                  return(
                    <tr key={i} style={{borderTop:`1px solid ${C.border}`}}>
                      <td style={{padding:"6px 11px",color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10,whiteSpace:"nowrap"}}>{a.code}</td>
                      <td style={{padding:"6px 11px",color:C.text,maxWidth:240,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{a.name}</td>
                      <td style={{padding:"6px 11px",color:'#111111',fontSize:11,whiteSpace:"nowrap"}}>{a.projectName||a.projectId}</td>
                      <td style={{padding:"6px 11px",color:'#111111',fontSize:11,maxWidth:120,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{a.wbs||"—"}</td>
                      <td style={{padding:"6px 11px",color:'#111111',fontSize:11,whiteSpace:"nowrap"}}>{a.type||"—"}</td>
                      <td style={{padding:"6px 11px",color:'#111111',textAlign:"right"}}>{a.dur||"—"}</td>
                      <td style={{padding:"6px 11px",color:C.muted2,whiteSpace:"nowrap",fontSize:11}}>{fmtDate(bs)}</td>
                      <td style={{padding:"6px 11px",color:C.muted2,whiteSpace:"nowrap",fontSize:11}}>{fmtDate(bf)}</td>
                      <td style={{padding:"6px 11px",minWidth:80}}>
                        <div style={{display:"flex",alignItems:"center",gap:5}}>
                          <div style={{flex:1,background:C.border,borderRadius:3,height:4}}>
                            <div style={{width:`${a.pctComplete||0}%`,background:a.pctComplete>=100?C.green:C.accent,borderRadius:3,height:4}}/>
                          </div>
                          <span style={{color:'#111111',fontSize:10}}>{a.pctComplete||0}%</span>
                        </div>
                      </td>
                      <td style={{padding:"6px 11px"}}>
                        <span style={{fontSize:10,padding:"2px 7px",borderRadius:4,background:a.pctComplete>=100?"rgba(0,229,160,0.12)":a.start?"rgba(0,200,240,0.12)":"rgba(100,116,139,0.12)",color:a.pctComplete>=100?C.green:a.start?C.accent:C.muted2}}>
                          {a.pctComplete>=100?"Complete":a.start?"Active":"Not Started"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {acts.length>PAGE&&(
            <div style={{display:"flex",gap:8,justifyContent:"center",marginTop:10,alignItems:"center"}}>
              <button onClick={()=>setPage((p:number)=>Math.max(0,p-1))} disabled={page===0}
                style={{background:C.card,border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"5px 13px",cursor:"pointer",fontSize:12}}>← Prev</button>
              <span style={{color:C.muted2,fontSize:12}}>Page {page+1} of {Math.ceil(acts.length/PAGE)}</span>
              <button onClick={()=>setPage((p:number)=>Math.min(Math.ceil(acts.length/PAGE)-1,p+1))} disabled={(page+1)*PAGE>=acts.length}
                style={{background:C.card,border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"5px 13px",cursor:"pointer",fontSize:12}}>Next →</button>
            </div>
          )}
        </>
      )}
    </Sec>
  );

  const conceptBanner=(
    <div style={{background:`${C.purple}0a`,border:`1px solid ${C.purple}30`,borderRadius:10,padding:'10px 16px',marginBottom:14,fontSize:12,color:C.muted,display:'flex',alignItems:'center',gap:8}}>
      <span style={{fontSize:14}}>🔗</span>
      <span>This is a live open-ends view — one input into <strong style={{color:C.purple}}>QUALITY</strong> ("is the schedule built correctly?"). The full DCMA-style Quality Score (circular logic, constraints, lags, duration outliers) is shown on the <strong>Status</strong> tab alongside Status and Risk for comparison.</span>
    </div>
  );

  if(!hasData)return(<>
    {conceptBanner}
    <div style={{textAlign:"center",padding:"60px 20px",color:C.muted2}}>
      <div style={{fontSize:36,marginBottom:12}}>🔗</div>
      <div style={{fontSize:16,fontWeight:600,color:C.text,marginBottom:8}}>No Logic Data in Dataset</div>
      <div style={{fontSize:13,maxWidth:520,margin:"0 auto",lineHeight:1.8,color:C.muted2}}>
        Predecessor and successor counts were not found in these activities.<br/>
        The backend must return <code style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:4,padding:"1px 6px",fontSize:12}}>predCount</code> /
        <code style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:4,padding:"1px 6px",fontSize:12,marginLeft:4}}>succCount</code> or
        <code style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:4,padding:"1px 6px",fontSize:12,marginLeft:4}}>predecessors</code> /
        <code style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:4,padding:"1px 6px",fontSize:12,marginLeft:4}}>successors</code> fields with each activity.
      </div>
    </div>
  </>);

  const showAll      = activeFilter===null||activeFilter==="ALL";
  const showNoPred   = activeFilter===null||activeFilter==="NO_PRED";
  const showNoSucc   = activeFilter===null||activeFilter==="NO_SUCC";
  const showIsolated = activeFilter===null||activeFilter==="ISOLATED";

  return(<>
    {conceptBanner}
    <Sec title="Open Ends Check Summary" icon="🔗">
      <div style={{fontSize:11,color:C.muted2,marginBottom:10}}>Click a card to filter the list below · click again to show all</div>
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(160px,1fr))",gap:9,marginBottom:16}}>
        {kpis.map(k=>(
          <KPI key={k.id} label={k.label} value={k.value} color={k.color} warn={k.warn}
            active={activeFilter===k.id}
            onClick={()=>handleFilter(k.id)}
          />
        ))}
      </div>
      {noPred.length>0&&(activeFilter===null||activeFilter==="NO_PRED")&&<div style={{background:"rgba(255,87,87,0.07)",border:`1px solid ${C.red}30`,borderRadius:8,padding:"10px 14px",fontSize:12,color:C.muted2,marginBottom:8}}>
        <strong style={{color:C.red}}>⚠ {noPred.length} open-start {noPred.length===1?"activity":"activities"}</strong> — no predecessors means these can start at any time without constraint, indicating missing schedule logic.
      </div>}
      {noSucc.length>0&&(activeFilter===null||activeFilter==="NO_SUCC")&&<div style={{background:"rgba(255,181,71,0.07)",border:`1px solid ${C.amber}30`,borderRadius:8,padding:"10px 14px",fontSize:12,color:C.muted2,marginBottom:8}}>
        <strong style={{color:C.amber}}>⚠ {noSucc.length} open-end {noSucc.length===1?"activity":"activities"}</strong> — no successors means these are not driving any downstream work, indicating missing schedule logic.
      </div>}
      {isolated.length>0&&(activeFilter===null||activeFilter==="ISOLATED")&&<div style={{background:"rgba(255,87,87,0.07)",border:`1px solid ${C.red}30`,borderRadius:8,padding:"10px 14px",fontSize:12,color:C.muted2}}>
        <strong style={{color:C.red}}>⚠ {isolated.length} completely isolated {isolated.length===1?"activity":"activities"}</strong> — no predecessors AND no successors, completely disconnected from the schedule network.
      </div>}
    </Sec>

    {showAll     &&<ActTable title="All Activities"                        icon="📋" acts={allActivities||[]} page={pageAll} setPage={setPageAll} accentColor={C.accent} emptyMsg="No activities found."/>}
    {showNoPred  &&<ActTable title="No Predecessor — Open Start"           icon="⬅"  acts={noPred}            page={pageNP}  setPage={setPageNP}  accentColor={C.red}    emptyMsg="✓ All activities have at least one predecessor."/>}
    {showNoSucc  &&<ActTable title="No Successor — Open End"               icon="➡"  acts={noSucc}            page={pageNS}  setPage={setPageNS}  accentColor={C.amber}  emptyMsg="✓ All activities have at least one successor."/>}
    {showIsolated&&<ActTable title="Isolated — No Predecessor & Successor" icon="🔴" acts={isolated}          page={pageI}   setPage={setPageI}   accentColor={C.red}    emptyMsg="✓ No isolated activities found."/>}
  </>);
}

// ─── NARRATIVE VIEW ───────────────────────────────────────────────────────────
function NarrativeView({M,files,selectedIds,allActivities,dataDate}:any){
  // ── Config ────────────────────────────────────────────────────────────────
  const selFiles=useMemo(()=>files.filter((f:any)=>selectedIds.includes(f.id)),[files,selectedIds]);
  const defaultProject=useMemo(()=>selFiles.map((f:any)=>f.name).join(', ')||'','[selFiles]'.length>0?[selFiles]:[]);

  const [cfg,setCfg]=useState({
    projectName:'',company:'',preparedBy:'ScheduleIQ',
    reportTitle:'Schedule Performance Narrative',
    contractFinishDate:'',showQuality:true,showTrend:true,maxFindings:7,
  });

  // ── Milestone designation ─────────────────────────────────────────────────
  const [msList,setMsList]=useState<{actId:string;desc:string;contractDate:string;isContractual:boolean}[]>([]);
  const [showAddMs,setShowAddMs]=useState(false);
  const [newMs,setNewMs]=useState({actId:'',desc:'',contractDate:'',isContractual:true});

  // ── Result ────────────────────────────────────────────────────────────────
  const [result,setResult]=useState<any>(null);
  const [loading,setLoading]=useState(false);
  const [err,setErr]=useState<string|null>(null);
  const [editedSections,setEditedSections]=useState<Record<string,string>>({});
  const [editHistory,setEditHistory]=useState<{sectionId:string;timestamp:string;length:number}[]>([]);
  const [exporting,setExporting]=useState<string|null>(null);
  const [locked,setLocked]=useState(false);

  const handleGenerate=useCallback(async()=>{
    if(!(allActivities?.length)){setErr('No activities loaded. Upload a schedule file first.');return;}
    setLoading(true);setErr(null);setResult(null);setEditedSections({});
    const body:any={
      current_activities:allActivities,
      // No "|| today" fallback — the backend must require an explicit Data
      // Date for this narrative's quality/status assessment rather than
      // this call silently substituting today.
      data_date:dataDate||undefined,
      settings:{
        project_name:cfg.projectName||defaultProject,
        company:cfg.company,
        prepared_by:cfg.preparedBy,
        report_title:cfg.reportTitle,
        max_findings:cfg.maxFindings,
      },
    };
    if(cfg.contractFinishDate) body.contract_finish_date=cfg.contractFinishDate;
    if(msList.length) body.milestones=msList.filter(m=>m.actId).map(m=>({
      activityId:m.actId,description:m.desc,
      contractRequiredDate:m.contractDate||undefined,
      isContractual:m.isContractual,
    }));
    try{
      const r=await fetch(`${API}/api/narrative/`,{
        method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),
      });
      const text=await r.text();
      if(!r.ok){let d=text;try{d=JSON.parse(text).error||text;}catch{}throw new Error(d.slice(0,500));}
      setResult(JSON.parse(text));
    }catch(e:any){setErr(e.message||String(e));}
    finally{setLoading(false);}
  },[allActivities,dataDate,cfg,msList,defaultProject]);

  const handleEdit=(id:string,text:string)=>{
    setEditedSections(p=>({...p,[id]:text}));
    setEditHistory(p=>[...p,{sectionId:id,timestamp:new Date().toISOString(),length:text.length}]);
  };
  const handleRestore=(id:string)=>{
    setEditedSections(p=>{const n={...p};delete n[id];return n;});
  };
  const handleRestoreAll=()=>{setEditedSections({});};

  const handleWord=()=>{
    if(!result)return;
    setExporting('word');
    setTimeout(()=>{exportToWordDoc(result,editedSections);setExporting(null);},80);
  };
  const handlePDF=()=>{
    if(!result)return;
    setExporting('pdf');
    setTimeout(()=>{exportToPrintPDF(result,editedSections);setExporting(null);},80);
  };
  const handleCopy=async()=>{
    if(!result)return;
    const sections=['executive_summary','current_position','critical_path_narrative',
      'negative_float_narrative','progress_narrative','quality_narrative','trend_narrative','conclusion'];
    const text=sections.map(s=>`${s.replace(/_/g,' ').toUpperCase()}\n\n${editedSections[s]||result[s]||''}`).join('\n\n---\n\n');
    try{await navigator.clipboard.writeText(text);alert('Copied to clipboard');}catch{}
  };

  if(!(allActivities?.length)){
    return(
      <div style={{textAlign:'center',padding:'60px 20px',color:C.muted2}}>
        <div style={{fontSize:40,marginBottom:12}}>📝</div>
        <div style={{fontWeight:700,fontSize:16,color:C.text,marginBottom:6}}>Schedule Performance Narrative</div>
        <div style={{fontSize:13}}>Upload a schedule file to generate an evidence-based narrative.</div>
      </div>
    );
  }

  const statusColor=result?NAR_STATUS_COLOR[result.status]||C.muted:C.muted;
  const st=result?.summary_stats||{};
  const editedCount=Object.keys(editedSections).length;

  return(
    <div style={{maxWidth:1100}}>

      {/* ── Config panel ── */}
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'18px 22px',marginBottom:16}}>
        <div style={{fontWeight:800,fontSize:14,color:C.text,marginBottom:14}}>📝 Narrative Configuration</div>

        <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:12,marginBottom:14}}>
          {[
            {label:'Project Name',key:'projectName',placeholder:defaultProject||'Auto-detected'},
            {label:'Company / Organisation',key:'company',placeholder:'Optional'},
            {label:'Prepared By',key:'preparedBy',placeholder:'ScheduleIQ'},
            {label:'Report Title',key:'reportTitle',placeholder:'Schedule Performance Narrative'},
          ].map(f=>(
            <div key={f.key}>
              <div style={{fontSize:11,fontWeight:600,color:C.muted,marginBottom:4}}>{f.label}</div>
              <input value={(cfg as any)[f.key]} placeholder={f.placeholder}
                onChange={e=>setCfg(p=>({...p,[f.key]:e.target.value}))}
                style={{width:'100%',border:`1px solid ${C.border}`,borderRadius:6,padding:'6px 10px',
                  fontSize:12,fontFamily:'inherit',background:C.bg,color:C.text,outline:'none',boxSizing:'border-box'}}/>
            </div>
          ))}
          <div>
            <div style={{fontSize:11,fontWeight:600,color:C.muted,marginBottom:4}}>Contract Finish Date</div>
            <input type="date" value={cfg.contractFinishDate}
              onChange={e=>setCfg(p=>({...p,contractFinishDate:e.target.value}))}
              style={{width:'100%',border:`1px solid ${C.border}`,borderRadius:6,padding:'6px 10px',
                fontSize:12,fontFamily:'inherit',background:C.bg,color:C.text,outline:'none',boxSizing:'border-box'}}/>
          </div>
          <div style={{alignSelf:'end'}}>
            <div style={{fontSize:11,fontWeight:600,color:C.muted,marginBottom:4}}>Max Findings</div>
            <select value={cfg.maxFindings} onChange={e=>setCfg(p=>({...p,maxFindings:+e.target.value}))}
              style={{width:'100%',border:`1px solid ${C.border}`,borderRadius:6,padding:'7px 10px',
                fontSize:12,fontFamily:'inherit',background:C.bg,color:C.text,outline:'none'}}>
              {[3,4,5,6,7].map(n=><option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>

        {/* Milestones */}
        <div style={{marginBottom:14}}>
          <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:8}}>
            <span style={{fontSize:12,fontWeight:700,color:C.text}}>Contractual Milestones</span>
            <button onClick={()=>setShowAddMs(v=>!v)}
              style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,
                borderRadius:6,padding:'3px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
              + Add
            </button>
          </div>
          {showAddMs&&(
            <div style={{display:'flex',gap:8,flexWrap:'wrap',background:C.panel,
              borderRadius:8,padding:'10px 14px',marginBottom:8,alignItems:'center'}}>
              <input placeholder="Activity ID" value={newMs.actId} onChange={e=>setNewMs(p=>({...p,actId:e.target.value}))}
                style={{border:`1px solid ${C.border}`,borderRadius:6,padding:'5px 9px',fontSize:11,fontFamily:'inherit',background:C.bg,color:C.text,width:100}}/>
              <input placeholder="Description" value={newMs.desc} onChange={e=>setNewMs(p=>({...p,desc:e.target.value}))}
                style={{border:`1px solid ${C.border}`,borderRadius:6,padding:'5px 9px',fontSize:11,fontFamily:'inherit',background:C.bg,color:C.text,flex:1,minWidth:120}}/>
              <input type="date" value={newMs.contractDate} onChange={e=>setNewMs(p=>({...p,contractDate:e.target.value}))}
                style={{border:`1px solid ${C.border}`,borderRadius:6,padding:'5px 9px',fontSize:11,fontFamily:'inherit',background:C.bg,color:C.text}}/>
              <label style={{display:'flex',alignItems:'center',gap:5,fontSize:11,color:C.text,cursor:'pointer'}}>
                <input type="checkbox" checked={newMs.isContractual} onChange={e=>setNewMs(p=>({...p,isContractual:e.target.checked}))}/>
                Contractual
              </label>
              <button onClick={()=>{if(!newMs.actId)return;setMsList(p=>[...p,{...newMs}]);setNewMs({actId:'',desc:'',contractDate:'',isContractual:true});setShowAddMs(false);}}
                style={{background:C.accent,color:'#fff',border:'none',borderRadius:6,padding:'5px 12px',cursor:'pointer',fontSize:11,fontFamily:'inherit',fontWeight:700}}>
                Save
              </button>
              <button onClick={()=>setShowAddMs(false)}
                style={{background:'transparent',border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:'5px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
                Cancel
              </button>
            </div>
          )}
          {msList.length>0&&(
            <div style={{display:'flex',flexWrap:'wrap',gap:8}}>
              {msList.map((m,i)=>(
                <div key={i} style={{display:'flex',alignItems:'center',gap:6,background:C.panel,
                  borderRadius:7,padding:'4px 10px',fontSize:11,border:`1px solid ${C.border}`}}>
                  <strong style={{color:C.text}}>{m.actId}</strong>
                  {m.desc&&<span style={{color:C.muted}}>{m.desc}</span>}
                  {m.contractDate&&<span style={{color:C.accent}}>{m.contractDate}</span>}
                  {m.isContractual&&<span style={{fontSize:9,background:`${C.gold}22`,color:C.gold,borderRadius:4,padding:'1px 5px',fontWeight:700}}>CONTRACT</span>}
                  <button onClick={()=>setMsList(p=>p.filter((_,j)=>j!==i))}
                    style={{background:'transparent',border:'none',color:C.muted2,cursor:'pointer',fontSize:11,padding:0,lineHeight:1}}>✕</button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
          <button onClick={handleGenerate} disabled={loading}
            style={{background:loading?C.muted:C.accent,color:'#fff',border:'none',borderRadius:9,
              padding:'10px 24px',cursor:loading?'default':'pointer',fontSize:14,fontFamily:'inherit',
              fontWeight:700,opacity:loading?0.7:1}}>
            {loading?'Generating…':'Generate Narrative'}
          </button>
          {result&&<>
            <button onClick={handleWord} disabled={!!exporting}
              style={{background:`${C.accent}10`,border:`1px solid ${C.accent}40`,color:C.accent,
                borderRadius:8,padding:'8px 16px',cursor:'pointer',fontSize:12,fontFamily:'inherit',fontWeight:600}}>
              {exporting==='word'?'Generating…':'📄 Download Word (.docx)'}
            </button>
            <button onClick={handlePDF} disabled={!!exporting}
              style={{background:`${C.gold}10`,border:`1px solid ${C.gold}40`,color:C.gold,
                borderRadius:8,padding:'8px 16px',cursor:'pointer',fontSize:12,fontFamily:'inherit',fontWeight:600}}>
              {exporting==='pdf'?'Generating…':'📑 Export to PDF'}
            </button>
            <button onClick={handleCopy}
              style={{background:`${C.green}10`,border:`1px solid ${C.green}40`,color:C.green,
                borderRadius:8,padding:'8px 16px',cursor:'pointer',fontSize:12,fontFamily:'inherit',fontWeight:600}}>
              📋 Copy Text
            </button>
            {editedCount>0&&(
              <button onClick={handleRestoreAll}
                style={{background:'transparent',border:`1px solid ${C.amber}`,color:C.amber,
                  borderRadius:8,padding:'8px 14px',cursor:'pointer',fontSize:12,fontFamily:'inherit'}}>
                ↩ Restore All Originals ({editedCount})
              </button>
            )}
            <label style={{display:'flex',alignItems:'center',gap:6,fontSize:12,color:C.muted,cursor:'pointer',marginLeft:4}}>
              <input type="checkbox" checked={locked} onChange={e=>setLocked(e.target.checked)}/>
              🔒 Lock narrative
            </label>
          </>}
        </div>
      </div>

      {/* ── Error ── */}
      {err&&(
        <div style={{background:`${C.red}10`,border:`1px solid ${C.red}40`,borderRadius:10,padding:'14px 18px',marginBottom:16}}>
          <div style={{fontWeight:700,color:C.red,marginBottom:4}}>Narrative generation failed</div>
          <pre style={{fontSize:11,color:C.amber,margin:0,whiteSpace:'pre-wrap',wordBreak:'break-all'}}>{err}</pre>
        </div>
      )}

      {/* ── Result ── */}
      {result&&(
        <div style={{display:'flex',flexDirection:'column',gap:14}}>

          {/* Status header */}
          <div style={{background:C.card,border:`2px solid ${statusColor}30`,borderRadius:12,padding:'20px 24px'}}>
            <div style={{display:'flex',alignItems:'flex-start',gap:24,flexWrap:'wrap'}}>
              <div style={{flex:1,minWidth:200}}>
                <div style={{fontSize:10,fontWeight:700,color:C.muted,textTransform:'uppercase',letterSpacing:'0.08em',marginBottom:8}}>Overall Status</div>
                <div style={{display:'inline-flex',alignItems:'center',gap:8,padding:'8px 18px',borderRadius:10,
                  background:`${statusColor}15`,border:`2px solid ${statusColor}`}}>
                  <span style={{fontSize:18}}>{NAR_STATUS_ICON[result.status]||'❓'}</span>
                  <span style={{fontSize:18,fontWeight:900,color:statusColor}}>{NAR_STATUS_LABEL[result.status]||result.status}</span>
                </div>
                <div style={{fontSize:11,color:C.muted,marginTop:8}}>
                  Data date: <strong style={{color:C.text}}>{result.data_date||'—'}</strong>
                  &nbsp;·&nbsp; Report: <strong style={{color:C.text}}>{result.report_date}</strong>
                  &nbsp;·&nbsp; {result.prepared_by}
                  {result.company&&<>&nbsp;·&nbsp; {result.company}</>}
                </div>
              </div>
              <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:10}}>
                {[
                  {l:'Risk Score',v:`${(result.risk_score||0).toFixed(0)}/100`,c:result.risk_score>60?C.red:result.risk_score>35?C.amber:C.green},
                  {l:'Confidence',v:`${(result.confidence_score||0).toFixed(0)}/100`,c:C.accent},
                  {l:'Quality',v:result.summary_stats?.quality_score!=null?`${(result.summary_stats.quality_score||0).toFixed(0)}/100`:'N/A',
                    c:result.summary_stats?.quality_score<60?C.red:result.summary_stats?.quality_score<80?C.amber:C.green},
                ].map((k,i)=>(
                  <div key={i} style={{background:C.card2,borderRadius:8,padding:'10px 14px',textAlign:'center'}}>
                    <div style={{fontSize:9,color:C.muted,textTransform:'uppercase',letterSpacing:'0.06em',marginBottom:3}}>{k.l}</div>
                    <div style={{fontSize:20,fontWeight:900,color:k.c,fontFamily:"'DM Mono',monospace"}}>{k.v}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Key stats row */}
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(130px,1fr))',gap:8,marginTop:14}}>
              {[
                {l:'Total Activities',v:st.total},
                {l:'% Complete',v:st.total_non_ms?`${(st.complete_count/st.total_non_ms*100).toFixed(1)}%`:null},
                {l:'Critical',v:`${st.critical_count} (${st.critical_pct}%)`,c:st.critical_count>0?C.red:C.green},
                {l:'Near-Critical',v:st.near_critical_count,c:st.near_critical_count>0?C.amber:C.green},
                {l:'Negative Float',v:st.neg_float_count,c:st.neg_float_count>0?C.red:C.green},
                {l:'Overdue',v:st.overdue_count,c:st.overdue_count>0?C.amber:C.green},
                {l:'BEI',v:st.bei_valid&&st.bei!=null?st.bei.toFixed(2):'N/A',
                  c:st.bei_valid?st.bei>=0.95?C.green:st.bei>=0.8?C.amber:C.red:C.muted},
              ].filter(k=>k.v!=null&&k.v!==undefined).map((k,i)=>(
                <div key={i} style={{background:C.panel,borderRadius:8,padding:'8px 12px'}}>
                  <div style={{fontSize:9,color:C.muted,textTransform:'uppercase',letterSpacing:'0.05em',marginBottom:2}}>{k.l}</div>
                  <div style={{fontSize:16,fontWeight:900,color:(k as any).c||C.text,lineHeight:1}}>{k.v}</div>
                </div>
              ))}
            </div>

            {result.contract_finish_variance_days!=null&&(
              <div style={{marginTop:12,fontSize:12,padding:'8px 14px',borderRadius:8,
                background:result.contract_finish_variance_days>0?`${C.red}12`:`${C.green}10`,
                border:`1px solid ${result.contract_finish_variance_days>0?C.red:C.green}30`,color:result.contract_finish_variance_days>0?C.red:C.green}}>
                Contract variance: <strong>{result.contract_finish_variance_days>0?'+':''}{result.contract_finish_variance_days} calendar days</strong>
                {result.contract_finish_date&&<span style={{marginLeft:8,fontWeight:400,color:C.muted}}>vs. contract completion {result.contract_finish_date}</span>}
              </div>
            )}
          </div>

          {/* Editable sections */}
          {[
            {id:'executive_summary',   title:'Executive Summary',                    icon:'📋'},
            {id:'current_position',    title:'Current Schedule Position',            icon:'📅'},
            {id:'critical_path_narrative',title:'Critical Path and Near-Critical Activities',icon:'🔴'},
            {id:'negative_float_narrative',title:'Negative Float Analysis',          icon:'📉'},
            {id:'progress_narrative',  title:'Progress Performance',                 icon:'📊'},
            ...(cfg.showQuality?[{id:'quality_narrative',title:'Schedule Quality Findings',icon:'🔗'}]:[]),
            ...(cfg.showTrend?[{id:'trend_narrative',title:'Changes Since Previous Update',icon:'📈'}]:[]),
          ].map(s=>(
            <NarrativeSectionCard key={s.id} id={s.id} title={s.title} icon={s.icon}
              text={result[s.id]||''} edited={editedSections}
              onEdit={handleEdit} onRestore={handleRestore} disabled={locked}/>
          ))}

          {/* Key Findings */}
          <Sec title="Key Findings" icon="🔍">
            {!(result.findings?.length)?(
              <div style={{background:`${C.green}09`,border:`1px solid ${C.green}30`,borderRadius:10,padding:'16px 20px',color:C.green,fontWeight:600}}>
                No schedule alerts were triggered at the current configured thresholds.
              </div>
            ):result.findings.map((f:any,i:number)=>{
              const col=NAR_SEV_COLOR[f.severity]||C.muted;
              return(
                <div key={i} style={{borderLeft:`4px solid ${col}`,padding:'12px 16px',marginBottom:10,
                  background:`${col}08`,borderRadius:'0 8px 8px 0'}}>
                  <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:6}}>
                    <span style={{background:col,color:'#fff',fontSize:9,fontWeight:700,padding:'2px 8px',borderRadius:4,letterSpacing:'0.06em'}}>{f.severity}</span>
                    <span style={{fontSize:13,fontWeight:700,color:C.text}}>{i+1}. {f.title}</span>
                  </div>
                  <div style={{fontSize:12,color:C.muted2,marginBottom:4,lineHeight:1.7}}><strong style={{color:C.text}}>Finding:</strong> {f.finding}</div>
                  {f.impact&&<div style={{fontSize:12,color:C.muted2,marginBottom:4,lineHeight:1.7}}><strong style={{color:C.text}}>Impact:</strong> {f.impact}</div>}
                  <div style={{fontSize:12,color:C.muted2,lineHeight:1.7}}><strong style={{color:C.text}}>Recommended Action:</strong> {f.recommendation}</div>
                </div>
              );
            })}
          </Sec>

          {/* Negative-float supporting table */}
          {result.worst_neg_float_acts?.length>0&&(
            <Sec title="Most Negative Float Activities (Representative)" icon="📉">
              <div style={{fontSize:11,color:C.muted,marginBottom:8}}>
                These activities represent the most negative float values in the schedule. They may share a common controlling constraint — refer to the negative-float narrative above for context.
              </div>
              <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,overflow:'auto'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:12,minWidth:480}}>
                  <thead><tr style={{background:`${C.red}08`}}>
                    {['Activity ID','Activity Name','WBS','Float (days)'].map(h=>(
                      <th key={h} style={{padding:'8px 12px',textAlign:'left',color:C.muted,fontWeight:600,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em'}}>{h}</th>
                    ))}
                  </tr></thead>
                  <tbody>{result.worst_neg_float_acts.map((a:any,i:number)=>(
                    <tr key={i} style={{borderTop:`1px solid ${C.border}`}}>
                      <td style={{padding:'8px 12px',fontFamily:"'DM Mono',monospace",fontSize:11,color:C.text}}>{a.code||'—'}</td>
                      <td style={{padding:'8px 12px',color:C.text,maxWidth:260,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{a.name||'—'}</td>
                      <td style={{padding:'8px 12px',color:C.muted2,fontSize:11}}>{a.wbs||'—'}</td>
                      <td style={{padding:'8px 12px',color:C.red,fontWeight:700,fontFamily:"'DM Mono',monospace"}}>{a.float!=null?a.float.toFixed(1):'—'}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </Sec>
          )}

          {/* Recommendations */}
          {result.recommendations?.length>0&&(
            <NarrativeSectionCard id="recommendations" title="Recommended Actions" icon="✅"
              text={result.recommendations.map((r:string,i:number)=>`${i+1}. ${r}`).join('\n\n')}
              edited={editedSections} onEdit={handleEdit} onRestore={handleRestore} disabled={locked}/>
          )}

          {/* Conclusion */}
          <NarrativeSectionCard id="conclusion" title="Conclusion" icon="📌"
            text={result.conclusion||''} edited={editedSections}
            onEdit={handleEdit} onRestore={handleRestore} disabled={locked}/>

          {/* Data limitations */}
          {result.limitations?.length>0&&(
            <Sec title="Data Limitations and Confidence" icon="⚠️">
              <div style={{fontSize:11,fontWeight:600,color:C.muted,marginBottom:8}}>
                Confidence level: <strong style={{color:result.confidence_score>=75?C.green:result.confidence_score>=50?C.amber:C.red}}>
                  {result.confidence_level||'—'}</strong> ({(result.confidence_score||0).toFixed(0)}/100)
              </div>
              <ul style={{margin:0,paddingLeft:18}}>
                {result.limitations.map((l:string,i:number)=>(
                  <li key={i} style={{fontSize:12,color:C.muted2,marginBottom:6,lineHeight:1.6}}>{l}</li>
                ))}
              </ul>
            </Sec>
          )}

          {/* Edit history */}
          {editHistory.length>0&&(
            <Sec title="Edit History" icon="📜">
              <div style={{fontSize:11,color:C.muted,marginBottom:6}}>
                {editHistory.length} edit{editHistory.length>1?'s':''} recorded this session.
                Auto-generated content is labelled separately from user-edited content in exports.
              </div>
              <div style={{maxHeight:160,overflow:'auto'}}>
                {editHistory.slice().reverse().map((e,i)=>(
                  <div key={i} style={{fontSize:11,color:C.muted2,padding:'4px 0',borderBottom:`1px solid ${C.border}`}}>
                    <strong style={{color:C.text}}>{e.sectionId.replace(/_/g,' ')}</strong>
                    &nbsp;— edited at {new Date(e.timestamp).toLocaleTimeString()} ({e.length} chars)
                  </div>
                ))}
              </div>
            </Sec>
          )}

          <div style={{fontSize:11,color:C.muted,marginTop:4,lineHeight:1.5}}>
            Auto-generated by ScheduleIQ on {result.report_date}.
            All metrics are derived directly from the uploaded schedule data.
            {editedCount>0&&<> &nbsp;·&nbsp; <span style={{color:C.amber}}>This narrative contains {editedCount} user-edited section{editedCount>1?'s':''}.</span></>}
            {locked&&<> &nbsp;·&nbsp; <span style={{color:C.green}}>🔒 Narrative is locked.</span></>}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── HOME UPLOAD PANEL (shown inside the dashboard shell when no data) ────────
function HomeUploadPanel({onLoad,onSelectFiles}:any){
  const [drag,setDrag]=useState(false);
  const loading=false;
  const errs:string[]=[];
  const ref=useRef<HTMLInputElement>(null);

  const go=(fl:File[])=>{
    if(fl.length)onSelectFiles(fl);
  };

  return(
    <div style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"70vh",gap:20,padding:24}}>
      <div style={{textAlign:"center",marginBottom:8}}>
        <div style={{fontSize:15,fontWeight:700,color:C.text,marginBottom:6}}>Upload your schedule files to begin</div>
        <div style={{fontSize:13,color:C.muted2,maxWidth:420,lineHeight:1.6}}>
          Drag &amp; drop one or more files below, or click to browse. Supports XER, Excel, CSV, XML and PDF formats.
        </div>
      </div>

      <div
        onDragOver={e=>{e.preventDefault();setDrag(true);}}
        onDragLeave={()=>setDrag(false)}
        onDrop={e=>{e.preventDefault();setDrag(false);go(Array.from(e.dataTransfer.files));}}
        onClick={()=>ref.current?.click()}
        style={{width:"100%",maxWidth:560,border:`2px dashed ${drag?C.accent:C.border}`,borderRadius:16,padding:"44px 32px",cursor:"pointer",background:drag?"rgba(0,200,240,0.04)":C.card,transition:"all 0.2s",textAlign:"center"}}
      >
        <input ref={ref} type="file" accept=".xer,.xlsx,.xls,.csv,.xml,.pdf,.mpp" multiple onChange={e=>go(Array.from(e.target.files||[]))} style={{display:"none"}}/>
        <div style={{fontSize:36,marginBottom:10}}>📂</div>
        {loading
          ?<div style={{color:C.accent,fontSize:14}}>Parsing files…</div>
          :<>
            <div style={{color:C.text,fontSize:15,fontWeight:600,marginBottom:6}}>Drop files here or click to browse</div>
            <div style={{fontSize:12,color:C.muted,lineHeight:1.9}}>
              <span style={{color:C.accent,fontWeight:600}}>.XER</span> (Primavera P6) ·{' '}
              <span style={{color:C.green,fontWeight:600}}>.XLSX / .XLS / .CSV</span> ·{' '}
              <span style={{color:C.gold,fontWeight:600}}>.XML</span> (MS Project) ·{' '}
              <span style={{color:C.purple,fontWeight:600}}>.PDF</span> (schedule export)
            </div>
          </>}
      </div>

      {errs.length>0&&(
        <div style={{maxWidth:560,width:"100%",color:C.amber,background:"rgba(255,181,71,0.07)",border:`1px solid rgba(255,181,71,0.2)`,borderRadius:8,padding:"8px 14px",fontSize:12}}>
          {errs.map((e,i)=><div key={i}>{e}</div>)}
        </div>
      )}

      <div style={{display:"flex",alignItems:"center",gap:12}}>
        <div style={{height:1,width:60,background:C.border}}/>
        <span style={{fontSize:12,color:C.muted}}>or</span>
        <div style={{height:1,width:60,background:C.border}}/>
      </div>

      <button onClick={()=>onLoad(generateDemo())}
        style={{background:"rgba(212,168,67,0.08)",border:`1px solid ${C.gold}`,color:C.gold,borderRadius:10,padding:"11px 28px",cursor:"pointer",fontSize:14,fontFamily:"inherit",fontWeight:600,transition:"all 0.15s"}}
        onMouseEnter={e=>(e.currentTarget as HTMLElement).style.background="rgba(212,168,67,0.15)"}
        onMouseLeave={e=>(e.currentTarget as HTMLElement).style.background="rgba(212,168,67,0.08)"}
      >
        Load 5-project demo →
      </button>

      <div style={{display:"flex",gap:12,maxWidth:560,width:"100%",marginTop:4}}>
        {[{icon:"📁",t:"6 file formats",d:"XER, Excel, CSV, XML, PDF"},{icon:"🔀",t:"Portfolio analysis",d:"Multi-project comparison"},{icon:"📝",t:"AI Narrative",d:"Auto-generated report"}].map((c,i)=>(
          <div key={i} style={{flex:"1 1 140px",background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:"12px",textAlign:"center"}}>
            <div style={{fontSize:20,marginBottom:4}}>{c.icon}</div>
            <div style={{fontSize:11,fontWeight:600,color:C.text,marginBottom:2}}>{c.t}</div>
            <div style={{fontSize:10,color:C.muted2}}>{c.d}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── TIME IMPACT ANALYSIS VIEW ────────────────────────────────────────────────
function TIAView({M,allActivities}:any){
  const acts=allActivities||[];
  const nonMS=acts.filter((a:any)=>!a.isMilestone);

  type ImpactEvent={id:string;description:string;responsible:string;startDate:string;duration:number;affectedWBS:string;estimatedImpact:number;type:string;};
  const RESP_OPTIONS=["Contractor","Owner","Third Party","Weather / Force Majeure","Design / Engineering","Regulatory","Other"];
  const TYPE_OPTIONS=["Delay","Acceleration","Scope Change","Suspension","Variation"];
  const RESP_COLORS:Record<string,string>={
    "Contractor":C.red,"Owner":C.accent,"Third Party":C.purple,
    "Weather / Force Majeure":"#7ca8c8","Design / Engineering":C.amber,
    "Regulatory":"#b07e6b","Other":C.muted2,
  };

  const [events,setEvents]=useState<ImpactEvent[]>([]);
  const [showForm,setShowForm]=useState(false);
  const [draft,setDraft]=useState<Partial<ImpactEvent>>({responsible:"Contractor",type:"Delay",duration:0,estimatedImpact:0,startDate:"",affectedWBS:"",description:""});
  const [selectedWBS,setSelectedWBS]=useState<string|null>(null);
  const [selectedProject,setSelectedProject]=useState<string|null>(null);
  const [expandedActId,setExpandedActId]=useState<string|null>(null);
  const toggleAct=(id:string)=>setExpandedActId(p=>p===id?null:id);

  const actMap=useMemo(()=>{const m:Record<string,any>={};acts.forEach((a:any)=>{m[a.id]=a;});return m;},[acts]);
  const navigateToAct=useCallback((actId:string)=>{
    setExpandedActId(actId);
    setTimeout(()=>document.querySelector(`[data-actid="${CSS.escape(actId)}"]`)?.scrollIntoView({behavior:"smooth",block:"center"}),60);
  },[]);
  const {colW:drillColW,onResizeStart:drillResize,reset:drillReset}=useColResize({code:90,name:220,wbs:110,crit:46,bFinish:100,projFinish:100,finishVar:80,float:70,pct:60});
  const {colW:topColW,onResizeStart:topResize,reset:topReset}=useColResize({code:90,name:220,project:130,wbs:110,crit:46,bFinish:100,projFinish:100,finishVar:80,float:70});
  const DRILL_COLS:[string,string][]=[['code','Code'],['name','Activity Name'],['wbs','WBS'],['crit','Crit'],['bFinish','BL Finish'],['projFinish','Proj Finish'],['finishVar','Finish Var'],['float','Float'],['pct','%']];
  const TOP_COLS:[string,string][]=[['code','Code'],['name','Activity Name'],['project','Project'],['wbs','WBS'],['crit','Crit'],['bFinish','BL Finish'],['projFinish','Proj Finish'],['finishVar','Finish Var'],['float','Float']];
  const drillCols=useColOrder(DRILL_COLS);
  const {visible:drillVisible,hidden:drillHidden}=drillCols;
  const topCols=useColOrder(TOP_COLS);
  const {visible:topVisible,hidden:topHidden}=topCols;

  const blStatus=useMemo(()=>detectBaseline(acts),[acts]);

  const wbsImpact=useMemo(()=>{
    const map:Record<string,{wbs:string,totalDelay:number,count:number,critCount:number}>={};
    nonMS.filter((a:any)=>(a.finishVariance||0)>0).forEach((a:any)=>{
      const w=a.wbs||"Unclassified";
      if(!map[w])map[w]={wbs:w,totalDelay:0,count:0,critCount:0};
      map[w].totalDelay+=(a.finishVariance||0);
      map[w].count++;
      if(a.isCritical)map[w].critCount++;
    });
    return Object.values(map).sort((a,b)=>b.totalDelay-a.totalDelay).slice(0,14);
  },[allActivities]);

  const projImpact=useMemo(()=>{
    const map:Record<string,{name:string,maxDelay:number,critDelay:number,delayed:number,count:number}>={};
    nonMS.filter((a:any)=>a.bFinish).forEach((a:any)=>{
      const pn=a.projectName||a.projectId||"Unknown";
      if(!map[pn])map[pn]={name:pn,maxDelay:0,critDelay:0,delayed:0,count:0};
      const v=a.finishVariance||0;
      map[pn].maxDelay=Math.max(map[pn].maxDelay,v);
      map[pn].count++;
      if(v>0)map[pn].delayed++;
      if(a.isCritical&&v>0)map[pn].critDelay=Math.max(map[pn].critDelay,v);
    });
    return Object.values(map).sort((a,b)=>b.maxDelay-a.maxDelay);
  },[allActivities]);

  const respBreakdown=useMemo(()=>{
    const map:Record<string,number>={};
    events.forEach(e=>{if(!map[e.responsible])map[e.responsible]=0;map[e.responsible]+=Number(e.estimatedImpact)||0;});
    return Object.entries(map).map(([name,value])=>({name,value,fill:RESP_COLORS[name]||C.muted2}));
  },[events]);

  const topDelayed=useMemo(()=>
    nonMS.filter((a:any)=>(a.finishVariance||0)>0)
      .sort((a:any,b:any)=>{if(a.isCritical!==b.isCritical)return a.isCritical?-1:1;return(b.finishVariance||0)-(a.finishVariance||0);})
      .slice(0,60)
  ,[allActivities]);

  const critDelayed=nonMS.filter((a:any)=>a.isCritical&&(a.finishVariance||0)>0).length;
  const maxDelay=nonMS.reduce((m:number,a:any)=>Math.max(m,a.finishVariance||0),0);
  const totalEventImpact=events.reduce((s,e)=>s+(Number(e.estimatedImpact)||0),0);
  const wbsOptions=Array.from(new Set(nonMS.map((a:any)=>a.wbs||"Unclassified").filter(Boolean))).sort() as string[];

  const drillActivities=useMemo(()=>{
    if(!selectedWBS&&!selectedProject)return[];
    return nonMS.filter((a:any)=>{
      const wMatch=!selectedWBS||(a.wbs||"Unclassified")===selectedWBS;
      const pMatch=!selectedProject||(a.projectName||a.projectId)===selectedProject;
      return wMatch&&pMatch&&(a.finishVariance||0)>0;
    }).sort((a:any,b:any)=>{
      if(a.isCritical!==b.isCritical)return a.isCritical?-1:1;
      return(b.finishVariance||0)-(a.finishVariance||0);
    });
  },[allActivities,selectedWBS,selectedProject]);

  const addEvent=()=>{
    if(!draft.description||!draft.startDate)return;
    let impact=Number(draft.estimatedImpact)||0;
    if(!impact&&draft.affectedWBS&&draft.duration){
      const affected=nonMS.filter((a:any)=>(a.wbs||"Unclassified")===draft.affectedWBS);
      const hasCrit=affected.some((a:any)=>a.isCritical);
      const minFloat=affected.reduce((m:number,a:any)=>Math.min(m,(a.adjustedFloat??a.totalFloat??999)),999);
      impact=hasCrit?Number(draft.duration):Math.max(0,Number(draft.duration)-minFloat);
    }
    const ev:ImpactEvent={
      id:Date.now().toString(),
      description:draft.description||"",
      responsible:draft.responsible||"Contractor",
      startDate:draft.startDate||"",
      duration:Number(draft.duration)||0,
      affectedWBS:draft.affectedWBS||"",
      estimatedImpact:impact,
      type:draft.type||"Delay",
    };
    setEvents(prev=>[...prev,ev]);
    setDraft({responsible:"Contractor",type:"Delay",duration:0,estimatedImpact:0,startDate:"",affectedWBS:"",description:""});
    setShowForm(false);
  };
  const removeEvent=(id:string)=>setEvents(prev=>prev.filter(e=>e.id!==id));

  const fmt=(v:number|null)=>v===null||v===undefined?'—':`${v>0?'+':''}${v}d`;
  const fmtDate=(d:any)=>d?(d instanceof Date?d:new Date(d)).toLocaleDateString("en-GB",{day:"2-digit",month:"short",year:"2-digit"}):'—';
  const inpStyle:React.CSSProperties={width:"100%",background:C.card,border:`1px solid ${C.border}`,borderRadius:7,padding:"7px 10px",color:C.text,fontSize:12,fontFamily:"inherit",boxSizing:"border-box"};

  return(<>
    {!blStatus.hasBaseline&&(
      <div style={{margin:'0 0 14px',padding:'10px 16px',borderRadius:10,background:`${C.amber}15`,border:`1px solid ${C.amber}40`,display:'flex',alignItems:'center',gap:10}}>
        <span style={{fontSize:16}}>⚠️</span>
        <div>
          <span style={{fontWeight:700,color:C.amber,fontSize:12}}>{blStatus.status==="Partial Baseline"?"Partial Baseline":"No Baseline"} — TIA accuracy limited</span>
          <span style={{color:C.muted,fontSize:11,marginLeft:8}}>{blStatus.coverage>0?`Only ${blStatus.coverage}% of activities are baselined.`:"No baseline dates found."} Variance-based attribution requires baseline dates.</span>
        </div>
      </div>
    )}

    <Sec title="Time Impact Summary" icon="⏱️">
      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(160px,1fr))",gap:9,marginBottom:18}}>
        <KPI label="Critical Activities Delayed" value={critDelayed}     color={critDelayed>0?C.red:C.green}    warn={critDelayed>0}/>
        <KPI label="Max Single Activity Delay"   value={fmt(maxDelay)}   color={maxDelay>14?C.red:maxDelay>0?C.amber:C.green} warn={maxDelay>0}/>
        <KPI label="Total Activities Delayed"    value={topDelayed.length} color={topDelayed.length>0?C.amber:C.green}/>
        <KPI label="WBS Packages Impacted"       value={wbsImpact.length} color={wbsImpact.length>0?C.amber:C.green}/>
        <KPI label="Logged Impact Events"        value={events.length}   color={C.accent}/>
        <KPI label="Total Logged Impact"         value={totalEventImpact>0?`${totalEventImpact}d`:'—'} color={totalEventImpact>0?C.red:C.muted2} warn={totalEventImpact>0}/>
      </div>
    </Sec>

    <Sec title="Delay Driver Analysis — Auto-detected from Schedule" icon="📊">
      <div style={{marginBottom:8,fontSize:11,color:C.muted}}>Click a bar to drill into its activities</div>
      <div style={{display:"flex",flexWrap:"wrap",gap:12}}>
        <CC title="Delay Contribution by WBS (cumulative days)" flex="2 1 420px" height={Math.max(220,wbsImpact.length*30+60)}>
          <ResponsiveContainer>
            <BarChart layout="vertical" data={wbsImpact} margin={{left:8,right:34}}
              onClick={(d:any)=>{if(d?.activePayload?.[0]){const w=d.activePayload[0].payload.wbs;setSelectedWBS(p=>p===w?null:w);setSelectedProject(null);}}}>
              <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
              <XAxis type="number" tick={{fill:C.muted,fontSize:10}}/>
              <YAxis type="category" dataKey="wbs" tick={{fill:C.muted,fontSize:9}} width={130}/>
              <Tooltip content={<TT/>}/>
              <Bar dataKey="totalDelay" name="Total Delay (days)" radius={[0,4,4,0]} cursor="pointer">
                {wbsImpact.map((e,i)=>{
                  const sel=selectedWBS===e.wbs;
                  const base=e.critCount>0?C.red:C.amber;
                  return <Cell key={i} fill={base} opacity={selectedWBS&&!sel?0.35:1} stroke={sel?"#fff":undefined} strokeWidth={sel?2:0}/>;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </CC>
        {projImpact.length>0&&(
          <CC title="Max Delay by Project (click to filter)" flex="1 1 200px" height={Math.max(220,projImpact.length*30+60)}>
            <ResponsiveContainer>
              <BarChart layout="vertical" data={projImpact} margin={{left:8,right:24}}
                onClick={(d:any)=>{if(d?.activePayload?.[0]){const p=d.activePayload[0].payload.name;setSelectedProject(pp=>pp===p?null:p);setSelectedWBS(null);}}}>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border}/>
                <XAxis type="number" tick={{fill:C.muted,fontSize:10}}/>
                <YAxis type="category" dataKey="name" tick={{fill:C.muted,fontSize:9}} width={100}/>
                <Tooltip content={<TT/>}/>
                <Bar dataKey="maxDelay"  name="Max Delay (days)" radius={[0,4,4,0]} cursor="pointer">
                  {projImpact.map((e,i)=><Cell key={i} fill={C.amber} opacity={selectedProject&&selectedProject!==e.name?0.35:1}/>)}
                </Bar>
                <Bar dataKey="critDelay" name="Critical Path Delay (d)" radius={[0,4,4,0]} cursor="pointer">
                  {projImpact.map((e,i)=><Cell key={i} fill={C.red} opacity={selectedProject&&selectedProject!==e.name?0.35:1}/>)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CC>
        )}
      </div>

      {/* Drill-down activity list */}
      {(selectedWBS||selectedProject)&&(
        <div style={{marginTop:14,background:C.card2,border:`1px solid ${C.border}`,borderRadius:12,overflow:"hidden"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"10px 14px",borderBottom:`1px solid ${C.border}`,background:C.panel}}>
            <span style={{fontSize:12,fontWeight:700,color:C.text}}>
              {selectedWBS?`WBS: ${selectedWBS}`:`Project: ${selectedProject}`}
              <span style={{fontWeight:400,color:C.muted,marginLeft:8}}>{drillActivities.length} delayed activit{drillActivities.length===1?"y":"ies"}</span>
            </span>
            <button onClick={()=>{setSelectedWBS(null);setSelectedProject(null);}}
              style={{background:"transparent",border:"none",color:C.muted,cursor:"pointer",fontSize:14,padding:"0 4px",lineHeight:1}}>✕</button>
          </div>
          <div style={{overflowX:"auto",maxHeight:360,overflowY:"auto"}}>
            {drillActivities.length>0?(
              <>
              <div style={{display:"flex",justifyContent:"flex-end",alignItems:"center",padding:"4px 8px 0",gap:6}}>
                {(()=>{
                  const drillCell=(a:any,k:string)=>{const bf=a.bFinish?new Date(a.bFinish):null,pf=a.projectedFinish?new Date(a.projectedFinish):a.finish?new Date(a.finish):null,fv=a.finishVariance??null,fl=a.adjustedFloat??a.totalFloat??null;switch(k){case'code':return a.code||'';case'name':return a.name||'';case'wbs':return a.wbs||'';case'crit':return a.isCritical?'CP':'';case'bFinish':return fmtDateExport(bf);case'projFinish':return fmtDateExport(pf);case'finishVar':return fv==null?'':fv>0?`+${fv}d`:`${fv}d`;case'float':return fl==null?'':String(fl)+'d';case'pct':return`${a.pctComplete||0}%`;default:return'';}};
                  const drillTitle=selectedWBS?`Delay Drivers — ${selectedWBS}`:selectedProject?`Delay Drivers — ${selectedProject}`:'Delay Drivers';
                  return(<>
                    <button type="button" onClick={()=>printTable(drillActivities,drillVisible,drillTitle,'delay-drivers',drillCell)} style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>🖨 PDF</button>
                    <button type="button" onClick={()=>downloadCSV(drillActivities,drillVisible,'delay-drivers',drillCell)} style={{background:`${C.green}14`,border:`1px solid ${C.green}40`,color:C.green,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>📊 Excel</button>
                  </>);
                })()}
                <ColPickerDialog allCols={DRILL_COLS} cols={drillCols}/>
                <button type="button" onClick={drillReset} style={{background:"transparent",border:"none",color:C.muted2,cursor:"pointer",fontSize:11,padding:"2px 6px"}}>↔ Reset</button>
              </div>
              <table style={{tableLayout:"fixed",borderCollapse:"collapse",fontSize:11,width:drillVisible.reduce((s,[k])=>s+drillColW[k],0)}}>
                <colgroup>{drillVisible.map(([k])=><col key={k} style={{width:drillColW[k]}}/>)}</colgroup>
                <thead style={{position:"sticky",top:0,background:C.card2,zIndex:2}}>
                  <tr style={{borderBottom:`1px solid ${C.border}`}}>
                    {DRILL_COLS.map(([key,label])=>{
                      if(drillHidden.has(key))return null;
                      return(<RTh key={key} colKey={key} label={label} colW={drillColW} onResizeStart={drillResize}/>);
                    })}
                  </tr>
                </thead>
                <tbody>
                  {drillActivities.map((a:any)=>{
                    const bf=a.bFinish instanceof Date?a.bFinish:a.bFinish?new Date(a.bFinish):null;
                    const pf=a.projectedFinish instanceof Date?a.projectedFinish:a.projectedFinish?new Date(a.projectedFinish):a.finish?new Date(a.finish):null;
                    const fv=a.finishVariance??null;
                    const fl=a.adjustedFloat??a.totalFloat??null;
                    const fmtD=(d:any)=>d?(d instanceof Date?d:new Date(d)).toLocaleDateString("en-GB",{day:"2-digit",month:"short",year:"2-digit"}):'—';
                    const actId=a.id||a.code;
                    const isExp=expandedActId===actId;
                    const td:React.CSSProperties={padding:"6px 11px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"};
                    return(<Fragment key={actId}>
                      <tr data-actid={actId} onClick={()=>toggleAct(actId)} style={{borderTop:`1px solid ${C.border}`,cursor:"pointer",background:isExp?`${C.accent}0a`:"transparent"}} title="Click to view predecessors & successors">
                        {!drillHidden.has('code')&&<td style={{...td,color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10}}>{a.code}</td>}
                        {!drillHidden.has('name')&&<td style={{...td}} title={a.name}>{a.name}</td>}
                        {!drillHidden.has('wbs')&&<td style={{...td,color:C.muted2,fontSize:10}} title={a.wbs||""}>{a.wbs||"—"}</td>}
                        {!drillHidden.has('crit')&&<td style={{...td,textAlign:"center"}}>{a.isCritical&&<span style={{fontSize:9,padding:"1px 5px",borderRadius:8,background:`${C.red}20`,color:C.red,fontWeight:700}}>CP</span>}</td>}
                        {!drillHidden.has('bFinish')&&<td style={{...td,color:'#111111',fontSize:11}}>{fmtD(bf)}</td>}
                        {!drillHidden.has('projFinish')&&<td style={{...td,color:C.amber,fontSize:11}}>{fmtD(pf)}</td>}
                        {!drillHidden.has('finishVar')&&<td style={{...td,fontWeight:700,fontFamily:"'DM Mono',monospace",color:fv==null?C.muted2:fv>14?C.red:fv>0?C.amber:C.green}}>{fv==null?'—':`+${fv}d`}</td>}
                        {!drillHidden.has('float')&&<td style={{...td,fontFamily:"'DM Mono',monospace",color:fl==null?C.muted2:fl<0?C.red:fl<5?C.amber:C.green}}>{fl==null?'—':`${fl}d`}</td>}
                        {!drillHidden.has('pct')&&<td style={{...td,color:C.muted2,fontFamily:"'DM Mono',monospace"}}>{a.pctComplete||0}%</td>}
                      </tr>
                      {isExp&&<LogicPanel a={a} actMap={actMap} colSpan={drillVisible.length} onNavigate={navigateToAct}/>}
                    </Fragment>);
                  })}
                </tbody>
              </table>
              </>
            ):(
              <div style={{padding:"20px",textAlign:"center",color:C.muted,fontSize:12}}>No delayed activities in this selection.</div>
            )}
          </div>
        </div>
      )}
    </Sec>

    <Sec title="Impact Event Log" icon="📋">
      <div style={{display:"flex",flexWrap:"wrap",gap:12}}>
        <div style={{flex:"2 1 500px"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:10}}>
            <span style={{color:C.muted,fontSize:11}}>{events.length} event{events.length!==1?"s":""} logged{totalEventImpact>0?` · Total logged impact: +${totalEventImpact}d`:""}</span>
            <button onClick={()=>setShowForm(f=>!f)} style={{background:showForm?`${C.accent}20`:"rgba(0,200,240,0.08)",border:`1px solid ${C.accent}`,color:C.accent,borderRadius:8,padding:"6px 14px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:600}}>
              {showForm?"Cancel":"+ Add Event"}
            </button>
          </div>

          {showForm&&(
            <div style={{background:C.card2,border:`1px solid ${C.border}`,borderRadius:12,padding:"14px 16px",marginBottom:12}}>
              <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:10,marginBottom:10}}>
                <div style={{gridColumn:"1/-1"}}>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>DESCRIPTION *</label>
                  <input value={draft.description||""} onChange={e=>setDraft(d=>({...d,description:e.target.value}))}
                    placeholder="e.g. Employer delayed approval of shop drawings"
                    style={inpStyle}/>
                </div>
                <div>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>RESPONSIBLE PARTY</label>
                  <select value={draft.responsible||"Contractor"} onChange={e=>setDraft(d=>({...d,responsible:e.target.value}))} style={inpStyle}>
                    {RESP_OPTIONS.map(r=><option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>EVENT TYPE</label>
                  <select value={draft.type||"Delay"} onChange={e=>setDraft(d=>({...d,type:e.target.value}))} style={inpStyle}>
                    {TYPE_OPTIONS.map(t=><option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>IMPACT START DATE *</label>
                  <input type="date" value={draft.startDate||""} onChange={e=>setDraft(d=>({...d,startDate:e.target.value}))} style={inpStyle}/>
                </div>
                <div>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>DURATION (calendar days)</label>
                  <input type="number" min={0} value={draft.duration||""} onChange={e=>setDraft(d=>({...d,duration:Number(e.target.value)}))} style={inpStyle}/>
                </div>
                <div>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>AFFECTED WBS</label>
                  <select value={draft.affectedWBS||""} onChange={e=>setDraft(d=>({...d,affectedWBS:e.target.value}))} style={inpStyle}>
                    <option value="">— all / unspecified —</option>
                    {wbsOptions.map((w:string)=><option key={w} value={w}>{w}</option>)}
                  </select>
                </div>
                <div>
                  <label style={{fontSize:10,color:C.muted,display:"block",marginBottom:3}}>ESTIMATED TIME IMPACT (days) <span style={{color:C.muted2,fontSize:9}}>auto-computed if blank</span></label>
                  <input type="number" min={0} value={draft.estimatedImpact||""} onChange={e=>setDraft(d=>({...d,estimatedImpact:Number(e.target.value)}))} style={inpStyle}/>
                </div>
              </div>
              <button onClick={addEvent} style={{background:`${C.green}18`,border:`1px solid ${C.green}60`,color:C.green,borderRadius:8,padding:"7px 18px",cursor:"pointer",fontSize:12,fontFamily:"inherit",fontWeight:700}}>
                Add to Log
              </button>
            </div>
          )}

          {events.length>0?(
            <div style={{overflowX:"auto"}}>
              <table style={{width:"100%",borderCollapse:"collapse",fontSize:11}}>
                <thead>
                  <tr style={{borderBottom:`1px solid ${C.border}`}}>
                    {["#","Description","Responsible","Type","Start","Duration","WBS","Impact",""].map(h=>(
                      <th key={h} style={{padding:"6px 10px",color:C.muted,fontWeight:600,textAlign:"left",fontSize:10,whiteSpace:"nowrap"}}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {events.map((e,i)=>(
                    <tr key={e.id} style={{borderTop:`1px solid ${C.border}`}}>
                      <td style={{padding:"7px 10px",color:C.muted2,fontFamily:"'DM Mono',monospace"}}>{i+1}</td>
                      <td style={{padding:"7px 10px",color:C.text,maxWidth:200,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{e.description}</td>
                      <td style={{padding:"7px 10px",whiteSpace:"nowrap"}}>
                        <span style={{fontSize:10,padding:"2px 7px",borderRadius:10,background:`${RESP_COLORS[e.responsible]||C.muted2}20`,color:RESP_COLORS[e.responsible]||C.muted2,fontWeight:700}}>{e.responsible}</span>
                      </td>
                      <td style={{padding:"7px 10px",color:C.muted2,whiteSpace:"nowrap",fontSize:10}}>{e.type}</td>
                      <td style={{padding:"7px 10px",color:C.muted2,fontFamily:"'DM Mono',monospace",whiteSpace:"nowrap"}}>{e.startDate}</td>
                      <td style={{padding:"7px 10px",color:C.muted2,fontFamily:"'DM Mono',monospace",whiteSpace:"nowrap"}}>{e.duration}d</td>
                      <td style={{padding:"7px 10px",color:C.muted2,whiteSpace:"nowrap",maxWidth:120,overflow:"hidden",textOverflow:"ellipsis"}}>{e.affectedWBS||"—"}</td>
                      <td style={{padding:"7px 10px",fontWeight:700,fontFamily:"'DM Mono',monospace",color:e.estimatedImpact>0?C.red:C.muted2}}>{e.estimatedImpact>0?`+${e.estimatedImpact}d`:'—'}</td>
                      <td style={{padding:"7px 10px"}}><button onClick={()=>removeEvent(e.id)} style={{background:"transparent",border:"none",color:C.muted,cursor:"pointer",fontSize:13,padding:0}}>✕</button></td>
                    </tr>
                  ))}
                  <tr style={{borderTop:`2px solid ${C.border}`}}>
                    <td colSpan={7} style={{padding:"7px 10px",color:C.muted,fontWeight:700,textAlign:"right",fontSize:11}}>Total Logged Impact</td>
                    <td style={{padding:"7px 10px",fontWeight:800,color:totalEventImpact>0?C.red:C.muted2,fontFamily:"'DM Mono',monospace"}}>{totalEventImpact>0?`+${totalEventImpact}d`:'—'}</td>
                    <td/>
                  </tr>
                </tbody>
              </table>
            </div>
          ):(
            <div style={{textAlign:"center",padding:"28px 0",color:C.muted,fontSize:12}}>
              No impact events logged yet. Click <strong>+ Add Event</strong> to record a delay event with responsible party and estimated time impact.
            </div>
          )}
        </div>

        {events.length>0&&respBreakdown.length>0&&(
          <CC title="Impact by Responsible Party (days)" flex="1 1 200px" height={270}>
            <ResponsiveContainer>
              <PieChart>
                <Pie data={respBreakdown} cx="50%" cy="50%" outerRadius={82} innerRadius={38} dataKey="value"
                  labelLine={false} label={({name,value}:any)=>`${name.split(" ")[0]}: ${value}d`} fontSize={10}>
                  {respBreakdown.map((e,i)=><Cell key={i} fill={e.fill}/>)}
                </Pie>
                <Tooltip content={<TT/>}/>
                <Legend iconSize={9} wrapperStyle={{fontSize:10}}/>
              </PieChart>
            </ResponsiveContainer>
          </CC>
        )}
      </div>
    </Sec>

    <Sec title="Impacted Activities — Critical Path First" icon="🔴">
      <div style={{overflowX:"auto",maxHeight:500,overflowY:"auto"}}>
        {topDelayed.length>0?(
          <>
          <div style={{display:"flex",justifyContent:"flex-end",alignItems:"center",padding:"4px 8px 0",gap:6}}>
            {(()=>{
              const topCell=(a:any,k:string)=>{const bf=a.bFinish?new Date(a.bFinish):null,pf=a.projectedFinish?new Date(a.projectedFinish):a.finish?new Date(a.finish):null,fv=a.finishVariance??null,fl=a.adjustedFloat??a.totalFloat??null;switch(k){case'code':return a.code||'';case'name':return a.name||'';case'project':return a.projectName||a.projectId||'';case'wbs':return a.wbs||'';case'crit':return a.isCritical?'CP':'';case'bFinish':return fmtDateExport(bf);case'projFinish':return fmtDateExport(pf);case'finishVar':return fv==null?'':fv>0?`+${fv}d`:`${fv}d`;case'float':return fl==null?'':String(fl)+'d';default:return'';}};
              return(<>
                <button type="button" onClick={()=>printTable(topDelayed,topVisible,'Impacted Activities — Critical Path First','impacted-activities',topCell)} style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>🖨 PDF</button>
                <button type="button" onClick={()=>downloadCSV(topDelayed,topVisible,'impacted-activities',topCell)} style={{background:`${C.green}14`,border:`1px solid ${C.green}40`,color:C.green,borderRadius:7,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>📊 Excel</button>
              </>);
            })()}
            <ColPickerDialog allCols={TOP_COLS} cols={topCols}/>
            <button type="button" onClick={topReset} style={{background:"transparent",border:"none",color:C.muted2,cursor:"pointer",fontSize:11,padding:"2px 6px"}}>↔ Reset</button>
          </div>
          <table style={{tableLayout:"fixed",borderCollapse:"collapse",fontSize:11,width:topVisible.reduce((s,[k])=>s+topColW[k],0)}}>
            <colgroup>{topVisible.map(([k])=><col key={k} style={{width:topColW[k]}}/>)}</colgroup>
            <thead style={{position:"sticky",top:0,background:C.card2,zIndex:2}}>
              <tr style={{borderBottom:`1px solid ${C.border}`}}>
                {TOP_COLS.map(([key,label])=>{
                  if(topHidden.has(key))return null;
                  return(<RTh key={key} colKey={key} label={label} colW={topColW} onResizeStart={topResize}/>);
                })}
              </tr>
            </thead>
            <tbody>
              {topDelayed.map((a:any)=>{
                const bf=a.bFinish instanceof Date?a.bFinish:a.bFinish?new Date(a.bFinish):null;
                const pf=a.projectedFinish instanceof Date?a.projectedFinish:a.projectedFinish?new Date(a.projectedFinish):a.finish?new Date(a.finish):null;
                const fv=a.finishVariance??null;
                const fl=a.adjustedFloat??a.totalFloat??null;
                const actId=a.id||a.code;
                const isExp=expandedActId===actId;
                const td:React.CSSProperties={padding:"7px 11px",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"};
                return(<Fragment key={actId}>
                  <tr data-actid={actId} onClick={()=>toggleAct(actId)} style={{borderTop:`1px solid ${C.border}`,cursor:"pointer",background:isExp?`${C.accent}0a`:"transparent"}} title="Click to view predecessors & successors">
                    {!topHidden.has('code')&&<td style={{...td,color:'#111111',fontFamily:"'DM Mono',monospace",fontSize:10}}>{a.code}</td>}
                    {!topHidden.has('name')&&<td style={{...td}} title={a.name}>{a.name}</td>}
                    {!topHidden.has('project')&&<td style={{...td,color:C.muted2,fontSize:10}} title={a.projectName||a.projectId}>{a.projectName||a.projectId}</td>}
                    {!topHidden.has('wbs')&&<td style={{...td,color:C.muted2,fontSize:10}} title={a.wbs||""}>{a.wbs||"—"}</td>}
                    {!topHidden.has('crit')&&<td style={{...td,textAlign:"center"}}>{a.isCritical&&<span style={{fontSize:9,padding:"2px 5px",borderRadius:8,background:`${C.red}20`,color:C.red,fontWeight:700}}>CP</span>}</td>}
                    {!topHidden.has('bFinish')&&<td style={{...td,color:'#111111',fontSize:11}}>{fmtDate(bf)}</td>}
                    {!topHidden.has('projFinish')&&<td style={{...td,color:fv&&fv>0?C.amber:C.muted2,fontSize:11}}>{fmtDate(pf)}</td>}
                    {!topHidden.has('finishVar')&&<td style={{...td,fontWeight:700,fontFamily:"'DM Mono',monospace",color:fv==null?C.muted2:fv>14?C.red:fv>0?C.amber:C.green}}>{fv==null?'—':`+${fv}d`}</td>}
                    {!topHidden.has('float')&&<td style={{...td,fontFamily:"'DM Mono',monospace",color:fl==null?C.muted2:fl<0?C.red:fl<5?C.amber:C.green}}>{fl==null?'—':`${fl}d`}</td>}
                  </tr>
                  {isExp&&<LogicPanel a={a} actMap={actMap} colSpan={topVisible.length} onNavigate={navigateToAct}/>}
                </Fragment>);
              })}
            </tbody>
          </table>
          </>
        ):(
          <div style={{textAlign:"center",padding:"40px 0",color:C.muted,fontSize:12}}>
            No delayed activities detected. Upload a schedule with baseline dates to see delay analysis.
          </div>
        )}
      </div>
    </Sec>
  </>);
}

// ─── POWER BI VIEW ────────────────────────────────────────────────────────────
function PowerBIView({M,allActivities,dataDate}:any){
  const acts=allActivities||[];
  // "Overdue" below is evaluated against the active schedule's own
  // effective Data Date — never today's date.
  const today=parseDate(dataDate);

  const [selStatus,setSelStatus]=useState<Set<string>>(new Set());
  const [selProject,setSelProject]=useState<Set<string>>(new Set());
  const [selPhase,setSelPhase]=useState<Set<string>>(new Set());
  const [critSel,setCritSel]=useState<string|null>(null);
  const [wbsText,setWbsText]=useState('');
  const [dateFrom,setDateFrom]=useState('');
  const [dateTo,setDateTo]=useState('');
  const [xFilt,setXFilt]=useState<{field:string;value:string}|null>(null);
  const [page,setPage]=useState(0);
  const PAGE=999999;

  const projects=useMemo(()=>[...new Set(acts.map((a:any)=>a.projectId||'Unknown'))] as string[],[acts]);

  function getStatus(a:any){
    if((a.pctComplete||0)>=100)return 'Complete';
    if(a.start)return 'In Progress';
    return 'Not Started';
  }
  function getFloatBucket(f:number){
    if(f<0)return '< 0';
    if(f===0)return '= 0';
    if(f<=5)return '1–5';
    if(f<=15)return '6–15';
    return '> 15';
  }

  const filtered=useMemo(()=>acts.filter((a:any)=>{
    if(selStatus.size>0&&!selStatus.has(getStatus(a)))return false;
    if(selProject.size>0&&!selProject.has(a.projectId||'Unknown'))return false;
    if(selPhase.size>0&&!selPhase.has(getEpcPhase(a.wbs||'',a.wbsPath||'').key))return false;
    if(critSel==='critical'&&!a.isCritical)return false;
    if(critSel==='non'&&a.isCritical)return false;
    if(critSel==='near'&&!(0<(a.totalFloat||0)&&(a.totalFloat||0)<=5&&!a.isMilestone))return false;
    if(wbsText&&!(a.wbs||'').toLowerCase().includes(wbsText.toLowerCase()))return false;
    if(dateFrom){const d=parseDate(a.finish||a.bFinish);if(!d||d<new Date(dateFrom))return false;}
    if(dateTo){const d=parseDate(a.finish||a.bFinish);if(!d||d>new Date(dateTo))return false;}
    if(xFilt){
      if(xFilt.field==='status'&&getStatus(a)!==xFilt.value)return false;
      if(xFilt.field==='floatBucket'&&getFloatBucket(a.totalFloat??0)!==xFilt.value)return false;
      if(xFilt.field==='phase'&&getEpcPhase(a.wbs||'').key!==xFilt.value)return false;
      if(xFilt.field==='wbs'&&(a.wbs||'Unassigned')!==xFilt.value)return false;
      if(xFilt.field==='critType'){
        if(xFilt.value==='Critical'&&(!a.isCritical||a.isMilestone))return false;
        if(xFilt.value==='Near-Critical'&&!(0<(a.totalFloat||0)&&(a.totalFloat||0)<=5&&!a.isMilestone))return false;
        if(xFilt.value==='Non-Critical'&&(a.isCritical||(0<(a.totalFloat||0)&&(a.totalFloat||0)<=5)||a.isMilestone))return false;
        if(xFilt.value==='Milestones'&&!a.isMilestone)return false;
      }
    }
    return true;
  }),[acts,selStatus,selProject,selPhase,critSel,wbsText,dateFrom,dateTo,xFilt]);

  useEffect(()=>setPage(0),[filtered]);

  const total=filtered.length;
  const complete=filtered.filter((a:any)=>(a.pctComplete||0)>=100).length;
  const inProg=filtered.filter((a:any)=>a.start&&(a.pctComplete||0)<100).length;
  const notStarted=filtered.filter((a:any)=>!a.start).length;
  const overdue=filtered.filter((a:any)=>{const f=parseDate(a.bFinish||a.finish);return f&&today&&f<today&&(a.pctComplete||0)<100&&!a.isMilestone;}).length;
  const critical=filtered.filter((a:any)=>a.isCritical&&!a.isMilestone).length;
  const totalDur=filtered.reduce((s:number,a:any)=>s+(a.dur||0),0);
  const earnedDur=filtered.reduce((s:number,a:any)=>s+(a.dur||0)*((a.pctComplete||0)/100),0);
  const schedPct=totalDur>0?(earnedDur/totalDur*100):0;
  const bei=complete>0?complete/(complete+overdue):(overdue>0?0:1);
  const avgFloat=total>0?(filtered.reduce((s:number,a:any)=>s+(a.totalFloat??0),0)/total):0;

  const statusData=useMemo(()=>[
    {name:'Not Started',value:filtered.filter((a:any)=>!a.start).length,fill:C.muted2},
    {name:'In Progress',value:filtered.filter((a:any)=>a.start&&(a.pctComplete||0)<100).length,fill:C.accent},
    {name:'Complete',value:filtered.filter((a:any)=>(a.pctComplete||0)>=100).length,fill:C.green},
  ].filter(d=>d.value>0),[filtered]);

  const critData=useMemo(()=>[
    {name:'Critical',value:filtered.filter((a:any)=>a.isCritical&&!a.isMilestone).length,fill:C.red},
    {name:'Near-Critical',value:filtered.filter((a:any)=>0<(a.totalFloat||0)&&(a.totalFloat||0)<=5&&!a.isMilestone).length,fill:C.amber},
    {name:'Non-Critical',value:Math.max(0,filtered.filter((a:any)=>!a.isMilestone).length-filtered.filter((a:any)=>a.isCritical&&!a.isMilestone).length-filtered.filter((a:any)=>0<(a.totalFloat||0)&&(a.totalFloat||0)<=5&&!a.isMilestone).length),fill:C.green},
    {name:'Milestones',value:filtered.filter((a:any)=>a.isMilestone).length,fill:C.purple},
  ].filter(d=>d.value>0),[filtered]);

  const floatData=useMemo(()=>[
    {range:'< 0', count:filtered.filter((a:any)=>(a.totalFloat??0)<0).length,   fill:C.red},
    {range:'= 0', count:filtered.filter((a:any)=>(a.totalFloat??0)===0).length, fill:C.amber},
    {range:'1–5', count:filtered.filter((a:any)=>(a.totalFloat??0)>0&&(a.totalFloat??0)<=5).length,  fill:C.orange},
    {range:'6–15',count:filtered.filter((a:any)=>(a.totalFloat??0)>5&&(a.totalFloat??0)<=15).length, fill:C.green},
    {range:'> 15',count:filtered.filter((a:any)=>(a.totalFloat??0)>15).length,  fill:C.accent},
  ],[filtered]);

  const phaseData=useMemo(()=>EPC_ORDER.map(k=>{
    const ph=EPC_PHASES[k];
    return{name:ph.label,key:k,count:filtered.filter((a:any)=>getEpcPhase(a.wbs||'',a.wbsPath||'').key===k).length,fill:ph.color};
  }).filter(d=>d.count>0),[filtered]);

  const wbsData=useMemo(()=>{
    const m:Record<string,number>={};
    filtered.forEach((a:any)=>{const w=a.wbs||'Unassigned';m[w]=(m[w]||0)+1;});
    return Object.entries(m).sort((a,b)=>b[1]-a[1]).slice(0,10).map(([name,count])=>({name,count}));
  },[filtered]);

  const pctData=useMemo(()=>[
    {range:'0%',    count:filtered.filter((a:any)=>(a.pctComplete||0)===0).length},
    {range:'1–25%', count:filtered.filter((a:any)=>(a.pctComplete||0)>0&&(a.pctComplete||0)<=25).length},
    {range:'26–50%',count:filtered.filter((a:any)=>(a.pctComplete||0)>25&&(a.pctComplete||0)<=50).length},
    {range:'51–75%',count:filtered.filter((a:any)=>(a.pctComplete||0)>50&&(a.pctComplete||0)<=75).length},
    {range:'76–99%',count:filtered.filter((a:any)=>(a.pctComplete||0)>75&&(a.pctComplete||0)<100).length},
    {range:'100%',  count:filtered.filter((a:any)=>(a.pctComplete||0)>=100).length},
  ],[filtered]);

  const monthlyData=useMemo(()=>{
    const m:Record<string,{month:string;_d:Date;notStarted:number;inProgress:number;complete:number}>={};
    filtered.forEach((a:any)=>{
      const d=parseDate(a.bFinish||a.finish);if(!d)return;
      const k=fmtShort(d);
      if(!m[k])m[k]={month:k,_d:d,notStarted:0,inProgress:0,complete:0};
      if((a.pctComplete||0)>=100)m[k].complete++;
      else if(a.start)m[k].inProgress++;
      else m[k].notStarted++;
    });
    return Object.values(m).sort((a,b)=>a._d.getTime()-b._d.getTime()).slice(-18)
      .map(({_d,...rest})=>rest);
  },[filtered]);

  const hasFilters=selStatus.size>0||selProject.size>0||selPhase.size>0||!!critSel||!!wbsText||!!dateFrom||!!dateTo||!!xFilt;
  function clearAll(){setSelStatus(new Set());setSelProject(new Set());setSelPhase(new Set());setCritSel(null);setWbsText('');setDateFrom('');setDateTo('');setXFilt(null);}
  function toggleXFilt(field:string,value:string){setXFilt(p=>p&&p.field===field&&p.value===value?null:{field,value});}
  const xActive=(field:string,val:string)=>xFilt?.field===field&&xFilt.value===val;

  function Pill({label,active,onClick,color}:{label:string;active:boolean;onClick:()=>void;color?:string}){
    return(
      <button onClick={onClick} style={{background:active?(color||C.accent)+'22':'transparent',border:`1px solid ${active?(color||C.accent):C.border}`,color:active?(color||C.accent):C.muted2,borderRadius:16,padding:'3px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit',fontWeight:active?700:400,transition:'all 0.12s',whiteSpace:'nowrap'}}>
        {label}
      </button>
    );
  }

  const card={background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'16px'};
  const ttStyle={contentStyle:{background:C.card2,border:`1px solid ${C.border}`,borderRadius:8,color:C.text,fontSize:12}};
  const xHint=(field:string,label:string)=>xFilt?.field===field?<div style={{textAlign:'center',fontSize:10,color:C.accent,marginTop:4}}>Filtering: {xFilt.value} {label} · click again to clear</div>:null;
  const pageData=filtered.slice(page*PAGE,(page+1)*PAGE);
  const totalPages=Math.ceil(total/PAGE);

  return(
    <div style={{padding:'18px 20px',maxWidth:1600,margin:'0 auto'}}>

      {/* Toolbar */}
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:14}}>
        <div style={{display:'flex',alignItems:'center',gap:10}}>
          <span style={{fontSize:20}}>📊</span>
          <span style={{fontSize:16,fontWeight:700,color:C.text}}>Cross-Filter Dashboard</span>
          <span style={{fontSize:11,color:C.muted,background:C.card,border:`1px solid ${C.border}`,borderRadius:6,padding:'2px 8px'}}>
            {total.toLocaleString()} of {acts.length.toLocaleString()} activities
          </span>
        </div>
        {hasFilters&&(
          <button onClick={clearAll} style={{background:`${C.red}18`,border:`1px solid ${C.red}`,color:C.red,borderRadius:8,padding:'5px 14px',cursor:'pointer',fontSize:12,fontFamily:'inherit',fontWeight:600}}>
            ✕ Clear All Filters
          </button>
        )}
      </div>

      {/* Slicers */}
      <div style={{...card,marginBottom:14}}>
        <div style={{fontSize:10,color:C.muted,textTransform:'uppercase',letterSpacing:'0.1em',marginBottom:10,fontWeight:600}}>Slicers</div>
        <div style={{display:'flex',flexWrap:'wrap',gap:18,alignItems:'flex-start'}}>
          <div>
            <div style={{fontSize:10,color:C.muted,marginBottom:5}}>Status</div>
            <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
              {(['Not Started','In Progress','Complete'] as const).map(s=>(
                <Pill key={s} label={s} active={selStatus.has(s)} color={s==='Complete'?C.green:s==='In Progress'?C.accent:C.muted2}
                  onClick={()=>setSelStatus(p=>{const n=new Set(p);n.has(s)?n.delete(s):n.add(s);return n;})}/>
              ))}
            </div>
          </div>
          <div>
            <div style={{fontSize:10,color:C.muted,marginBottom:5}}>Criticality</div>
            <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
              {([['critical','Critical',C.red],['near','Near-Critical',C.amber],['non','Non-Critical',C.green]] as [string,string,string][]).map(([k,l,col])=>(
                <Pill key={k} label={l} active={critSel===k} color={col} onClick={()=>setCritSel(p=>p===k?null:k)}/>
              ))}
            </div>
          </div>
          <div>
            <div style={{fontSize:10,color:C.muted,marginBottom:5}}>EPC Phase</div>
            <div style={{display:'flex',gap:4,flexWrap:'wrap'}}>
              {EPC_ORDER.filter(k=>k!=='?').map(k=>{const ph=EPC_PHASES[k];return(
                <Pill key={k} label={ph.short||ph.label} active={selPhase.has(k)} color={ph.color}
                  onClick={()=>setSelPhase(p=>{const n=new Set(p);n.has(k)?n.delete(k):n.add(k);return n;})}/>
              );})}
            </div>
          </div>
          {projects.length>1&&(
            <div>
              <div style={{fontSize:10,color:C.muted,marginBottom:5}}>Project</div>
              <div style={{display:'flex',gap:4,flexWrap:'wrap',maxWidth:480}}>
                {projects.slice(0,8).map((p:string)=>(
                  <Pill key={p} label={p} active={selProject.has(p)}
                    onClick={()=>setSelProject(prev=>{const n=new Set(prev);n.has(p)?n.delete(p):n.add(p);return n;})}/>
                ))}
              </div>
            </div>
          )}
          <div>
            <div style={{fontSize:10,color:C.muted,marginBottom:5}}>WBS Search</div>
            <input value={wbsText} onChange={e=>setWbsText(e.target.value)} placeholder="Filter by WBS…"
              style={{background:C.card2,border:`1px solid ${C.border}`,color:C.text,borderRadius:8,padding:'4px 10px',fontSize:11,fontFamily:'inherit',outline:'none',width:140}}/>
          </div>
          <div>
            <div style={{fontSize:10,color:C.muted,marginBottom:5}}>Finish Date Range</div>
            <div style={{display:'flex',gap:6,alignItems:'center'}}>
              <input type="date" value={dateFrom} onChange={e=>setDateFrom(e.target.value)}
                style={{background:C.card2,border:`1px solid ${C.border}`,color:C.text,borderRadius:8,padding:'4px 8px',fontSize:11,fontFamily:'inherit',outline:'none'}}/>
              <span style={{color:C.muted,fontSize:11}}>→</span>
              <input type="date" value={dateTo} onChange={e=>setDateTo(e.target.value)}
                style={{background:C.card2,border:`1px solid ${C.border}`,color:C.text,borderRadius:8,padding:'4px 8px',fontSize:11,fontFamily:'inherit',outline:'none'}}/>
            </div>
          </div>
        </div>
      </div>

      {/* KPI Cards */}
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(120px,1fr))',gap:10,marginBottom:14}}>
        {[
          {label:'Total',value:total.toLocaleString(),color:C.text},
          {label:'Sched %',value:`${schedPct.toFixed(1)}%`,color:C.green},
          {label:'Complete',value:complete.toLocaleString(),color:C.green},
          {label:'In Progress',value:inProg.toLocaleString(),color:C.accent},
          {label:'Not Started',value:notStarted.toLocaleString(),color:C.muted2},
          {label:'Critical',value:critical.toLocaleString(),color:C.red},
          {label:'Overdue',value:overdue.toLocaleString(),color:overdue>0?C.amber:C.green},
          {label:'BEI',value:bei.toFixed(2),color:bei>=0.95?C.green:bei>=0.8?C.amber:C.red},
          {label:'Avg Float',value:`${avgFloat.toFixed(1)}d`,color:avgFloat<0?C.red:avgFloat<5?C.amber:C.green},
        ].map(k=>(
          <div key={k.label} style={{...card,textAlign:'center',padding:'12px 8px'}}>
            <div style={{fontSize:20,fontWeight:800,color:k.color,fontFamily:"'DM Mono',monospace"}}>{k.value}</div>
            <div style={{fontSize:10,color:C.muted,textTransform:'uppercase',letterSpacing:'0.08em',marginTop:3}}>{k.label}</div>
          </div>
        ))}
      </div>

      {/* Chart Grid */}
      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(330px,1fr))',gap:14,marginBottom:14}}>

        {/* Status Pie */}
        <div style={card}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>Activity Status</div>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={statusData} cx="50%" cy="50%" outerRadius={78} dataKey="value"
                onClick={(e:any)=>toggleXFilt('status',e.name)} style={{cursor:'pointer'}}>
                {statusData.map((entry:any)=>(
                  <Cell key={entry.name} fill={entry.fill}
                    opacity={xFilt?.field==='status'&&!xActive('status',entry.name)?0.3:1}
                    stroke={xActive('status',entry.name)?C.text:'none'} strokeWidth={2}/>
                ))}
              </Pie>
              <Tooltip {...ttStyle}/>
              <Legend iconType="circle" iconSize={8} wrapperStyle={{fontSize:11,color:C.muted2}}/>
            </PieChart>
          </ResponsiveContainer>
          {xHint('status','')}
        </div>

        {/* Criticality Pie */}
        <div style={card}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>Criticality Distribution</div>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={critData} cx="50%" cy="50%" outerRadius={78} dataKey="value"
                onClick={(e:any)=>toggleXFilt('critType',e.name)} style={{cursor:'pointer'}}>
                {critData.map((entry:any)=>(
                  <Cell key={entry.name} fill={entry.fill}
                    opacity={xFilt?.field==='critType'&&!xActive('critType',entry.name)?0.3:1}
                    stroke={xActive('critType',entry.name)?C.text:'none'} strokeWidth={2}/>
                ))}
              </Pie>
              <Tooltip {...ttStyle}/>
              <Legend iconType="circle" iconSize={8} wrapperStyle={{fontSize:11,color:C.muted2}}/>
            </PieChart>
          </ResponsiveContainer>
          {xHint('critType','')}
        </div>

        {/* Float Distribution */}
        <div style={card}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>Float Distribution</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={floatData} margin={{top:0,right:10,left:0,bottom:0}}
              onClick={(e:any)=>{if(e?.activePayload?.[0])toggleXFilt('floatBucket',e.activePayload[0].payload.range);}}>
              <CartesianGrid strokeDasharray="3 3" stroke={`${C.border}60`}/>
              <XAxis dataKey="range" tick={{fill:C.muted2,fontSize:10}} tickLine={false}/>
              <YAxis tick={{fill:C.muted2,fontSize:10}} tickLine={false} axisLine={false}/>
              <Tooltip {...ttStyle}/>
              <Bar dataKey="count" radius={[4,4,0,0]} cursor="pointer">
                {floatData.map((entry:any)=>(
                  <Cell key={entry.range} fill={entry.fill}
                    opacity={xFilt?.field==='floatBucket'&&!xActive('floatBucket',entry.range)?0.3:1}/>
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          {xHint('floatBucket','float')}
        </div>

        {/* EPC Phase */}
        <div style={card}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>EPC Phase Breakdown</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={phaseData} layout="vertical" margin={{top:0,right:10,left:70,bottom:0}}
              onClick={(e:any)=>{if(e?.activePayload?.[0])toggleXFilt('phase',e.activePayload[0].payload.key);}}>
              <CartesianGrid strokeDasharray="3 3" stroke={`${C.border}60`} horizontal={false}/>
              <XAxis type="number" tick={{fill:C.muted2,fontSize:10}} tickLine={false}/>
              <YAxis dataKey="name" type="category" tick={{fill:C.muted2,fontSize:10}} tickLine={false} width={70}/>
              <Tooltip {...ttStyle}/>
              <Bar dataKey="count" radius={[0,4,4,0]} cursor="pointer">
                {phaseData.map((entry:any)=>(
                  <Cell key={entry.key} fill={entry.fill}
                    opacity={xFilt?.field==='phase'&&!xActive('phase',entry.key)?0.3:1}/>
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          {xHint('phase','phase')}
        </div>

        {/* WBS Top 10 */}
        <div style={card}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>Top 10 WBS by Count</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={wbsData} layout="vertical" margin={{top:0,right:10,left:84,bottom:0}}
              onClick={(e:any)=>{if(e?.activePayload?.[0])toggleXFilt('wbs',e.activePayload[0].payload.name);}}>
              <CartesianGrid strokeDasharray="3 3" stroke={`${C.border}60`} horizontal={false}/>
              <XAxis type="number" tick={{fill:C.muted2,fontSize:10}} tickLine={false}/>
              <YAxis dataKey="name" type="category" tick={{fill:C.muted2,fontSize:9}} tickLine={false} width={84}
                tickFormatter={(v:string)=>v.length>13?v.slice(0,13)+'…':v}/>
              <Tooltip {...ttStyle}/>
              <Bar dataKey="count" radius={[0,4,4,0]} cursor="pointer">
                {wbsData.map((entry:any)=>(
                  <Cell key={entry.name} fill={xActive('wbs',entry.name)?C.gold:C.accent}
                    opacity={xFilt?.field==='wbs'&&!xActive('wbs',entry.name)?0.3:1}/>
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          {xHint('wbs','WBS')}
        </div>

        {/* % Complete Histogram */}
        <div style={card}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>% Complete Distribution</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={pctData} margin={{top:0,right:10,left:0,bottom:0}}>
              <CartesianGrid strokeDasharray="3 3" stroke={`${C.border}60`}/>
              <XAxis dataKey="range" tick={{fill:C.muted2,fontSize:10}} tickLine={false}/>
              <YAxis tick={{fill:C.muted2,fontSize:10}} tickLine={false} axisLine={false}/>
              <Tooltip {...ttStyle}/>
              <Bar dataKey="count" fill={C.purple} radius={[4,4,0,0]}/>
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Monthly Trend */}
        <div style={{...card,gridColumn:'span 2'}}>
          <div style={{fontSize:12,fontWeight:700,color:C.text,marginBottom:10}}>Monthly Activity Completion Trend</div>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={monthlyData} margin={{top:0,right:20,left:0,bottom:0}}>
              <CartesianGrid strokeDasharray="3 3" stroke={`${C.border}60`}/>
              <XAxis dataKey="month" tick={{fill:C.muted2,fontSize:10}} tickLine={false}/>
              <YAxis tick={{fill:C.muted2,fontSize:10}} tickLine={false} axisLine={false}/>
              <Tooltip {...ttStyle}/>
              <Legend iconType="square" iconSize={8} wrapperStyle={{fontSize:11,color:C.muted2}}/>
              <Bar dataKey="notStarted" name="Not Started" stackId="a" fill={C.muted2}/>
              <Bar dataKey="inProgress" name="In Progress" stackId="a" fill={C.accent}/>
              <Bar dataKey="complete"   name="Complete"    stackId="a" fill={C.green} radius={[3,3,0,0]}/>
            </BarChart>
          </ResponsiveContainer>
        </div>

      </div>

      {/* Data Table */}
      <div style={card}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:12}}>
          <div style={{fontSize:12,fontWeight:700,color:C.text}}>Activity Detail — {total.toLocaleString()} records</div>
          <button onClick={()=>downloadCSV(filtered,
            [['id','ID'],['name','Activity Name'],['wbs','WBS'],['_status','Status'],['pctComplete','% Complete'],
             ['start','Start'],['finish','Finish'],['bStart','BL Start'],['bFinish','BL Finish'],['totalFloat','Float'],['dur','Duration'],['projectId','Project']],
            'powerbi-export',
            (r:any,k:string)=>{
              if(k==='_status')return getStatus(r);
              if(['start','finish','bStart','bFinish'].includes(k))return fmtDateExport(r[k]);
              return String(r[k]??'');
            }
          )} style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:7,padding:'5px 12px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
            ⬇ Export CSV
          </button>
        </div>
        <div style={{overflowX:'auto'}}>
          <table style={{width:'100%',borderCollapse:'collapse',fontSize:11,minWidth:860}}>
            <thead>
              <tr style={{background:C.card2}}>
                {[['ID','80px'],['Activity Name','260px'],['WBS','120px'],['Status','90px'],['% Done','70px'],['Start','90px'],['Finish','90px'],['Float','60px'],['Dur','55px'],['Crit','45px']].map(([h,w])=>(
                  <th key={h} style={{padding:'7px 8px',textAlign:'left',color:C.muted,fontSize:9,textTransform:'uppercase',letterSpacing:'0.07em',borderBottom:`1px solid ${C.border}`,width:w,whiteSpace:'nowrap'}}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {pageData.map((a:any,i:number)=>{
                const st=getStatus(a);
                const fl=a.totalFloat;
                const finish=parseDate(a.finish||a.bFinish);
                const isOvd=!!(finish&&finish<today&&(a.pctComplete||0)<100&&!a.isMilestone);
                return(
                  <tr key={i} style={{borderBottom:`1px solid ${C.border}30`,background:i%2===0?'transparent':`${C.card2}80`}}>
                    <td style={{padding:'5px 8px',color:C.muted2,fontSize:10,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis',maxWidth:80}}>{a.id||a.code||'—'}</td>
                    <td style={{padding:'5px 8px',color:C.text,maxWidth:260,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={a.name}>{a.name||'—'}</td>
                    <td style={{padding:'5px 8px',color:C.muted2,fontSize:10,maxWidth:120,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={a.wbs}>{a.wbs||'—'}</td>
                    <td style={{padding:'5px 8px'}}>
                      <span style={{fontSize:9,padding:'2px 6px',borderRadius:8,fontWeight:700,background:st==='Complete'?`${C.green}20`:st==='In Progress'?`${C.accent}20`:`${C.muted2}20`,color:st==='Complete'?C.green:st==='In Progress'?C.accent:C.muted2}}>
                        {st}
                      </span>
                    </td>
                    <td style={{padding:'5px 8px',fontFamily:"'DM Mono',monospace",fontSize:11,color:(a.pctComplete||0)>=100?C.green:(a.pctComplete||0)>50?C.accent:C.muted2}}>
                      {a.pctComplete||0}%
                    </td>
                    <td style={{padding:'5px 8px',color:C.muted2,fontSize:10,whiteSpace:'nowrap'}}>{fmtDate(parseDate(a.start))}</td>
                    <td style={{padding:'5px 8px',color:isOvd?C.red:C.muted2,fontSize:10,whiteSpace:'nowrap'}}>{fmtDate(finish)}{isOvd&&' ⚠'}</td>
                    <td style={{padding:'5px 8px',fontFamily:"'DM Mono',monospace",fontSize:11,color:fl==null?C.muted2:fl<0?C.red:fl<=5?C.amber:C.green}}>
                      {fl==null?'—':`${fl}d`}
                    </td>
                    <td style={{padding:'5px 8px',color:C.muted2,fontSize:10}}>{a.dur!=null?`${a.dur}d`:'—'}</td>
                    <td style={{padding:'5px 8px',textAlign:'center'}}>
                      {a.isCritical&&<span style={{fontSize:9,padding:'2px 4px',borderRadius:4,background:`${C.red}20`,color:C.red,fontWeight:700}}>CP</span>}
                    </td>
                  </tr>
                );
              })}
              {pageData.length===0&&(
                <tr><td colSpan={10} style={{textAlign:'center',padding:'32px',color:C.muted}}>No activities match the current filters.</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {total>PAGE&&(
          <div style={{display:'flex',alignItems:'center',justifyContent:'center',gap:8,marginTop:12}}>
            <button onClick={()=>setPage(p=>Math.max(0,p-1))} disabled={page===0}
              style={{background:page===0?'transparent':`${C.accent}14`,border:`1px solid ${page===0?C.border:C.accent}`,color:page===0?C.muted:C.accent,borderRadius:6,padding:'4px 12px',cursor:page===0?'default':'pointer',fontSize:11,fontFamily:'inherit'}}>
              ← Prev
            </button>
            <span style={{color:'#111111',fontSize:11}}>Page {page+1} of {totalPages}</span>
            <button onClick={()=>setPage(p=>Math.min(totalPages-1,p+1))} disabled={page>=totalPages-1}
              style={{background:page>=totalPages-1?'transparent':`${C.accent}14`,border:`1px solid ${page>=totalPages-1?C.border:C.accent}`,color:page>=totalPages-1?C.muted:C.accent,borderRadius:6,padding:'4px 12px',cursor:page>=totalPages-1?'default':'pointer',fontSize:11,fontFamily:'inherit'}}>
              Next →
            </button>
          </div>
        )}
      </div>

    </div>
  );
}

// ─── RESOURCE VIEW ────────────────────────────────────────────────────────────
function ResourceView({allActivities}:any){
  const acts:any[] = allActivities||[];

  // Aggregate resource assignments across all activities
  const rsrcAgg: Record<string,{
    name:string; type:string;
    budgetedUnits:number; actualUnits:number; remainingUnits:number;
    budgetedCost:number; actualCost:number; remainingCost:number;
    actCount:number;
  }> = {};

  let totalBgtUnits=0, totalActUnits=0, totalRemUnits=0;
  let totalBgtCost=0, totalActCost=0, totalRemCost=0;
  let totalRsrcActs=0;

  const typeCount: Record<string,number> = {};

  for(const a of acts){
    const assignments:any[] = a.resourceAssignments||[];
    if(assignments.length>0) totalRsrcActs++;
    for(const ra of assignments){
      const key = ra.rsrcId||ra.rsrcName||'Unknown';
      if(!rsrcAgg[key]){
        rsrcAgg[key]={name:ra.rsrcName||key, type:ra.rsrcType||'RT_Labor',
          budgetedUnits:0,actualUnits:0,remainingUnits:0,
          budgetedCost:0,actualCost:0,remainingCost:0,actCount:0};
      }
      const e=rsrcAgg[key];
      e.budgetedUnits+=(ra.budgetedUnits||0);
      e.actualUnits+=(ra.actualUnits||0);
      e.remainingUnits+=(ra.remainingUnits||0);
      e.budgetedCost+=(ra.budgetedCost||0);
      e.actualCost+=(ra.actualCost||0);
      e.remainingCost+=(ra.remainingCost||0);
      e.actCount++;
      totalBgtUnits+=(ra.budgetedUnits||0);
      totalActUnits+=(ra.actualUnits||0);
      totalRemUnits+=(ra.remainingUnits||0);
      totalBgtCost+=(ra.budgetedCost||0);
      totalActCost+=(ra.actualCost||0);
      totalRemCost+=(ra.remainingCost||0);
      const rt=ra.rsrcType||'RT_Labor';
      typeCount[rt]=(typeCount[rt]||0)+1;
    }
  }

  // Fallback: use rolled-up hours if no assignments
  const hasFull = Object.keys(rsrcAgg).length>0;
  if(!hasFull){
    for(const a of acts){
      if(!a.primaryResource) continue;
      const key=a.primaryResource;
      if(!rsrcAgg[key]) rsrcAgg[key]={name:key,type:a.primaryResourceType||'RT_Labor',
        budgetedUnits:0,actualUnits:0,remainingUnits:0,
        budgetedCost:0,actualCost:0,remainingCost:0,actCount:0};
      const e=rsrcAgg[key];
      e.budgetedUnits+=(a.budgetedHours||0);
      e.actualUnits+=(a.actualHours||0);
      e.remainingUnits+=(a.remainingHours||0);
      e.budgetedCost+=(a.budgetedCost||0);
      e.actualCost+=(a.actualCost||0);
      e.remainingCost+=(a.remainingCost||0);
      e.actCount++;
      totalBgtUnits+=(a.budgetedHours||0);
      totalActUnits+=(a.actualHours||0);
      totalRemUnits+=(a.remainingHours||0);
      totalBgtCost+=(a.budgetedCost||0);
      totalActCost+=(a.actualCost||0);
      totalRemCost+=(a.remainingCost||0);
      const rt=a.primaryResourceType||'RT_Labor';
      typeCount[rt]=(typeCount[rt]||0)+1;
    }
  }

  const totalRsrcs=Object.keys(rsrcAgg).length;
  const unitsDonePct=totalBgtUnits>0?Math.round(totalActUnits/totalBgtUnits*100):0;

  // Sort resources for chart (top 12 by budgeted units)
  const sortedRsrcs=Object.values(rsrcAgg).sort((a,b)=>b.budgetedUnits-a.budgetedUnits);
  const top12=sortedRsrcs.slice(0,12);

  // Type breakdown for pie
  const TYPE_LABELS:Record<string,string>={RT_Labor:'Labor',RT_Equip:'Equipment',RT_Mat:'Material',RT_Crew:'Crew'};
  const TYPE_COLORS:Record<string,string>={RT_Labor:C.accent,RT_Equip:C.gold,RT_Mat:C.green,RT_Crew:C.purple};
  const typePie=Object.entries(typeCount).map(([t,v])=>({name:TYPE_LABELS[t]||t,value:v,fill:TYPE_COLORS[t]||C.muted}));

  // Table state
  const [search,setSearch]=useState('');
  const [sortCol,setSortCol]=useState<'budgetedUnits'|'actualUnits'|'remainingUnits'|'budgetedCost'|'actualCost'|'remainingCost'|'name'>('budgetedUnits');
  const [sortAsc,setSortAsc]=useState(false);
  const [page,setPage]=useState(0);
  const [expanded,setExpanded]=useState<Set<string>>(new Set());
  const PAGE=999999;

  const filtered=useMemo(()=>{
    const q=search.toLowerCase();
    return sortedRsrcs
      .filter(r=>!q||r.name.toLowerCase().includes(q)||r.type.toLowerCase().includes(q))
      .sort((a,b)=>{
        const av=a[sortCol as keyof typeof a] as number|string;
        const bv=b[sortCol as keyof typeof b] as number|string;
        if(typeof av==='string') return sortAsc?av.localeCompare(bv as string):(bv as string).localeCompare(av);
        return sortAsc?(av as number)-(bv as number):(bv as number)-(av as number);
      });
  },[sortedRsrcs,search,sortCol,sortAsc]);

  useEffect(()=>setPage(0),[filtered]);

  const pageData=filtered.slice(page*PAGE,(page+1)*PAGE);
  const totalPages=Math.max(1,Math.ceil(filtered.length/PAGE));

  function toggleSort(col:typeof sortCol){
    if(sortCol===col) setSortAsc(p=>!p);
    else{setSortCol(col);setSortAsc(false);}
  }
  function toggleExpand(key:string){
    setExpanded(p=>{const n=new Set(p);n.has(key)?n.delete(key):n.add(key);return n;});
  }

  function exportCSV(){
    const rows=[['Resource','Type','Budgeted Units','Actual Units','Remaining Units','Budgeted Cost','Actual Cost','Remaining Cost','Activities']];
    for(const r of filtered){
      rows.push([r.name,TYPE_LABELS[r.type]||r.type,
        r.budgetedUnits.toFixed(1),r.actualUnits.toFixed(1),r.remainingUnits.toFixed(1),
        r.budgetedCost.toFixed(2),r.actualCost.toFixed(2),r.remainingCost.toFixed(2),
        String(r.actCount)]);
    }
    const csv=rows.map(r=>r.map(c=>`"${String(c).replace(/"/g,'""')}"`).join(',')).join('\n');
    const a=document.createElement('a');
    a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
    a.download='resources.csv'; a.click();
  }

  function fmt(n:number){return n>=1e6?(n/1e6).toFixed(1)+'M':n>=1e3?(n/1e3).toFixed(1)+'k':n.toFixed(1);}
  function fmtCost(n:number){return '$'+fmt(n);}
  function Th({col,label}:{col:typeof sortCol,label:string}){
    return <th onClick={()=>toggleSort(col)} style={{cursor:'pointer',padding:'8px 10px',textAlign:'right',
      color:sortCol===col?C.accent:C.muted,fontWeight:600,whiteSpace:'nowrap',userSelect:'none'}}>
      {label}{sortCol===col?(sortAsc?' ▲':' ▼'):''}
    </th>;
  }

  // KPI card
  function KPI({label,val,sub,color}:{label:string;val:string;sub?:string;color?:string}){
    return <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:'14px 18px',minWidth:140}}>
      <div style={{fontSize:11,color:C.muted,marginBottom:4}}>{label}</div>
      <div style={{fontSize:22,fontWeight:700,color:color||C.accent}}>{val}</div>
      {sub&&<div style={{fontSize:11,color:C.muted2,marginTop:2}}>{sub}</div>}
    </div>;
  }

  if(totalRsrcs===0){
    return <div style={{padding:40,textAlign:'center',color:C.muted}}>
      <div style={{fontSize:36,marginBottom:12}}>👷</div>
      <div style={{fontSize:16,marginBottom:6}}>No resource data found</div>
      <div style={{fontSize:13,color:C.muted2}}>Upload a P6 XER file with resource assignments to see resource analysis.</div>
    </div>;
  }

  return <div style={{padding:'20px 24px',color:C.text,fontFamily:'inherit'}}>
    <div style={{fontSize:20,fontWeight:700,color:C.accent,marginBottom:16}}>Resource Analysis</div>

    {/* KPI Cards */}
    <div style={{display:'flex',gap:12,flexWrap:'wrap',marginBottom:20}}>
      <KPI label="Total Resources" val={String(totalRsrcs)}/>
      <KPI label="Budgeted Units" val={fmt(totalBgtUnits)} sub="hours"/>
      <KPI label="Actual Units" val={fmt(totalActUnits)} sub="hours"/>
      <KPI label="Remaining Units" val={fmt(totalRemUnits)} sub="hours"/>
      <KPI label="Units % Done" val={unitsDonePct+'%'} color={unitsDonePct>=90?C.green:unitsDonePct>=50?C.gold:C.red}/>
      <KPI label="Budgeted Cost" val={fmtCost(totalBgtCost)}/>
      <KPI label="Actual Cost" val={fmtCost(totalActCost)}/>
      <KPI label="Remaining Cost" val={fmtCost(totalRemCost)} color={C.amber}/>
    </div>

    {/* Charts row */}
    <div style={{display:'grid',gridTemplateColumns:'280px 1fr',gap:16,marginBottom:20}}>
      {/* Resource type pie */}
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:16}}>
        <div style={{fontWeight:600,fontSize:13,color:C.muted,marginBottom:10}}>Resource Types</div>
        {typePie.length>0
          ? <PieChart width={248} height={200}>
              <Pie data={typePie} dataKey="value" cx="50%" cy="50%" outerRadius={80} label={({name,percent})=>`${name} ${(percent*100).toFixed(0)}%`} labelLine={false}>
                {typePie.map((e,i)=><Cell key={i} fill={e.fill}/>)}
              </Pie>
              <Tooltip formatter={(v:any)=>[v,'Assignments']}/>
            </PieChart>
          : <div style={{color:C.muted,fontSize:13,paddingTop:40,textAlign:'center'}}>No type data</div>
        }
      </div>

      {/* Top-12 bar chart */}
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:16}}>
        <div style={{fontWeight:600,fontSize:13,color:C.muted,marginBottom:10}}>Top Resources — Units (Budgeted vs Actual vs Remaining)</div>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={top12.map(r=>({name:r.name.length>18?r.name.slice(0,17)+'…':r.name,
            Budgeted:+r.budgetedUnits.toFixed(1),Actual:+r.actualUnits.toFixed(1),Remaining:+r.remainingUnits.toFixed(1)}))}
            layout="vertical" margin={{left:8,right:20,top:0,bottom:0}}>
            <XAxis type="number" tick={{fill:C.muted,fontSize:11}}/>
            <YAxis dataKey="name" type="category" width={130} tick={{fill:C.text,fontSize:11}}/>
            <Tooltip contentStyle={{background:C.panel,border:`1px solid ${C.border}`,color:C.text}}/>
            <Legend wrapperStyle={{color:C.muted,fontSize:11}}/>
            <Bar dataKey="Budgeted" fill={C.accent} radius={[0,3,3,0]}/>
            <Bar dataKey="Actual"   fill={C.green}  radius={[0,3,3,0]}/>
            <Bar dataKey="Remaining" fill={C.gold}  radius={[0,3,3,0]}/>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>

    {/* Resource table */}
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,overflow:'hidden'}}>
      <div style={{display:'flex',alignItems:'center',gap:10,padding:'12px 16px',borderBottom:`1px solid ${C.border}`}}>
        <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search resource…"
          style={{flex:1,background:C.card2,border:`1px solid ${C.border}`,borderRadius:6,padding:'6px 10px',
            color:C.text,outline:'none',fontSize:13}}/>
        <button onClick={exportCSV} style={{background:C.accent,color:'#000',border:'none',borderRadius:6,
          padding:'6px 14px',cursor:'pointer',fontWeight:700,fontSize:12}}>Export CSV</button>
      </div>
      <div style={{overflowX:'auto'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
          <thead>
            <tr style={{background:C.card2,color:C.muted,fontSize:11}}>
              <th style={{padding:'8px 10px',textAlign:'left',color:C.muted,fontWeight:600}}></th>
              <th onClick={()=>toggleSort('name')} style={{cursor:'pointer',padding:'8px 10px',textAlign:'left',
                color:sortCol==='name'?C.accent:C.muted,fontWeight:600}}>
                Resource{sortCol==='name'?(sortAsc?' ▲':' ▼'):''}
              </th>
              <th style={{padding:'8px 10px',textAlign:'left',color:C.muted,fontWeight:600}}>Type</th>
              <Th col="budgetedUnits" label="Budgeted Units"/>
              <Th col="actualUnits" label="Actual Units"/>
              <Th col="remainingUnits" label="Remaining Units"/>
              <Th col="budgetedCost" label="Budgeted Cost"/>
              <Th col="actualCost" label="Actual Cost"/>
              <Th col="remainingCost" label="Remaining Cost"/>
              <th style={{padding:'8px 10px',textAlign:'right',color:C.muted,fontWeight:600}}>Activities</th>
            </tr>
          </thead>
          <tbody>
            {pageData.map((r,i)=>{
              const key=r.name;
              const isExp=expanded.has(key);
              // Find assignments for this resource across all activities
              const relActs = hasFull
                ? acts.filter(a=>(a.resourceAssignments||[]).some((ra:any)=>(ra.rsrcName||ra.rsrcId)===r.name||(ra.rsrcId===key)))
                : acts.filter(a=>a.primaryResource===key);
              return <Fragment key={key}>
                <tr style={{borderTop:`1px solid ${C.border}`,background:i%2===0?'transparent':C.card2,cursor:'pointer'}}
                  onClick={()=>toggleExpand(key)}>
                  <td style={{padding:'7px 6px 7px 10px',color:C.muted,textAlign:'center'}}>{isExp?'▾':'▸'}</td>
                  <td style={{padding:'7px 10px',color:C.text,fontWeight:500,whiteSpace:'nowrap'}}>{r.name}</td>
                  <td style={{padding:'7px 10px',color:C.muted}}>
                    <span style={{background:C.card2,borderRadius:4,padding:'2px 6px',fontSize:10}}>
                      {TYPE_LABELS[r.type]||r.type}
                    </span>
                  </td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.gold}}>{r.budgetedUnits.toFixed(1)}</td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.green}}>{r.actualUnits.toFixed(1)}</td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.amber}}>{r.remainingUnits.toFixed(1)}</td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.muted2}}>{fmtCost(r.budgetedCost)}</td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.muted2}}>{fmtCost(r.actualCost)}</td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.muted2}}>{fmtCost(r.remainingCost)}</td>
                  <td style={{padding:'7px 10px',textAlign:'right',color:C.muted}}>{r.actCount}</td>
                </tr>
                {isExp&&relActs.slice(0,20).map((a:any,ai:number)=>{
                  const asgn = hasFull
                    ? (a.resourceAssignments||[]).find((ra:any)=>(ra.rsrcName||ra.rsrcId)===r.name||(ra.rsrcId===key))
                    : null;
                  return <tr key={a.id||ai} style={{background:C.panel,fontSize:11}}>
                    <td style={{padding:'5px 6px'}}></td>
                    <td colSpan={2} style={{padding:'5px 10px 5px 20px',color:C.muted2}}>
                      {a.code} — {a.name?.slice(0,50)}
                    </td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.gold}}>{asgn?asgn.budgetedUnits.toFixed(1):(a.budgetedHours||0).toFixed(1)}</td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.green}}>{asgn?asgn.actualUnits.toFixed(1):(a.actualHours||0).toFixed(1)}</td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.amber}}>{asgn?asgn.remainingUnits.toFixed(1):(a.remainingHours||0).toFixed(1)}</td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.muted2}}>{asgn?fmtCost(asgn.budgetedCost):fmtCost(a.budgetedCost||0)}</td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.muted2}}>{asgn?fmtCost(asgn.actualCost):fmtCost(a.actualCost||0)}</td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.muted2}}>{asgn?fmtCost(asgn.remainingCost):fmtCost(a.remainingCost||0)}</td>
                    <td style={{padding:'5px 10px',textAlign:'right',color:C.muted}}>
                      {asgn&&asgn.isPrimary?<span style={{color:C.accent,fontSize:9}}>PRIMARY</span>:''}
                    </td>
                  </tr>;
                })}
                {isExp&&relActs.length>20&&<tr style={{background:C.panel}}>
                  <td colSpan={10} style={{padding:'4px 20px',color:C.muted,fontSize:11}}>…and {relActs.length-20} more activities</td>
                </tr>}
              </Fragment>;
            })}
          </tbody>
        </table>
      </div>
      {/* Pagination */}
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'10px 16px',borderTop:`1px solid ${C.border}`}}>
        <span style={{color:C.muted,fontSize:12}}>{filtered.length} resources</span>
        <div style={{display:'flex',gap:8,alignItems:'center'}}>
          <button onClick={()=>setPage(p=>Math.max(0,p-1))} disabled={page===0}
            style={{background:page===0?C.card2:C.accent,color:page===0?C.muted:'#000',border:'none',
              borderRadius:5,padding:'4px 12px',cursor:page===0?'default':'pointer',fontSize:12}}>‹</button>
          <span style={{color:C.muted,fontSize:12}}>Page {page+1}/{totalPages}</span>
          <button onClick={()=>setPage(p=>Math.min(totalPages-1,p+1))} disabled={page>=totalPages-1}
            style={{background:page>=totalPages-1?C.card2:C.accent,color:page>=totalPages-1?C.muted:'#000',
              border:'none',borderRadius:5,padding:'4px 12px',cursor:page>=totalPages-1?'default':'pointer',fontSize:12}}>›</button>
        </div>
      </div>
    </div>
  </div>;
}

// ─── OUT OF SEQUENCE VIEW ─────────────────────────────────────────────────────
function OutOfSequenceView({allActivities}:any){
  const acts:any[] = allActivities||[];

  // Build lookup map
  const actMap = useMemo(()=>{
    const m:Record<string,any>={};
    for(const a of acts) m[a.id||a.code]=a;
    return m;
  },[acts]);

  // OOS detection: activity has started/progressed but predecessor not yet done
  const oosItems = useMemo(()=>{
    const results:any[]=[];
    for(const act of acts){
      const pct = act.pctComplete||0;
      const hasStarted = !!act.start || pct>0;
      if(!hasStarted) continue;          // not started — no OOS possible
      if(pct>=100) continue;             // fully complete — skip
      if(act.isMilestone) continue;      // milestones excluded

      const offenders:any[]=[];
      for(const pred of (act.predecessors||[])){
        const predAct = actMap[pred.actId];
        if(!predAct) continue;
        const predPct = predAct.pctComplete||0;
        const rel = pred.relType||'FS';

        let violated=false;
        let reason='';
        if(rel==='FS'){
          // Predecessor must be 100% before successor starts
          if(predPct<100){violated=true;reason=`Predecessor ${predPct.toFixed(0)}% complete (must finish first)`;}
        } else if(rel==='SS'){
          // Predecessor must have started before successor starts
          if(!predAct.start && predPct===0){violated=true;reason='Predecessor has not started (SS relationship)';}
        } else if(rel==='FF'){
          // Both must finish — if act is in progress but pred not done
          if(predPct<100){violated=true;reason=`Predecessor ${predPct.toFixed(0)}% complete (FF — must also finish)`;}
        }
        // SF rarely causes OOS in practice — skip

        if(violated){
          offenders.push({
            actId:pred.actId,
            code:predAct.code||pred.actId,
            name:predAct.name||pred.actId,
            pctComplete:predPct,
            status:predAct.status||'',
            relType:rel,
            lagDays:pred.lagDays||0,
            reason,
          });
        }
      }

      if(offenders.length>0){
        // Severity: how far ahead is this activity vs incomplete predecessor
        const maxPredPct = Math.max(...offenders.map((o:any)=>o.pctComplete));
        const gap = pct - maxPredPct;
        const severity = gap>50 ? 'HIGH' : gap>20 ? 'MEDIUM' : 'LOW';
        results.push({...act, oosOffenders:offenders, severity, progressGap:gap});
      }
    }
    // Sort: HIGH first, then by progressGap desc
    return results.sort((a,b)=>{
      const sOrd:Record<string,number>={HIGH:0,MEDIUM:1,LOW:2};
      if(sOrd[a.severity]!==sOrd[b.severity]) return sOrd[a.severity]-sOrd[b.severity];
      return b.progressGap-a.progressGap;
    });
  },[acts,actMap]);

  const highCnt  = oosItems.filter(a=>a.severity==='HIGH').length;
  const medCnt   = oosItems.filter(a=>a.severity==='MEDIUM').length;
  const lowCnt   = oosItems.filter(a=>a.severity==='LOW').length;
  const totalActive = acts.filter(a=>a.start&&(a.pctComplete||0)<100&&!a.isMilestone).length;
  const oosPct = totalActive>0?((oosItems.length/totalActive)*100).toFixed(1):'0.0';

  // WBS breakdown
  const wbsCount:Record<string,number>={};
  for(const a of oosItems){
    const w=(a.wbs||a.wbsPath||'Unknown').split('/')[0].trim()||'Unknown';
    wbsCount[w]=(wbsCount[w]||0)+1;
  }
  const wbsData=Object.entries(wbsCount).sort((a,b)=>b[1]-a[1]).slice(0,10)
    .map(([name,count])=>({name:name.length>22?name.slice(0,21)+'…':name,count}));

  // Table state
  const [search,setSearch]=useState('');
  const [sevFilt,setSevFilt]=useState<'ALL'|'HIGH'|'MEDIUM'|'LOW'>('ALL');
  const [expanded,setExpanded]=useState<Set<string>>(new Set());
  const [page,setPage]=useState(0);
  const PAGE=999999;

  const filtered=useMemo(()=>{
    const q=search.toLowerCase();
    return oosItems.filter(a=>{
      if(sevFilt!=='ALL'&&a.severity!==sevFilt) return false;
      if(q&&!(a.name||'').toLowerCase().includes(q)&&!(a.code||'').toLowerCase().includes(q)) return false;
      return true;
    });
  },[oosItems,search,sevFilt]);

  useEffect(()=>setPage(0),[filtered]);

  const pageData=filtered.slice(page*PAGE,(page+1)*PAGE);
  const totalPages=Math.max(1,Math.ceil(filtered.length/PAGE));

  function toggleExpand(id:string){
    setExpanded(p=>{const n=new Set(p);n.has(id)?n.delete(id):n.add(id);return n;});
  }

  function exportCSV(){
    const rows=[['Activity ID','Activity Name','WBS','% Complete','Severity','Progress Gap','OOS Predecessors','Relationship']];
    for(const a of filtered){
      for(const o of a.oosOffenders){
        rows.push([a.code,a.name,a.wbs||'',
          (a.pctComplete||0).toFixed(1)+'%',
          a.severity, a.progressGap.toFixed(1)+'%',
          `${o.code} — ${o.name} (${o.pctComplete.toFixed(0)}%)`,
          o.relType]);
      }
    }
    const csv=rows.map((r:string[])=>r.map(c=>`"${String(c).replace(/"/g,'""')}"`).join(',')).join('\n');
    const el=document.createElement('a');
    el.href='data:text/csv;charset=utf-8,'+encodeURIComponent(csv);
    el.download='out_of_sequence.csv'; el.click();
  }

  const SEV_COLOR:Record<string,string>={HIGH:C.red,MEDIUM:C.amber,LOW:C.gold};
  const SEV_BG:Record<string,string>={HIGH:'rgba(255,87,87,0.12)',MEDIUM:'rgba(255,181,71,0.12)',LOW:'rgba(255,217,102,0.10)'};

  function KPI({label,val,sub,color}:{label:string;val:string|number;sub?:string;color?:string}){
    return <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:'14px 18px',minWidth:130}}>
      <div style={{fontSize:11,color:C.muted,marginBottom:4}}>{label}</div>
      <div style={{fontSize:24,fontWeight:700,color:color||C.accent}}>{val}</div>
      {sub&&<div style={{fontSize:11,color:C.muted2,marginTop:2}}>{sub}</div>}
    </div>;
  }

  if(acts.length===0){
    return <div style={{padding:40,textAlign:'center',color:C.muted}}>
      <div style={{fontSize:36,marginBottom:12}}>⚠️</div>
      <div style={{fontSize:16}}>No activities loaded</div>
    </div>;
  }

  return <div style={{padding:'20px 24px',color:C.text,fontFamily:'inherit'}}>
    <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:18}}>
      <div>
        <div style={{fontSize:20,fontWeight:700,color:C.accent}}>Out of Sequence Analysis</div>
        <div style={{fontSize:12,color:C.muted,marginTop:2}}>
          Activities with actual progress but incomplete predecessors — violating planned schedule logic
        </div>
      </div>
      <button type="button" onClick={exportCSV}
        style={{marginLeft:'auto',background:C.accent,color:'#000',border:'none',
          borderRadius:6,padding:'7px 16px',cursor:'pointer',fontWeight:700,fontSize:12}}>
        Export CSV
      </button>
    </div>

    {/* KPI cards */}
    <div style={{display:'flex',gap:12,flexWrap:'wrap',marginBottom:20}}>
      <KPI label="Total OOS Activities" val={oosItems.length} color={oosItems.length>0?C.red:C.green}/>
      <KPI label="OOS % of Active" val={oosPct+'%'} sub={`${totalActive} active activities`}
        color={parseFloat(oosPct)>20?C.red:parseFloat(oosPct)>10?C.amber:C.green}/>
      <KPI label="High Severity" val={highCnt} sub="Gap > 50%" color={highCnt>0?C.red:C.green}/>
      <KPI label="Medium Severity" val={medCnt} sub="Gap 20–50%" color={medCnt>0?C.amber:C.green}/>
      <KPI label="Low Severity" val={lowCnt} sub="Gap < 20%" color={lowCnt>0?C.gold:C.green}/>
    </div>

    {oosItems.length===0?(
      <div style={{textAlign:'center',padding:'60px 20px',background:C.card,borderRadius:12,border:`1px solid ${C.border}`}}>
        <div style={{fontSize:40,marginBottom:12}}>✅</div>
        <div style={{fontSize:18,fontWeight:700,color:C.green,marginBottom:6}}>No Out-of-Sequence Activities</div>
        <div style={{fontSize:13,color:C.muted}}>All in-progress activities respect their predecessor logic.</div>
      </div>
    ):<>
      {/* Charts row */}
      <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16,marginBottom:20}}>
        {/* Severity breakdown */}
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:16}}>
          <div style={{fontWeight:600,fontSize:13,color:C.muted,marginBottom:12}}>Severity Breakdown</div>
          <PieChart width={260} height={180}>
            <Pie data={[
              {name:'High',value:highCnt,fill:C.red},
              {name:'Medium',value:medCnt,fill:C.amber},
              {name:'Low',value:lowCnt,fill:C.gold},
            ].filter(d=>d.value>0)} dataKey="value" cx="50%" cy="50%" outerRadius={70}
              label={({name,value})=>`${name}: ${value}`} labelLine={false}>
              {[C.red,C.amber,C.gold].map((c,i)=><Cell key={i} fill={c}/>)}
            </Pie>
            <Tooltip contentStyle={{background:C.panel,border:`1px solid ${C.border}`,color:C.text}}/>
          </PieChart>
        </div>
        {/* WBS distribution */}
        <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:16}}>
          <div style={{fontWeight:600,fontSize:13,color:C.muted,marginBottom:8}}>OOS by WBS (Top 10)</div>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={wbsData} layout="vertical" margin={{left:4,right:20,top:0,bottom:0}}>
              <XAxis type="number" tick={{fill:C.muted,fontSize:11}}/>
              <YAxis dataKey="name" type="category" width={140} tick={{fill:C.text,fontSize:11}}/>
              <Tooltip contentStyle={{background:C.panel,border:`1px solid ${C.border}`,color:C.text}}
                formatter={(v:any)=>[v,'OOS Activities']}/>
              <Bar dataKey="count" fill={C.red} radius={[0,3,3,0]}/>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Filter bar */}
      <div style={{display:'flex',gap:10,alignItems:'center',marginBottom:14,flexWrap:'wrap'}}>
        <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search activity ID or name…"
          style={{flex:1,minWidth:200,background:C.card,border:`1px solid ${C.border}`,borderRadius:6,
            padding:'7px 12px',color:C.text,outline:'none',fontSize:13}}/>
        {(['ALL','HIGH','MEDIUM','LOW'] as const).map(s=>(
          <button key={s} type="button" onClick={()=>setSevFilt(s)}
            style={{background:sevFilt===s?(SEV_COLOR[s]||C.accent):'transparent',
              border:`1px solid ${SEV_COLOR[s]||C.border}`,
              color:sevFilt===s?'#000':SEV_COLOR[s]||C.muted,
              borderRadius:6,padding:'6px 14px',cursor:'pointer',fontWeight:600,fontSize:12}}>
            {s}{s!=='ALL'&&` (${oosItems.filter(a=>a.severity===s).length})`}
          </button>
        ))}
      </div>

      {/* Table */}
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,overflow:'hidden'}}>
        <div style={{overflowX:'auto'}}>
          <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
            <thead>
              <tr style={{background:C.card2,color:C.muted,fontSize:11}}>
                <th style={{padding:'8px 8px',width:24}}></th>
                <th style={{padding:'8px 10px',textAlign:'left'}}>Activity ID</th>
                <th style={{padding:'8px 10px',textAlign:'left'}}>Activity Name</th>
                <th style={{padding:'8px 10px',textAlign:'left'}}>WBS</th>
                <th style={{padding:'8px 10px',textAlign:'right'}}>% Complete</th>
                <th style={{padding:'8px 10px',textAlign:'right'}}>Progress Gap</th>
                <th style={{padding:'8px 10px',textAlign:'center'}}>Severity</th>
                <th style={{padding:'8px 10px',textAlign:'right'}}>OOS Preds</th>
              </tr>
            </thead>
            <tbody>
              {pageData.map((a:any,i:number)=>{
                const isExp=expanded.has(a.id||a.code);
                return <Fragment key={a.id||a.code}>
                  <tr style={{borderTop:`1px solid ${C.border}`,
                    background:i%2===0?'transparent':C.card2,cursor:'pointer'}}
                    onClick={()=>toggleExpand(a.id||a.code)}>
                    <td style={{padding:'8px 8px',textAlign:'center',color:C.muted}}>{isExp?'▾':'▸'}</td>
                    <td style={{padding:'8px 10px',color:C.gold,fontWeight:600,whiteSpace:'nowrap'}}>{a.code}</td>
                    <td style={{padding:'8px 10px',color:C.text,maxWidth:280,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                      title={a.name}>{a.name}</td>
                    <td style={{padding:'8px 10px',color:C.muted,fontSize:11,maxWidth:160,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                      title={a.wbs||a.wbsPath}>{(a.wbs||a.wbsPath||'—').split('/').pop()}</td>
                    <td style={{padding:'8px 10px',textAlign:'right',color:C.green,fontWeight:600}}>
                      {(a.pctComplete||0).toFixed(1)}%
                    </td>
                    <td style={{padding:'8px 10px',textAlign:'right',
                      color:a.severity==='HIGH'?C.red:a.severity==='MEDIUM'?C.amber:C.gold,fontWeight:600}}>
                      +{a.progressGap.toFixed(1)}%
                    </td>
                    <td style={{padding:'8px 10px',textAlign:'center'}}>
                      <span style={{background:SEV_BG[a.severity],color:SEV_COLOR[a.severity],
                        border:`1px solid ${SEV_COLOR[a.severity]}40`,
                        borderRadius:6,padding:'2px 8px',fontWeight:700,fontSize:10}}>
                        {a.severity}
                      </span>
                    </td>
                    <td style={{padding:'8px 10px',textAlign:'right',color:C.muted}}>{a.oosOffenders.length}</td>
                  </tr>
                  {isExp&&<tr style={{background:C.panel}}>
                    <td colSpan={8} style={{padding:'0 0 0 32px'}}>
                      <table style={{width:'100%',borderCollapse:'collapse',fontSize:11}}>
                        <thead>
                          <tr style={{color:C.muted,borderBottom:`1px solid ${C.border}`}}>
                            <th style={{padding:'6px 10px',textAlign:'left',fontWeight:600}}>Predecessor ID</th>
                            <th style={{padding:'6px 10px',textAlign:'left',fontWeight:600}}>Predecessor Name</th>
                            <th style={{padding:'6px 10px',textAlign:'center',fontWeight:600}}>Rel</th>
                            <th style={{padding:'6px 10px',textAlign:'right',fontWeight:600}}>Lag (days)</th>
                            <th style={{padding:'6px 10px',textAlign:'right',fontWeight:600}}>Pred % Complete</th>
                            <th style={{padding:'6px 10px',textAlign:'left',fontWeight:600}}>Issue</th>
                          </tr>
                        </thead>
                        <tbody>
                          {a.oosOffenders.map((o:any,oi:number)=>(
                            <tr key={oi} style={{borderTop:`1px solid ${C.border}20`,background:oi%2===0?'transparent':'rgba(0,0,0,0.1)'}}>
                              <td style={{padding:'5px 10px',color:C.amber,fontWeight:600}}>{o.code}</td>
                              <td style={{padding:'5px 10px',color:C.muted2,maxWidth:240,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                                title={o.name}>{o.name}</td>
                              <td style={{padding:'5px 10px',textAlign:'center'}}>
                                <span style={{background:C.card2,borderRadius:4,padding:'1px 6px',color:C.accent,fontWeight:700}}>{o.relType}</span>
                              </td>
                              <td style={{padding:'5px 10px',textAlign:'right',color:C.muted}}>{o.lagDays||0}d</td>
                              <td style={{padding:'5px 10px',textAlign:'right'}}>
                                <span style={{color:o.pctComplete>=80?C.green:o.pctComplete>=50?C.amber:C.red,fontWeight:600}}>
                                  {o.pctComplete.toFixed(1)}%
                                </span>
                              </td>
                              <td style={{padding:'5px 10px',color:C.muted,fontStyle:'italic'}}>{o.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </td>
                  </tr>}
                </Fragment>;
              })}
            </tbody>
          </table>
        </div>
        {/* Pagination */}
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',
          padding:'10px 16px',borderTop:`1px solid ${C.border}`}}>
          <span style={{color:C.muted,fontSize:12}}>{filtered.length} OOS activities</span>
          <div style={{display:'flex',gap:8,alignItems:'center'}}>
            <button type="button" onClick={()=>setPage(p=>Math.max(0,p-1))} disabled={page===0}
              style={{background:page===0?C.card2:C.accent,color:page===0?C.muted:'#000',
                border:'none',borderRadius:5,padding:'4px 12px',
                cursor:page===0?'default':'pointer',fontSize:12}}>‹</button>
            <span style={{color:C.muted,fontSize:12}}>Page {page+1}/{totalPages}</span>
            <button type="button" onClick={()=>setPage(p=>Math.min(totalPages-1,p+1))} disabled={page>=totalPages-1}
              style={{background:page>=totalPages-1?C.card2:C.accent,color:page>=totalPages-1?C.muted:'#000',
                border:'none',borderRadius:5,padding:'4px 12px',
                cursor:page>=totalPages-1?'default':'pointer',fontSize:12}}>›</button>
          </div>
        </div>
      </div>
    </>}
  </div>;
}

// ─── PHASE SIDEBAR HELPERS ────────────────────────────────────────────────────
function matchPhase(a:any, keywords:string[]): boolean {
  const haystack = [a.name||'', a.wbs||'', a.wbsPath||'', a.category||''].join(' ').toLowerCase();
  return keywords.some(k => haystack.includes(k));
}

function PhasesSidebar({phases, active, counts, onSelect}:{
  phases: readonly {id:string;label:string;icon:string}[];
  active: string|null;
  counts: Record<string,number>;
  onSelect:(id:string|null)=>void;
}){
  const total=Object.values(counts).reduce((s,v)=>s+v,0);
  return(
    <div style={{
      width:178,flexShrink:0,background:C.panel,borderRight:`1px solid ${C.border}`,
      display:'flex',flexDirection:'column',paddingTop:12,position:'sticky',
      top:0,height:'calc(100vh - 110px)',overflowY:'auto',
    }}>
      <div style={{padding:'0 14px 10px',fontSize:10,color:C.muted,fontWeight:700,letterSpacing:'0.08em',textTransform:'uppercase'}}>
        Filter by Phase
      </div>

      {/* All button */}
      <button
        onClick={()=>onSelect(null)}
        style={{
          display:'flex',alignItems:'center',gap:10,padding:'9px 14px',
          background:active===null?`${C.accent}18`:'transparent',
          border:'none',borderLeft:active===null?`3px solid ${C.accent}`:'3px solid transparent',
          color:active===null?C.accent:C.text,cursor:'pointer',width:'100%',textAlign:'left',
          fontSize:13,fontWeight:active===null?700:400,transition:'all 0.15s',
        }}
      >
        <span style={{fontSize:15}}>📂</span>
        <span style={{flex:1}}>All Activities</span>
        <span style={{fontSize:10,color:C.muted,background:C.card2,borderRadius:10,padding:'1px 6px'}}>{total}</span>
      </button>

      <div style={{height:1,background:C.border,margin:'6px 10px'}}/>

      {phases.map(p=>{
        const cnt=counts[p.id]||0;
        const isActive=active===p.id;
        return(
          <button key={p.id}
            onClick={()=>onSelect(isActive?null:p.id)}
            style={{
              display:'flex',alignItems:'center',gap:10,padding:'9px 14px',
              background:isActive?`${C.accent}18`:'transparent',
              border:'none',borderLeft:isActive?`3px solid ${C.accent}`:'3px solid transparent',
              color:isActive?C.accent:cnt===0?C.muted:C.text,
              cursor:cnt===0?'default':'pointer',width:'100%',textAlign:'left',
              fontSize:13,fontWeight:isActive?700:400,transition:'all 0.15s',
              opacity:cnt===0?0.5:1,
            }}
          >
            <span style={{fontSize:15}}>{p.icon}</span>
            <span style={{flex:1,lineHeight:1.25}}>{p.label}</span>
            <span style={{fontSize:10,color:isActive?C.accent:C.muted,background:C.card2,borderRadius:10,padding:'1px 6px',flexShrink:0}}>{cnt}</span>
          </button>
        );
      })}
    </div>
  );
}

// ─── STATUS VIEW ─────────────────────────────────────────────────────────────

const STATUS_COLORS:Record<string,string>={
  ON_TRACK: '#00936b',
  AT_RISK:  '#c47c00',
  OFF_TRACK:'#d93030',
  UNDETERMINED:'#7a6454',
};
const STATUS_LABELS:Record<string,string>={
  ON_TRACK: 'ON TRACK',
  AT_RISK:  'AT RISK',
  OFF_TRACK:'OFF TRACK',
  UNDETERMINED:'UNDETERMINED',
};
const STATUS_ICONS:Record<string,string>={
  ON_TRACK:'✅', AT_RISK:'⚠️', OFF_TRACK:'🔴', UNDETERMINED:'❓',
};
const SEVERITY_COLORS:Record<string,string>={RED:'#d93030',YELLOW:'#c47c00',CRITICAL:'#8b0000',OK:'#00936b'};

function ScoreMeter({label,score,color,size=64,tooltip}:{label:string;score:number;color:string;size?:number;tooltip?:string}){
  const r=size*0.38;const circ=2*Math.PI*r;const dash=circ*(score/100);
  return(
    <div title={tooltip} style={{display:'flex',flexDirection:'column',alignItems:'center',gap:4,cursor:tooltip?'help':'default'}}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={C.border} strokeWidth={size*0.09}/>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={size*0.09}
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
          transform={`rotate(-90 ${size/2} ${size/2})`}/>
        <text x={size/2} y={size/2+size*0.065} textAnchor="middle" fontSize={size*0.22}
          fontWeight={700} fill={color} fontFamily="'DM Sans',sans-serif">
          {Math.round(score)}
        </text>
      </svg>
      <span style={{fontSize:10,color:C.muted,fontWeight:600,letterSpacing:'0.05em',textTransform:'uppercase',display:'flex',alignItems:'center',gap:3}}>
        {label}{tooltip&&<span style={{fontSize:9,opacity:0.6}}>ⓘ</span>}
      </span>
    </div>
  );
}

function StatusBadge({status}:{status:string}){
  const color=STATUS_COLORS[status]||C.muted;
  return(
    <div style={{
      display:'inline-flex',alignItems:'center',gap:8,
      padding:'10px 22px',borderRadius:12,
      background:`${color}18`,border:`2px solid ${color}`,
    }}>
      <span style={{fontSize:22}}>{STATUS_ICONS[status]||'❓'}</span>
      <span style={{fontSize:22,fontWeight:900,color,letterSpacing:'0.04em'}}>
        {STATUS_LABELS[status]||status}
      </span>
    </div>
  );
}

function QualityFinding({f}:{f:any}){
  const col=SEVERITY_COLORS[f.severity]||C.muted;
  return(
    <div style={{borderLeft:`3px solid ${col}`,paddingLeft:12,marginBottom:10}}>
      <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:3}}>
        <span style={{fontSize:11,fontWeight:700,color:col,textTransform:'uppercase',letterSpacing:'0.04em'}}>{f.severity}</span>
        <span style={{fontSize:12,fontWeight:700,color:C.text}}>{f.check}</span>
      </div>
      <div style={{fontSize:12,color:C.muted,marginBottom:3}}>{f.detail}</div>
      <div style={{fontSize:11,color:C.accent,fontStyle:'italic'}}>{f.recommendation}</div>
    </div>
  );
}

function MilestoneCard({m}:{m:any}){
  const statusColor=m.status==='COMPLETE'?C.green:m.status==='DELAYED'?C.red:m.status==='AT_RISK'?C.amber:C.muted;
  return(
    <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:10,padding:'12px 16px',
      borderLeft:`4px solid ${statusColor}`}}>
      <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
        <span style={{fontSize:10,fontWeight:700,color:statusColor,textTransform:'uppercase',letterSpacing:'0.05em'}}>{m.status||'—'}</span>
        {m.is_contractual&&<span style={{fontSize:9,background:`${C.gold}22`,color:C.gold,borderRadius:4,padding:'1px 6px',fontWeight:700}}>CONTRACT</span>}
      </div>
      <div style={{fontSize:13,fontWeight:700,color:C.text,marginBottom:2}}>{m.description||m.activity_id}</div>
      <div style={{fontSize:11,color:C.muted,marginBottom:4}}>{m.activity_id}</div>
      {m.contract_required_date&&<div style={{fontSize:11,color:C.muted}}>Contract date: <strong style={{color:C.text}}>{m.contract_required_date}</strong></div>}
      {m.forecast_date&&<div style={{fontSize:11,color:C.muted}}>Forecast: <strong style={{color:C.text}}>{m.forecast_date}</strong></div>}
      {m.variance_days!=null&&(
        <div style={{fontSize:11,fontWeight:700,color:Math.abs(m.variance_days)<5?C.green:m.variance_days>0?C.red:C.green,marginTop:2}}>
          {m.variance_days>0?`+${m.variance_days}d late`:m.variance_days<0?`${Math.abs(m.variance_days)}d early`:'On time'}
        </div>
      )}
      {m.summary&&<div style={{fontSize:11,color:C.muted2,marginTop:4,fontStyle:'italic'}}>{m.summary}</div>}
    </div>
  );
}

function TriggeredThreshold({t}:{t:any}){
  return(
    <div style={{background:`${C.red}08`,border:`1px solid ${C.red}30`,borderRadius:8,padding:'10px 14px',marginBottom:8}}>
      <div style={{display:'flex',alignItems:'center',gap:8,marginBottom:4}}>
        <span style={{fontSize:11,fontWeight:700,color:C.red,fontFamily:'monospace',background:`${C.red}15`,borderRadius:4,padding:'1px 6px'}}>
          {t.code}
        </span>
        {t.value!=null&&<span style={{fontSize:11,color:C.muted}}>Value: <strong style={{color:C.text}}>{typeof t.value==='number'?t.value.toFixed(1):t.value}</strong></span>}
        {t.threshold!=null&&<span style={{fontSize:11,color:C.muted}}>Threshold: <strong style={{color:C.text}}>{typeof t.threshold==='number'?t.threshold.toFixed(1):t.threshold}</strong></span>}
      </div>
      <div style={{fontSize:12,color:C.text}}>{t.message}</div>
    </div>
  );
}

function StatusView({allActivities,dataDate,onOpenActivityAnalysis}:{allActivities:any[];dataDate:string;onOpenActivityAnalysis?:()=>void}){
  const [result,setResult]=useState<any>(null);
  const [loading,setLoading]=useState(false);
  const [err,setErr]=useState<string|null>(null);
  const [contractFinish,setContractFinish]=useState('');
  const [milestoneRows,setMilestoneRows]=useState<{actId:string;desc:string;contractDate:string;isContractual:boolean}[]>([]);
  const [showAddMs,setShowAddMs]=useState(false);
  const [newMs,setNewMs]=useState({actId:'',desc:'',contractDate:'',isContractual:true});
  const [expandQuality,setExpandQuality]=useState(false);
  const [expandTriggered,setExpandTriggered]=useState(true);
  const [expandMilestones,setExpandMilestones]=useState(true);
  const [drillKey,setDrillKey]=useState<null|'critical'|'near'|'missedStart'|'missedFinish'>(null);

  // Predicates mirror scheduler/status_engine.py exactly (_is_critical, _is_near_critical,
  // missed starts/finishes) so the drill-down list matches the counts shown in the KPI tiles.
  const drillTitles:Record<string,string>={
    critical:'Critical Activities',
    near:'Near-Critical Activities (float 1–5d)',
    missedStart:'Activities with a Missed Start',
    missedFinish:'Activities with a Missed Finish',
  };
  const drillList=useMemo(()=>{
    if(!drillKey)return[];
    // Missed-start/missed-finish drill-downs compare against the active
    // schedule's own effective Data Date — never today's date. If it's
    // genuinely unavailable, those two predicates simply match nothing
    // rather than silently substituting today.
    const dd=parseDate(dataDate);
    const isIncomplete=(a:any)=>!a.isMilestone&&(a.pctComplete||0)<100;
    const preds:Record<string,(a:any)=>boolean>={
      critical:(a:any)=>isIncomplete(a)&&(a.isCritical||(a.totalFloat!=null&&a.totalFloat<=0)),
      near:(a:any)=>isIncomplete(a)&&a.totalFloat!=null&&a.totalFloat>0&&a.totalFloat<=5,
      missedStart:(a:any)=>{
        if(!isIncomplete(a)||!dd)return false;
        const bs=a.bStart instanceof Date?a.bStart:a.bStart?new Date(a.bStart):null;
        return!!bs&&bs<dd&&!a.start;
      },
      missedFinish:(a:any)=>{
        if(!isIncomplete(a)||!dd)return false;
        const bf=a.bFinish instanceof Date?a.bFinish:a.bFinish?new Date(a.bFinish):null;
        return!!bf&&bf<dd&&(a.pctComplete||0)<100;
      },
    };
    return(allActivities||[]).filter(preds[drillKey]).sort((a:any,b:any)=>(a.totalFloat??0)-(b.totalFloat??0));
  },[drillKey,allActivities,dataDate]);

  const run=useCallback(async()=>{
    if(!allActivities?.length){setErr('No activities loaded.');return;}
    setLoading(true);setErr(null);setResult(null);
    const body:any={
      current_activities:allActivities,
      // No "|| today" fallback — the backend's own NO_DATA_DATE gate
      // (status_engine.py) must be allowed to trigger when the active
      // schedule genuinely has no effective Data Date, rather than this
      // call silently substituting today and masking that condition.
      data_date:dataDate||undefined,
    };
    if(contractFinish)body.contract_finish_date=contractFinish;
    if(milestoneRows.length){
      body.milestones=milestoneRows.filter(m=>m.actId).map(m=>({
        activityId:m.actId,
        description:m.desc,
        contractRequiredDate:m.contractDate||undefined,
        isContractual:m.isContractual,
      }));
    }
    try{
      const r=await fetch(`${API}/api/analyze/`,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body),
      });
      const text=await r.text();
      if(!r.ok){let d=text;try{d=JSON.parse(text).error||text;}catch{}throw new Error(d.slice(0,500));}
      setResult(JSON.parse(text));
    }catch(e:any){
      setErr(e.message||String(e));
    }finally{setLoading(false);}
  },[allActivities,dataDate,contractFinish,milestoneRows]);

  const statusColor=result?STATUS_COLORS[result.status]||C.muted:C.muted;

  if(!allActivities?.length){
    return(
      <div style={{padding:40,textAlign:'center',color:C.muted}}>
        <div style={{fontSize:40,marginBottom:12}}>🎯</div>
        <div style={{fontWeight:700,fontSize:16,marginBottom:6}}>Schedule Status Engine</div>
        <div style={{fontSize:13}}>Upload a schedule file to run the classification engine.</div>
      </div>
    );
  }

  return(
    <div style={{maxWidth:1100}}>
      {/* ── Concept banner: STATUS is one of three distinct ScheduleIQ health
          pillars (QUALITY / STATUS / RISK) — see also Open Ends Check (Quality)
          and Risk & Milestones (Risk). Kept as a small explainer, not a
          redesign. ── */}
      <div style={{background:`${C.accent}0a`,border:`1px solid ${C.accent}30`,borderRadius:10,padding:'10px 16px',marginBottom:14,fontSize:12,color:C.muted,display:'flex',alignItems:'center',gap:8}}>
        <span style={{fontSize:14}}>🎯</span>
        <span><strong style={{color:C.accent}}>STATUS</strong> answers "how is the project currently performing?" — a classification against contract dates, milestones, and baseline. See <strong>Open Ends Check</strong> for schedule <strong>Quality</strong> (is it built correctly) and <strong>Risk &amp; Milestones</strong> for <strong>Risk</strong> (where future exposure is concentrated).</span>
      </div>
      {/* ── Config Panel ── */}
      <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'18px 22px',marginBottom:18}}>
        <div style={{fontWeight:800,fontSize:14,color:C.text,marginBottom:14}}>🎯 Schedule Status Engine</div>

        {/* Contract finish */}
        <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:14,flexWrap:'wrap'}}>
          <div style={{display:'flex',alignItems:'center',gap:8}}>
            <span style={{fontSize:12,color:C.muted,fontWeight:600}}>Contract Finish Date</span>
            <input type="date" value={contractFinish} onChange={e=>setContractFinish(e.target.value)}
              style={{border:`1px solid ${C.border}`,borderRadius:7,padding:'5px 10px',fontSize:12,fontFamily:'inherit',background:C.bg,color:C.text,outline:'none'}}/>
            {contractFinish&&<button onClick={()=>setContractFinish('')} style={{background:'transparent',border:'none',color:C.muted,cursor:'pointer',fontSize:12}}>✕</button>}
          </div>
          <div style={{color:C.muted2,fontSize:11}}>|</div>
          <span style={{fontSize:11,color:C.muted}}>Data date: <strong style={{color:C.text}}>{dataDate}</strong> · Activities: <strong style={{color:C.text}}>{allActivities.length.toLocaleString()}</strong></span>
        </div>

        {/* Milestones */}
        <div style={{marginBottom:14}}>
          <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:8}}>
            <span style={{fontSize:12,fontWeight:700,color:C.text}}>Contractual Milestones</span>
            <button onClick={()=>setShowAddMs(v=>!v)}
              style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:6,padding:'3px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
              + Add
            </button>
          </div>
          {showAddMs&&(
            <div style={{display:'flex',gap:8,alignItems:'center',flexWrap:'wrap',
              background:C.panel,borderRadius:8,padding:'10px 14px',marginBottom:8}}>
              <input placeholder="Activity ID" value={newMs.actId}
                onChange={e=>setNewMs(p=>({...p,actId:e.target.value}))}
                style={{border:`1px solid ${C.border}`,borderRadius:6,padding:'5px 9px',fontSize:11,fontFamily:'inherit',background:C.bg,color:C.text,width:110}}/>
              <input placeholder="Description" value={newMs.desc}
                onChange={e=>setNewMs(p=>({...p,desc:e.target.value}))}
                style={{border:`1px solid ${C.border}`,borderRadius:6,padding:'5px 9px',fontSize:11,fontFamily:'inherit',background:C.bg,color:C.text,flex:1,minWidth:130}}/>
              <input type="date" value={newMs.contractDate}
                onChange={e=>setNewMs(p=>({...p,contractDate:e.target.value}))}
                style={{border:`1px solid ${C.border}`,borderRadius:6,padding:'5px 9px',fontSize:11,fontFamily:'inherit',background:C.bg,color:C.text}}/>
              <label style={{display:'flex',alignItems:'center',gap:5,fontSize:11,color:C.text,cursor:'pointer'}}>
                <input type="checkbox" checked={newMs.isContractual} onChange={e=>setNewMs(p=>({...p,isContractual:e.target.checked}))}/>
                Contractual
              </label>
              <button onClick={()=>{
                if(!newMs.actId)return;
                setMilestoneRows(p=>[...p,{...newMs}]);
                setNewMs({actId:'',desc:'',contractDate:'',isContractual:true});
                setShowAddMs(false);
              }} style={{background:C.accent,color:'#fff',border:'none',borderRadius:6,padding:'5px 12px',cursor:'pointer',fontSize:11,fontFamily:'inherit',fontWeight:700}}>
                Save
              </button>
              <button onClick={()=>setShowAddMs(false)}
                style={{background:'transparent',border:`1px solid ${C.border}`,color:C.muted,borderRadius:6,padding:'5px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
                Cancel
              </button>
            </div>
          )}
          {milestoneRows.length>0&&(
            <div style={{display:'flex',flexWrap:'wrap',gap:8}}>
              {milestoneRows.map((m,i)=>(
                <div key={i} style={{display:'flex',alignItems:'center',gap:6,
                  background:C.panel,borderRadius:7,padding:'4px 10px',fontSize:11,border:`1px solid ${C.border}`}}>
                  <strong style={{color:C.text}}>{m.actId}</strong>
                  {m.desc&&<span style={{color:C.muted}}>{m.desc}</span>}
                  {m.contractDate&&<span style={{color:C.accent}}>{m.contractDate}</span>}
                  {m.isContractual&&<span style={{fontSize:9,background:`${C.gold}22`,color:C.gold,borderRadius:4,padding:'1px 5px',fontWeight:700}}>CONTRACT</span>}
                  <button onClick={()=>setMilestoneRows(p=>p.filter((_,j)=>j!==i))}
                    style={{background:'transparent',border:'none',color:C.muted2,cursor:'pointer',fontSize:11,padding:0,lineHeight:1}}>✕</button>
                </div>
              ))}
            </div>
          )}
        </div>

        <button onClick={run} disabled={loading}
          style={{background:loading?C.muted:C.accent,color:'#fff',border:'none',borderRadius:9,
            padding:'10px 24px',cursor:loading?'default':'pointer',fontSize:14,fontFamily:'inherit',
            fontWeight:700,letterSpacing:'0.01em',opacity:loading?0.7:1}}>
          {loading?'Analyzing…':'Run Classification'}
        </button>
      </div>

      {err&&(
        <div style={{background:`${C.red}10`,border:`1px solid ${C.red}40`,borderRadius:10,padding:'14px 18px',marginBottom:16}}>
          <div style={{fontWeight:700,color:C.red,marginBottom:4}}>Analysis error</div>
          <pre style={{fontSize:11,color:C.amber,margin:0,whiteSpace:'pre-wrap',wordBreak:'break-all'}}>{err}</pre>
        </div>
      )}

      {result&&(
        <div style={{display:'flex',flexDirection:'column',gap:16}}>

          {/* ── Status Header ── */}
          <div style={{background:C.card,border:`2px solid ${statusColor}30`,borderRadius:14,padding:'24px 28px'}}>
            <div style={{display:'flex',alignItems:'flex-start',gap:28,flexWrap:'wrap'}}>
              <div style={{flex:1,minWidth:240}}>
                <div style={{fontSize:11,fontWeight:700,color:C.muted,textTransform:'uppercase',letterSpacing:'0.08em',marginBottom:10}}>
                  Classification Result
                </div>
                <StatusBadge status={result.status}/>
                {result.primary_reason&&(
                  <div style={{marginTop:12,fontSize:13,color:C.text,lineHeight:1.5,maxWidth:500}}>
                    {result.primary_reason}
                  </div>
                )}
              </div>
              <div style={{display:'flex',gap:22,flexWrap:'wrap',alignItems:'center'}}>
                <ScoreMeter label="Off-Track Factor" score={result.risk_score||0}
                  color={result.risk_score>60?C.red:result.risk_score>35?C.amber:C.green}
                  tooltip="STATUS: how strongly current indicators (milestones, critical float, progress) point toward AT RISK/OFF TRACK. Not the same number as the Risk Heat Map under Risk & Milestones, which measures where future exposure is concentrated."/>
                <ScoreMeter label="Confidence" score={result.confidence_score||0} color={C.accent}
                  tooltip="How much supporting data (baseline, previous update, contractual milestones) was available for this classification — not a schedule health score itself."/>
                <ScoreMeter label="Schedule Quality" score={result.schedule_quality_score||0}
                  color={result.schedule_quality_score<60?C.red:result.schedule_quality_score<80?C.amber:C.green}
                  tooltip="QUALITY: how structurally sound the schedule is — DCMA-style checks for open ends, constraints, circular logic, excessive float. Independent of current progress; see the Open Ends Check tab for the live open-ends view."/>
              </div>
            </div>
          </div>

          {/* ── Key Metrics Row ── */}
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))',gap:10}}>
            {[
              {label:'Critical Activities',value:result.critical_activity_count,unit:'acts',color:C.red,key:'critical'},
              {label:'Near-Critical',value:result.near_critical_activity_count,unit:'acts',color:C.amber,key:'near'},
              {label:'Critical Float',value:result.critical_path_float_days!=null?`${result.critical_path_float_days.toFixed(1)}d`:null,color:result.critical_path_float_days<0?C.red:C.green,key:'critical'},
              {label:'Missed Starts',value:result.missed_starts_count,unit:'acts',color:result.missed_starts_count>0?C.red:C.green,key:'missedStart'},
              {label:'Missed Finishes',value:result.missed_finishes_count,unit:'acts',color:result.missed_finishes_count>0?C.red:C.green,key:'missedFinish'},
              {label:'Contract Variance',value:result.contract_finish_variance_days!=null?`${result.contract_finish_variance_days>0?'+':''}${result.contract_finish_variance_days.toFixed(0)}d`:null,color:result.contract_finish_variance_days>14?C.red:result.contract_finish_variance_days>0?C.amber:C.green,key:null},
              {label:'Forecast Finish',value:result.project_forecast_finish,color:C.text,key:null},
              {label:'Near-Crit Paths',value:result.near_critical_path_count,color:result.near_critical_path_count>=3?C.amber:C.green,key:'near'},
            ].filter(k=>k.value!=null&&k.value!==0||k.label==='Contract Variance'||k.label==='Critical Float').map((k,i)=>{
              const clickable=!!k.key&&k.value;
              return(
                <div key={i} onClick={clickable?()=>setDrillKey(prev=>prev===k.key?null:(k.key as any)):undefined}
                  style={{background:C.card,border:`1px solid ${clickable&&drillKey===k.key?C.accent:C.border}`,borderRadius:10,padding:'12px 16px',cursor:clickable?'pointer':'default',transition:'border-color 0.13s'}}>
                  <div style={{fontSize:10,color:C.muted,fontWeight:700,textTransform:'uppercase',letterSpacing:'0.05em',marginBottom:4}}>{k.label}</div>
                  <div style={{fontSize:20,fontWeight:900,color:k.color,lineHeight:1}}>{k.value??'—'}</div>
                  {k.unit&&<div style={{fontSize:10,color:C.muted2,marginTop:2}}>{k.unit}</div>}
                  {clickable&&<div style={{fontSize:9,color:C.accent,marginTop:5,fontWeight:600}}>{drillKey===k.key?'▲ Hide activities':'▼ View activities'}</div>}
                </div>
              );
            })}
          </div>

          {/* ── Drill-down: activities behind the selected KPI ── */}
          {drillKey&&(
            <div style={{background:C.card,border:`1px solid ${C.accent}50`,borderRadius:12,padding:'16px 18px'}}>
              <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:10}}>
                <div style={{fontWeight:800,fontSize:13,color:C.text}}>{drillTitles[drillKey]} ({drillList.length})</div>
                {onOpenActivityAnalysis&&(
                  <button onClick={onOpenActivityAnalysis} title="Open the full, authoritative activity list in Activity Analysis"
                    style={{background:`${C.accent}14`,border:`1px solid ${C.accent}40`,color:C.accent,borderRadius:6,padding:'3px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit',fontWeight:600}}>
                    Open in Activity Analysis →
                  </button>
                )}
                <button onClick={()=>setDrillKey(null)}
                  style={{marginLeft:'auto',background:'transparent',border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:'3px 10px',cursor:'pointer',fontSize:11,fontFamily:'inherit'}}>
                  ✕ Close
                </button>
              </div>
              {drillList.length===0?(
                <div style={{color:C.muted2,fontSize:12}}>No matching activities.</div>
              ):(
                <div style={{maxHeight:360,overflowY:'auto'}}>
                  <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
                    <thead><tr style={{textAlign:'left'}}>
                      <th style={{padding:'4px 8px',color:C.muted,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em'}}>Code</th>
                      <th style={{padding:'4px 8px',color:C.muted,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em'}}>Activity Name</th>
                      <th style={{padding:'4px 8px',color:C.muted,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em',textAlign:'right'}}>Float</th>
                      <th style={{padding:'4px 8px',color:C.muted,fontSize:10,textTransform:'uppercase',letterSpacing:'0.05em'}}>BL Finish</th>
                    </tr></thead>
                    <tbody>
                      {drillList.map((a:any,i:number)=>(
                        <tr key={a.id||a.code||i} style={{borderTop:`1px solid ${C.border}`}}>
                          <td style={{padding:'5px 8px',fontFamily:"'DM Mono',monospace",fontSize:10,color:C.muted}}>{a.code}</td>
                          <td style={{padding:'5px 8px',color:C.text}}>{a.name}</td>
                          <td style={{padding:'5px 8px',textAlign:'right',fontFamily:"'DM Mono',monospace",color:(a.totalFloat??0)<0?C.red:C.amber}}>{a.totalFloat??'—'}</td>
                          <td style={{padding:'5px 8px',color:C.muted2,fontSize:11}}>{fmtDate(a.bFinish instanceof Date?a.bFinish:null)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* ── Risk Score Breakdown ── */}
          {result.risk_score_breakdown&&Object.keys(result.risk_score_breakdown).length>0&&(
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'18px 22px'}}>
              <div style={{fontWeight:800,fontSize:13,color:C.text,marginBottom:2}}>Off-Track Factor Breakdown</div>
              <div style={{fontSize:11,color:C.muted2,marginBottom:14}}>How each STATUS input contributed — distinct from the Risk Heat Map score under Risk &amp; Milestones.</div>
              <div style={{display:'flex',flexDirection:'column',gap:8}}>
                {Object.entries(result.risk_score_breakdown).map(([cat,v]:any)=>(
                  <div key={cat} style={{display:'flex',alignItems:'center',gap:10}}>
                    <div style={{width:120,fontSize:11,fontWeight:700,color:C.muted,textTransform:'capitalize'}}>{cat.replace('_',' ')}</div>
                    <div style={{flex:1,background:C.panel,borderRadius:6,height:12,overflow:'hidden'}}>
                      <div style={{height:'100%',width:`${Math.min(100,v.score||0)}%`,
                        background:v.score>60?C.red:v.score>35?C.amber:C.green,borderRadius:6,
                        transition:'width 0.4s ease'}}/>
                    </div>
                    <div style={{width:70,fontSize:11,color:C.text,fontVariantNumeric:'tabular-nums',textAlign:'right'}}>
                      {(v.score||0).toFixed(0)} × {(v.weight||0).toFixed(0)}% = <strong style={{color:C.accent}}>{(v.contribution||0).toFixed(1)}</strong>
                    </div>
                  </div>
                ))}
              </div>
              <div style={{marginTop:12,fontSize:12,fontWeight:700,color:C.text,textAlign:'right'}}>
                Total risk score: <span style={{color:result.risk_score>60?C.red:result.risk_score>35?C.amber:C.green,fontSize:18}}>{(result.risk_score||0).toFixed(1)}</span>
              </div>
            </div>
          )}

          {/* ── Triggered Thresholds ── */}
          {result.triggered_thresholds?.length>0&&(
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'18px 22px'}}>
              <button onClick={()=>setExpandTriggered(v=>!v)}
                style={{width:'100%',display:'flex',alignItems:'center',gap:8,background:'none',border:'none',cursor:'pointer',padding:0,marginBottom:expandTriggered?14:0}}>
                <span style={{fontWeight:800,fontSize:13,color:C.text}}>Triggered Thresholds</span>
                <span style={{fontSize:11,background:`${C.red}18`,color:C.red,borderRadius:10,padding:'1px 8px',fontWeight:700}}>{result.triggered_thresholds.length}</span>
                <span style={{marginLeft:'auto',color:C.muted,fontSize:12}}>{expandTriggered?'▲':'▼'}</span>
              </button>
              {expandTriggered&&result.triggered_thresholds.map((t:any,i:number)=>(
                <TriggeredThreshold key={i} t={t}/>
              ))}
            </div>
          )}

          {/* ── Milestones ── */}
          {result.milestone_results?.length>0&&(
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'18px 22px'}}>
              <button onClick={()=>setExpandMilestones(v=>!v)}
                style={{width:'100%',display:'flex',alignItems:'center',gap:8,background:'none',border:'none',cursor:'pointer',padding:0,marginBottom:expandMilestones?14:0}}>
                <span style={{fontWeight:800,fontSize:13,color:C.text}}>Milestone Status</span>
                <span style={{fontSize:11,background:`${C.accent}18`,color:C.accent,borderRadius:10,padding:'1px 8px',fontWeight:700}}>{result.milestone_results.length}</span>
                <span style={{marginLeft:'auto',color:C.muted,fontSize:12}}>{expandMilestones?'▲':'▼'}</span>
              </button>
              {expandMilestones&&(
                <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(240px,1fr))',gap:10}}>
                  {result.milestone_results.map((m:any,i:number)=><MilestoneCard key={i} m={m}/>)}
                </div>
              )}
            </div>
          )}

          {/* ── Quality Details ── */}
          {result.quality_details&&(
            <div style={{background:C.card,border:`1px solid ${C.border}`,borderRadius:12,padding:'18px 22px'}}>
              <button onClick={()=>setExpandQuality(v=>!v)}
                style={{width:'100%',display:'flex',alignItems:'center',gap:8,background:'none',border:'none',cursor:'pointer',padding:0,marginBottom:expandQuality?14:0}}>
                <span style={{fontWeight:800,fontSize:13,color:C.text}}>Schedule Quality Detail</span>
                <span style={{fontSize:14,fontWeight:900,color:result.schedule_quality_score<60?C.red:result.schedule_quality_score<80?C.amber:C.green}}>
                  {result.schedule_quality_score?.toFixed(0)}/100
                </span>
                <span style={{marginLeft:'auto',color:C.muted,fontSize:12}}>{expandQuality?'▲':'▼'}</span>
              </button>
              {expandQuality&&result.quality_details.findings?.map((f:any,i:number)=>(
                <QualityFinding key={i} f={f}/>
              ))}
            </div>
          )}

          {/* ── Recommendations ── */}
          {result.recommended_actions?.length>0&&(
            <div style={{background:`${C.accent}09`,border:`1px solid ${C.accent}40`,borderRadius:12,padding:'18px 22px'}}>
              <div style={{fontWeight:800,fontSize:13,color:C.accent,marginBottom:12}}>Recommended Actions</div>
              <ol style={{margin:0,paddingLeft:18}}>
                {result.recommended_actions.map((a:string,i:number)=>(
                  <li key={i} style={{fontSize:13,color:C.text,marginBottom:8,lineHeight:1.5}}>{a}</li>
                ))}
              </ol>
            </div>
          )}

          {/* ── Data Limitations ── */}
          {result.data_limitations?.length>0&&(
            <div style={{background:`${C.muted}0a`,border:`1px solid ${C.border}`,borderRadius:10,padding:'14px 18px'}}>
              <div style={{fontWeight:700,fontSize:12,color:C.muted,marginBottom:8}}>Data Limitations</div>
              <ul style={{margin:0,paddingLeft:16}}>
                {result.data_limitations.map((d:string,i:number)=>(
                  <li key={i} style={{fontSize:12,color:C.muted2,marginBottom:4,lineHeight:1.5}}>{d}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── LEGACY VIEW → BACKEND WORKSPACE REDIRECT ──────────────────────────────
// Dashboard Consolidation Phase 1: several legacy client-side views
// independently recomputed something an authoritative backend workspace
// already computes correctly (see the Sep-2026 duplication audit). Rather
// than deleting their render code outright — real risk in a file this size
// — they are replaced with this same "moved" pattern Intelligence.tsx's
// Recovery Planner tab already used successfully, so the switch is
// reversible and low-risk. The legacy calculation functions themselves are
// left in place (dead code, not deleted) rather than risk removing a
// helper another view still depends on.
function LegacyMovedNotice({title, body, destinationLabel, destinationView, setView, goTo}:{
  title:string; body:string; destinationLabel:string; destinationView:string; setView:(v:string)=>void;
  // Optional: when the currently-selected local file is linked to a backend
  // Project/ScheduleVersion (imported via Import Center), pass its ids so
  // the destination workspace opens already scoped to the SAME schedule
  // instead of an empty "select a project" screen.
  goTo?:()=>void;
}){
  return (
    <div style={{textAlign:"center",padding:60}}>
      <div style={{fontSize:14,color:C.text,marginBottom:6,fontWeight:700}}>{title}</div>
      <div style={{fontSize:13,color:C.muted,marginBottom:18,maxWidth:480,marginLeft:"auto",marginRight:"auto"}}>{body}</div>
      <button onClick={()=>{if(goTo)goTo();setView(destinationView);}}
        style={{background:C.accent,color:"#fff",border:"none",borderRadius:8,padding:"10px 20px",fontSize:13,fontWeight:700,cursor:"pointer"}}>
        Go to {destinationLabel} →
      </button>
    </div>
  );
}

// ─── ROOT APP ─────────────────────────────────────────────────────────────────
export default function App(){
  const [files,setFiles]=useState<any[]>([]);
  const [selectedIds,setSelectedIds]=useState<any[]>([]);
  const [view,setView]=useState("portfolio");
  // Set right after a successful Import Center commit when the user picks a
  // quick-action ("View Update Intelligence" etc.) — lets those backend-
  // driven workspaces (which own their own project selector) pre-select the
  // just-imported project instead of showing an empty picker.
  const [preferredProjectId,setPreferredProjectId]=useState<string|undefined>(undefined);
  // Set when "Manage Versions" is clicked from Update Analysis / Baseline &
  // Progress — tells ProjectControls to land directly on its Schedule
  // Versions sub-tab instead of the default Cost Summary one.
  const [preferredSubTab,setPreferredSubTab]=useState<string|undefined>(undefined);
  // One-shot deep-link context for "Analyze Recovery" quick-links from
  // Update Intelligence / Critical Path into the Risk & Recovery workspace.
  const [preferredVersionId,setPreferredVersionId]=useState<string|undefined>(undefined);
  const [preferredRiskKey,setPreferredRiskKey]=useState<string|undefined>(undefined);
  const [preferredActivityId,setPreferredActivityId]=useState<string|undefined>(undefined);
  // One-shot filter seed for Activity Analysis, set by the legacy Activities
  // register's replacement deep links (global search jump, Portfolio KPI
  // drill-through, "view this project") — see activityNavigation.ts.
  const [preferredActivityFilters,setPreferredActivityFilters]=useState<Record<string,any>|undefined>(undefined);
  // One-shot filter seed for Float Analysis — Main Dashboard's Float
  // Health panel (negative/zero/near-critical).
  const [preferredFloatFilter,setPreferredFloatFilter]=useState<"negative"|"zero"|"nearCritical"|undefined>(undefined);
  const handleManageVersions=useCallback((projectId:string)=>{
    setPreferredProjectId(projectId);
    setPreferredSubTab("versions");
    setView("projectControls");
  },[]);
  const handleAnalyzeRecovery=useCallback((projectId:string,versionId?:string,riskKey?:string)=>{
    setPreferredProjectId(projectId);
    setPreferredVersionId(versionId);
    setPreferredRiskKey(riskKey);
    setPreferredSubTab("register");
    setView("riskIntel");
  },[]);
  // Cross-navigation from Activity Analysis's Activity Detail drawer into
  // Float Analysis, preserving project/version/activity context so the
  // scheduler never has to re-search for the same activity.
  const handleOpenFloatAnalysis=useCallback((projectId:string,versionId:string,activityId:string)=>{
    setPreferredProjectId(projectId);
    setPreferredVersionId(versionId);
    setPreferredActivityId(activityId);
    setView("floatAnalysis");
  },[]);
  // Main Dashboard drill-down handlers — a gateway into the detailed
  // workspaces, never a duplicate of them (see Dashboard.tsx).
  const handleDashboardOpenFloatAnalysis=useCallback((projectId:string,versionId:string,filter?:"negative"|"zero"|"nearCritical")=>{
    setPreferredProjectId(projectId);
    setPreferredVersionId(versionId);
    setPreferredFloatFilter(filter);
    setView("floatAnalysis");
  },[]);
  const handleDashboardOpenActivityAnalysis=useCallback((projectId:string,versionId:string,filters?:Record<string,any>)=>{
    setPreferredProjectId(projectId);
    setPreferredVersionId(versionId);
    setPreferredActivityFilters(filters||{});
    setView("activityAnalysis");
  },[]);
  const handleDashboardOpenUpdateAnalysis=useCallback((projectId:string)=>{
    setPreferredProjectId(projectId);
    setView("updateAnalysis");
  },[]);
  const handleDashboardOpenRiskMilestones=useCallback((projectId:string,versionId:string)=>{
    setPreferredProjectId(projectId);
    setPreferredVersionId(versionId);
    setView("riskIntel");
  },[]);
  const handleDashboardOpenProjectControls=useCallback((projectId:string,versionId:string,subTab?:string)=>{
    setPreferredProjectId(projectId);
    setPreferredSubTab(subTab);
    setView("projectControls");
  },[]);
  const handleDashboardOpenBaselineProgress=useCallback((projectId:string)=>{
    setPreferredProjectId(projectId);
    setView("baselineProgress");
  },[]);
  const handleDashboardOpenIntelligence=useCallback(()=>{
    setView("intelligence");
  },[]);
  // Manual top-nav clicks are not a quick-link — clear any one-shot deep-link
  // intent so navigating away and back (e.g. via the nav bar, not another
  // quick-link) lands on each workspace's normal default tab instead of
  // getting permanently "stuck" on whatever a prior quick-link requested.
  const handleNavClick=useCallback((v:string)=>{
    setPreferredSubTab(undefined);
    setPreferredVersionId(undefined);
    setPreferredRiskKey(undefined);
    setPreferredActivityId(undefined);
    setPreferredActivityFilters(undefined);
    setPreferredFloatFilter(undefined);
    setView(v);
  },[]);
  const [M,setM]=useState<any>(null);
  const [metricsLoading,setMetricsLoading]=useState(false); // true only until the FIRST metrics computation lands
  const [metricsRefreshing,setMetricsRefreshing]=useState(false); // true on every later recompute — never blocks the view
  const [metricsError,setMetricsError]=useState<string|null>(null);
  const [dataDate,setDataDate]=useState<string>(()=>new Date().toISOString().slice(0,10));

  const allActivities=useMemo(
    ()=>files.filter(f=>selectedIds.includes(f.id)).flatMap(f=>f.activities),
    [files,selectedIds]
  );

  // Project label used as print headline in all CC/Sec print outputs
  const projectLabel=useMemo(()=>{
    const sel=files.filter(f=>selectedIds.includes(f.id));
    if(sel.length===0)return'';
    if(sel.length===1)return sel[0].name;
    return sel.map(f=>f.name).join(' · ');
  },[files,selectedIds]);

  // Manual activity updates (user edits from web UI)
  const [activityUpdates,setActivityUpdates]=useState<Record<string,any>>({});

  // ── Undo stack (Ctrl+Z) ───────────────────────────────────────────────────
  const [undoStack,setUndoStack]=useState<any[]>([]);
  const [undoToast,setUndoToast]=useState<string|null>(null);
  // Ref always holds the latest snapshot so captureUndo can be stable (empty deps)
  const _undoSnap=useRef<any>({activityUpdates:{},phaseFilter:null,dataDate:'',logicEdits:{}});
  const captureUndo=useCallback(()=>{
    setUndoStack(prev=>[...prev.slice(-49),{..._undoSnap.current}]);
  },[]);

  const updateActivity=useCallback((id:string,chg:any)=>{
    captureUndo();
    setActivityUpdates(p=>({...p,[id]:{...(p[id]||{}),...chg}}));
  },[captureUndo]);

  // ── Schedule logic edits (FS/SS/FF/SF + lag) ──────────────────────────────
  const [logicEdits,setLogicEdits]=useState<LogicEditMap>({});
  const editRelationship=useCallback((predId:string,succId:string,chg:Partial<LogicEdit>)=>{
    captureUndo();
    const key=linkKey(predId,succId);
    setLogicEdits(prev=>({...prev,[key]:{...(prev[key]||{}),...chg}}));
  },[captureUndo]);
  const deleteRelationship=useCallback((predId:string,succId:string)=>{
    captureUndo();
    setLogicEdits(prev=>({...prev,[linkKey(predId,succId)]:{deleted:true}}));
  },[captureUndo]);
  const addRelationship=useCallback((predId:string,succId:string,relType:RelType,lagDays:number):boolean=>{
    if(wouldCreateCycle(allActivities,predId,succId))return false;
    captureUndo();
    setLogicEdits(prev=>({...prev,[linkKey(predId,succId)]:{relType,lagDays,deleted:false}}));
    return true;
  },[allActivities,captureUndo]);
  const clearLogicEdits=useCallback(()=>{captureUndo();setLogicEdits({});},[captureUndo]);

  const cpmActivities=useMemo(()=>{
    if(!Object.keys(logicEdits).length)return allActivities;
    return computeCPM(applyLogicEdits(allActivities,logicEdits),dataDate);
  },[allActivities,logicEdits,dataDate]);

  // P6-compliant Data Date projection → fill synthetic baselines → apply manual updates
  const adjustedActivities=useMemo(()=>{
    const base=fillBaselineDates(applyDataDate(cpmActivities,dataDate));
    if(!Object.keys(activityUpdates).length)return base;
    return base.map((a:any)=>{const id=a.id||a.code;const u=activityUpdates[id];return u?{...a,...u}:a;});
  },[cpmActivities,dataDate,activityUpdates]);

  // Fetch metrics whenever activities OR data date changes — uses cpmActivities
  // so portfolio aggregates (critical %, BEI, float buckets) reflect logic edits too.
  // Debounced + abortable so rapid edits (e.g. typing a lag value) don't fire a
  // request per keystroke or let a slow, stale response clobber a newer one — and
  // the view keeps showing the last-known metrics while a recompute is in flight
  // instead of blanking, so edits feel instant rather than causing a full reload.
  useEffect(()=>{
    if(!cpmActivities.length){setM(null);setMetricsError(null);setMetricsLoading(false);setMetricsRefreshing(false);return;}
    const isFirstLoad=!M;
    const ctrl=new AbortController();
    const run=()=>{
      if(isFirstLoad)setMetricsLoading(true);else setMetricsRefreshing(true);
      setMetricsError(null);
      fetch(`${API}/api/metrics/`,{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({activities:cpmActivities.map(actToJSON),dataDate}),
        signal:ctrl.signal,
      })
      .then(async r=>{
        const text=await r.text();
        if(!r.ok){
          // Try to parse Django error detail; fall back to raw text
          let detail=text;
          try{detail=JSON.parse(text).error||text;}catch{}
          throw new Error(`Metrics API ${r.status}: ${detail.slice(0,300)}`);
        }
        return JSON.parse(text);
      })
      .then(data=>{setM(deserializeMetrics(data));setMetricsLoading(false);setMetricsRefreshing(false);})
      .catch((err:any)=>{
        if(err?.name==='AbortError')return;
        setMetricsError(err.message||String(err));setMetricsLoading(false);setMetricsRefreshing(false);
      });
    };
    const timer=isFirstLoad?setTimeout(run,0):setTimeout(run,350);
    return()=>{clearTimeout(timer);ctrl.abort();};
  },[cpmActivities,dataDate]);

  // ── Load persisted projects (+ their logic edits) from IndexedDB on first render ──
  useEffect(()=>{
    idbLoadAll().then(saved=>{
      if(saved.length){
        setFiles(saved);
        // Restore a previously-narrowed single-file selection (e.g. the
        // user was viewing one specific schedule version's toolbar Data
        // Date) so a reload doesn't silently widen back to "all files" and
        // change what the toolbar shows — Test 6 in the Data Date spec.
        // Falls back to "all files selected" (the pre-existing default)
        // whenever there's no saved single selection, or it's stale.
        let restoredIds=saved.map((f:any)=>f.id);
        try{
          const savedSel=JSON.parse(localStorage.getItem('scheduleiq_selectedIds')||'null');
          if(Array.isArray(savedSel)&&savedSel.length===1&&saved.some((f:any)=>f.id===savedSel[0])){
            restoredIds=savedSel;
          }
        }catch{/* ignore malformed localStorage value */}
        setSelectedIds(restoredIds);
        const merged:LogicEditMap={};
        saved.forEach((f:any)=>{if(f.logicEdits)Object.assign(merged,f.logicEdits);});
        if(Object.keys(merged).length)setLogicEdits(merged);
        // Initialize the toolbar Data Date from the active selection's own
        // effective date rather than defaulting to today.
        const active=restoredIds.length===1?saved.filter((f:any)=>f.id===restoredIds[0]):saved;
        const eff=pickEffectiveDataDate(active);
        if(eff)setDataDate(eff);
      }
    }).catch(()=>{/* IDB unavailable — silently ignore */});
  },[]);

  // Remember a single-file selection across reloads (see above) — never
  // persists a multi-file selection, so "all files" stays the untouched
  // default whenever the user isn't specifically focused on one version.
  // Deliberately does nothing while selectedIds is still empty (its
  // pristine initial value, before the async IndexedDB-load effect above
  // has resolved) — otherwise this effect's own mount-time pass would wipe
  // out the very value that effect is about to read.
  useEffect(()=>{
    if(selectedIds.length===0)return;
    try{
      if(selectedIds.length===1)localStorage.setItem('scheduleiq_selectedIds',JSON.stringify(selectedIds));
      else localStorage.removeItem('scheduleiq_selectedIds');
    }catch{/* localStorage unavailable — session-only, non-fatal */}
  },[selectedIds]);

  // ── Toolbar Data Date follows the single active version when the user ──
  // switches which one file/project is selected in the project picker
  // (selecting exactly one narrows the session to that version — see
  // ProjectSelector's toggle()). With multiple files selected the toolbar
  // stays a session-wide projection date, same as it always has been.
  useEffect(()=>{
    if(selectedIds.length===1){
      const f=files.find((x:any)=>x.id===selectedIds[0]);
      if(f?.dataDate)setDataDate(f.dataDate);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[selectedIds]);

  // Persist logic edits onto each affected project's saved IndexedDB record,
  // so they survive a reload the same way an uploaded project does.
  useEffect(()=>{
    if(!files.length)return;
    const projectOf:Record<string,string>={};
    allActivities.forEach((a:any)=>{projectOf[a.id||a.code]=a.projectId;});
    files.forEach((f:any)=>{
      const projEdits:LogicEditMap={};
      for(const key in logicEdits){
        const predId=key.slice(0,key.indexOf('::'));
        if(projectOf[predId]===f.id)projEdits[key]=logicEdits[key];
      }
      if(Object.keys(projEdits).length||f.logicEdits)idbSave({...f,logicEdits:projEdits}).catch(()=>{});
    });
  },[logicEdits,files,allActivities]);

  const handleLoad=useCallback((newFiles:any[])=>{
    newFiles.forEach(f=>idbSave(f).catch(()=>{}));
    setFiles(newFiles);setSelectedIds(newFiles.map(f=>f.id));
  },[]);
  const handleAdd=useCallback((newFiles:any[])=>{
    newFiles.forEach(f=>idbSave(f).catch(()=>{}));
    setFiles(prev=>{const merged=[...prev,...newFiles];setSelectedIds(merged.map(f=>f.id));return merged;});
  },[]);

  // ── Import Center: files picked at Home (replace) vs "+ Add Files" (merge) ──
  const [pendingImportFiles,setPendingImportFiles]=useState<File[]|null>(null);
  const [pendingImportMode,setPendingImportMode]=useState<"load"|"add">("add");
  const handleSelectFilesToLoad=useCallback((fl:File[])=>{setPendingImportMode("load");setPendingImportFiles(fl);},[]);
  const handleSelectFilesToAdd=useCallback((fl:File[])=>{setPendingImportMode("add");setPendingImportFiles(fl);},[]);
  const handleImportCancel=useCallback(()=>setPendingImportFiles(null),[]);
  const handleImportConfirm=useCallback((results:any[])=>{
    const deserialized=results.map(r=>({...r,activities:r.activities.map(deserializeActivity)}));
    if(pendingImportMode==="load")handleLoad(deserialized);else handleAdd(deserialized);
    setPendingImportFiles(null);
    // Toolbar Data Date follows the just-imported file's own effective
    // Data Date (from ScheduleUpload, detected or user-confirmed at Import
    // Preview) — never today's date, never a stale previous file's date.
    const eff=pickEffectiveDataDate(deserialized);
    if(eff)setDataDate(eff);
  },[pendingImportMode,handleLoad,handleAdd]);
  const handleReset=useCallback(()=>{
    // Only clears the current view — saved projects survive a page refresh
    setFiles([]);setSelectedIds([]);setM(null);setView("portfolio");
    setDataDate(new Date().toISOString().slice(0,10));
  },[]);

  // The backend Project/ScheduleUpload rows are the single source of truth
  // (the Intelligence Portal reads the same rows), so a file that was
  // imported to the backend is deleted THERE first; only then is the local
  // copy removed. If the backend delete fails, nothing is removed locally
  // so the two never disagree.
  const handleDeleteProject=useCallback((id:string)=>{
    const f:any=files.find((x:any)=>x.id===id);
    const removeLocal=()=>{
      idbDelete(id).catch(()=>{});
      setFiles(prev=>{
        const next=prev.filter((x:any)=>x.id!==id);
        setSelectedIds(ids=>ids.filter((x:any)=>x!==id));
        return next;
      });
    };
    if(!(f?.projectId&&f?.scheduleUploadId)){removeLocal();return;}
    fetch(`/api/projects/${f.projectId}/versions/${f.scheduleUploadId}/`,{method:"DELETE"})
      .then(async r=>{
        if(r.status===404)return {projectNowEmpty:false};   // already gone server-side — just clear the local copy
        if(!r.ok)throw new Error(await r.text());
        return r.json();
      })
      .then(res=>{
        removeLocal();
        if(res.projectNowEmpty&&window.confirm("That was the last schedule version in this project.\n\nAlso delete the now-empty project record? (The Intelligence Portal already hides projects with no schedules.)")){
          fetch(`/api/projects/${f.projectId}/`,{method:"DELETE"}).catch(()=>{}).finally(notifyProjectsChanged);
        }else notifyProjectsChanged();
      })
      .catch((e:any)=>window.alert(`Could not delete this schedule from ScheduleIQ, so it was NOT removed: ${e.message||e}`));
  },[files]);

  // Activities register retired (Phase 1 consolidation, item #3) — these
  // three deep links now land in Activity Analysis instead, translated via
  // activityNavigation.ts's pure mapping (unit-tested in
  // tests/activityNavigation.test.mjs). Project/version scoping only applies
  // when the target file has real backend linkage (projectId +
  // scheduleUploadId from an Import Center commit) — the same disclosed
  // limitation as every other cross-workspace deep link in this app; a
  // locally-cached file with no backend record lands on Activity Analysis's
  // own (empty) project picker instead.
  const handleGoToActivity=useCallback((activity:any)=>{
    const f=files.find((x:any)=>x.id===activity.projectId||(x.activities||[]).some((a:any)=>a.projectId===activity.projectId));
    if(f?.projectId&&f?.scheduleUploadId){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}
    setPreferredActivityFilters(activityJumpFilter(activity.code||activity.name));
    setView("activityAnalysis");
  },[files]);

  const handleGoToFilter=useCallback((filt:string)=>{
    const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId);
    if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}
    setPreferredActivityFilters(legacyFilterToActivityAnalysis(filt));
    setView("activityAnalysis");
  },[files,selectedIds]);

  const handleGoToProject=useCallback((fileId:string)=>{
    setSelectedIds([fileId]);
    const f=files.find((x:any)=>x.id===fileId);
    if(f?.projectId&&f?.scheduleUploadId){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}
    setPreferredActivityFilters({});
    setView("activityAnalysis");
  },[files]);

  // ── Phase sidebar filter ──────────────────────────────────────────────────
  const [phaseFilter,setPhaseFilter]=useState<string|null>(null);

  const PHASE_CATS=[
    {id:'milestone',   label:'Milestones',              icon:'🏁', test:(a:any)=>!!a.isMilestone},
    {id:'design',      label:'Design',                  icon:'✏️',  test:(a:any)=>matchPhase(a,['design'])},
    {id:'engineering', label:'Engineering',              icon:'⚙️',  test:(a:any)=>matchPhase(a,['engineer','engr'])},
    {id:'submittals',  label:'Submittals',              icon:'📄', test:(a:any)=>matchPhase(a,['submittal','submit','rfi','rfq'])},
    {id:'construction',label:'Construction',            icon:'🏗️',  test:(a:any)=>matchPhase(a,['construct','civil','install','erect','fabricat'])},
    {id:'testing',     label:'Testing & Commissioning', icon:'🔬', test:(a:any)=>matchPhase(a,['test','commission','startup','start-up','t&c'])},
    {id:'closeout',    label:'Close Out',               icon:'✅', test:(a:any)=>matchPhase(a,['close','closeout','punch','handov','turnov','complet'])},
  ] as const;

  const phasedActivities=useMemo(()=>{
    if(!phaseFilter) return adjustedActivities;
    const cat=PHASE_CATS.find(c=>c.id===phaseFilter);
    if(!cat) return adjustedActivities;
    return adjustedActivities.filter((a:any)=>cat.test(a));
  },[adjustedActivities,phaseFilter]);

  const logicEditCtx=useMemo<LogicEditCtxValue>(()=>({
    logicEdits,editRelationship,deleteRelationship,addRelationship,allActivities,
  }),[logicEdits,editRelationship,deleteRelationship,addRelationship,allActivities]);

  // ── Keep undo snapshot ref current after every render ────────────────────
  useEffect(()=>{
    _undoSnap.current={activityUpdates,phaseFilter,dataDate,logicEdits};
  });

  // ── Ctrl+Z keyboard handler ───────────────────────────────────────────────
  useEffect(()=>{
    const showToast=(msg:string)=>{setUndoToast(msg);setTimeout(()=>setUndoToast(null),2500);};
    const handler=(e:KeyboardEvent)=>{
      if(!(e.ctrlKey||e.metaKey)||e.key!=='z'||e.shiftKey) return;
      const tag=(document.activeElement as HTMLElement)?.tagName?.toLowerCase();
      if(tag==='input'||tag==='textarea'||tag==='select') return; // let browser handle text undo
      e.preventDefault();
      setUndoStack(prev=>{
        if(!prev.length){showToast('Nothing to undo');return prev;}
        const snap=prev[prev.length-1];
        setActivityUpdates(snap.activityUpdates??{});
        setPhaseFilter(snap.phaseFilter??null);
        setDataDate(snap.dataDate??new Date().toISOString().slice(0,10));
        setLogicEdits(snap.logicEdits??{});
        const remaining=prev.length-1;
        showToast(`↩ Undone${remaining>0?` · ${remaining} step${remaining>1?'s':''} remaining`:''}`);
        return prev.slice(0,-1);
      });
    };
    document.addEventListener('keydown',handler);
    return ()=>document.removeEventListener('keydown',handler);
  },[]);

  // ── Undo-aware wrappers for phase filter and data date ───────────────────
  const setPhaseFilterU=useCallback((v:string|null)=>{captureUndo();setPhaseFilter(v);},[captureUndo]);
  const setDataDateU=useCallback((v:string)=>{captureUndo();setDataDate(v);},[captureUndo]);

  // ── Toolbar Data Date edit: when it unambiguously belongs to ONE real,
  // backend-persisted schedule version (exactly one file selected, and
  // that file has a projectId/scheduleUploadId from a real import commit),
  // persist the edit as that version's effective Data Date — same PATCH
  // Project Controls' own Data Date editor uses, so both stay consistent.
  // Falls back to the existing session-only projection date otherwise
  // (multiple files selected, or a file with no backend record — e.g.
  // legacy locally-cached data) so nothing here can silently fail or
  // regress the pre-existing "explore a hypothetical date" behavior.
  const handleDataDateChange=useCallback((v:string)=>{
    setDataDateU(v);
    if(selectedIds.length===1){
      const f=files.find((x:any)=>x.id===selectedIds[0]);
      if(f?.projectId&&f?.scheduleUploadId){
        fetch(`/api/projects/${f.projectId}/versions/${f.scheduleUploadId}/`,{
          method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({dataDate:v}),
        }).then(async r=>{
          if(!r.ok)return;
          const updated=await r.json();
          setFiles(prev=>{
            const next=prev.map((x:any)=>x.id===f.id?{...x,dataDate:updated.dataDate,sourceDataDate:updated.sourceDataDate,dataDateOverridden:updated.dataDateOverridden}:x);
            const saved=next.find((x:any)=>x.id===f.id);
            if(saved)idbSave(saved).catch(()=>{});
            return next;
          });
        }).catch(()=>{/* keep the session-only projection date even if the persisted update fails */});
      }
    }
  },[selectedIds,files,setDataDateU]);

  // ── Phase sidebar click → applies the phase filter across the legacy
  // views that consume phasedActivities, and opens Activity Analysis (which
  // has its own "Group by Phase (EPC)" option covering the same
  // categorization on the authoritative backend rows).
  const handlePhaseSelect=useCallback((v:string|null)=>{
    setPhaseFilterU(v);
    setView("activityAnalysis");
  },[setPhaseFilterU]);

  return(
    <Fragment>
    <PrintProjectContext.Provider value={projectLabel}>
    <LogicEditContext.Provider value={logicEditCtx}>
    <div style={{minHeight:"100vh",background:C.bg,fontFamily:"'DM Sans',sans-serif",color:C.text}}>
      <Header
        files={files} selectedIds={selectedIds} onSelectionChange={setSelectedIds}
        onSelectFiles={handleSelectFilesToAdd} onReset={handleReset} view={view} setView={handleNavClick}
        allActivities={allActivities} onGoToActivity={handleGoToActivity}
        dataDate={dataDate} onDataDateChange={handleDataDateChange} onResetDataDateToToday={setDataDateU}
        onDeleteProject={handleDeleteProject}
      />

      {/* Data date banner — shown when not today */}
      {dataDate!==new Date().toISOString().slice(0,10)&&(
        <div style={{background:dataDate>new Date().toISOString().slice(0,10)?"rgba(212,168,67,0.1)":"rgba(167,139,250,0.1)",borderBottom:`1px solid ${dataDate>new Date().toISOString().slice(0,10)?C.gold:C.purple}`,padding:"8px 22px",display:"flex",alignItems:"center",gap:12,fontSize:13}}>
          <span style={{fontSize:16}}>{dataDate>new Date().toISOString().slice(0,10)?"📅":"🕐"}</span>
          <span style={{color:dataDate>new Date().toISOString().slice(0,10)?C.gold:C.purple,fontWeight:600}}>
            {dataDate>new Date().toISOString().slice(0,10)
              ?`Forward-looking analysis — all metrics projected as of ${new Date(dataDate+"T12:00:00").toLocaleDateString("en-US",{month:"long",day:"numeric",year:"numeric"})}`
              :`Historical analysis — metrics as of ${new Date(dataDate+"T12:00:00").toLocaleDateString("en-US",{month:"long",day:"numeric",year:"numeric"})}`}
          </span>
          <span style={{color:C.muted2,fontSize:12}}>Completed work locked · In-progress projects from DD · Not-started constrained to DD · CPM float recalculated</span>
          <button onClick={()=>setDataDate(new Date().toISOString().slice(0,10))} style={{marginLeft:"auto",background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>Reset to Today</button>
        </div>
      )}

      {/* Schedule logic edit banner — shown whenever any relationship has been edited */}
      {Object.keys(logicEdits).length>0&&(
        <div style={{background:`${C.amber}12`,borderBottom:`1px solid ${C.amber}`,padding:"8px 22px",display:"flex",alignItems:"center",gap:12,fontSize:13}}>
          <span style={{fontSize:16}}>⚡</span>
          <span style={{color:C.amber,fontWeight:600}}>Schedule logic edited</span>
          <span style={{color:C.muted2,fontSize:12}}>Affected projects were recalculated the same way P6 would — early/late dates, total float and the critical path all reflect your changes.</span>
          <button onClick={clearLogicEdits} style={{marginLeft:"auto",background:"transparent",border:`1px solid ${C.border}`,color:C.muted2,borderRadius:6,padding:"3px 10px",cursor:"pointer",fontSize:11,fontFamily:"inherit"}}>Revert all logic edits</button>
        </div>
      )}

      {/* Non-blocking "recomputing" indicator — shown instead of hiding the dashboard */}
      {metricsRefreshing&&(
        <div style={{position:"fixed",top:14,right:18,zIndex:1500,background:C.card2,border:`1px solid ${C.accent}`,color:C.accent,borderRadius:20,padding:"6px 14px",fontSize:12,fontWeight:600,display:"flex",alignItems:"center",gap:7,boxShadow:"0 6px 20px rgba(0,0,0,0.4)"}}>
          <span style={{width:8,height:8,borderRadius:"50%",background:C.accent,display:"inline-block",animation:"pulse 1s ease-in-out infinite"}}/>
          Updating float & critical path…
        </div>
      )}

      <div style={{display:'flex',alignItems:'flex-start'}}>
        {/* Left phase sidebar — only shown when a file is loaded */}
        {M&&(()=>{
          const counts=Object.fromEntries(
            PHASE_CATS.map(p=>[p.id, adjustedActivities.filter((a:any)=>p.test(a)).length])
          );
          return <PhasesSidebar phases={PHASE_CATS} active={phaseFilter} counts={counts} onSelect={handlePhaseSelect}/>;
        })()}

        <div style={{flex:1,padding:22,minWidth:0}}>
          {phaseFilter&&M&&(
            <div style={{display:'flex',alignItems:'center',gap:10,marginBottom:14,
              background:`${C.accent}12`,border:`1px solid ${C.accent}40`,
              borderRadius:8,padding:'8px 14px',fontSize:13}}>
              <span style={{color:C.accent,fontWeight:700}}>
                {PHASE_CATS.find(p=>p.id===phaseFilter)?.icon}{' '}
                Filtering: {PHASE_CATS.find(p=>p.id===phaseFilter)?.label}
              </span>
              <span style={{color:C.muted,fontSize:12}}>— {phasedActivities.length} activities</span>
              <button type="button" onClick={()=>handlePhaseSelect(null)}
                style={{marginLeft:'auto',background:'transparent',border:`1px solid ${C.border}`,
                  color:C.muted2,borderRadius:5,padding:'2px 10px',cursor:'pointer',fontSize:11}}>
                ✕ Clear
              </button>
            </div>
          )}
          {metricsLoading&&<div style={{textAlign:"center",padding:"40px",color:C.accent,fontSize:14}}>Computing metrics…</div>}
          {metricsError&&!metricsLoading&&(
            <div style={{margin:"20px 0",background:"rgba(255,87,87,0.08)",border:`1px solid ${C.red}`,borderRadius:12,padding:"16px 20px"}}>
              <div style={{fontWeight:700,color:C.red,marginBottom:6}}>⚠ Metrics calculation failed</div>
              <pre style={{color:C.amber,fontSize:12,margin:0,whiteSpace:"pre-wrap",wordBreak:"break-all"}}>{metricsError}</pre>
              <div style={{fontSize:11,color:C.muted2,marginTop:8}}>The file was uploaded ({allActivities.length.toLocaleString()} activities loaded) but the analytics engine returned an error. Please send this message to support.</div>
            </div>
          )}
          {!M&&!metricsLoading&&!metricsError&&<HomeUploadPanel onLoad={handleLoad} onSelectFiles={handleSelectFilesToLoad}/>}
          {view==="dashboard"                      &&<Dashboard initialProjectId={preferredProjectId} initialVersionId={preferredVersionId}
            onOpenFloatAnalysis={handleDashboardOpenFloatAnalysis} onOpenActivityAnalysis={handleDashboardOpenActivityAnalysis}
            onOpenUpdateAnalysis={handleDashboardOpenUpdateAnalysis} onOpenRiskMilestones={handleDashboardOpenRiskMilestones}
            onOpenProjectControls={handleDashboardOpenProjectControls} onOpenBaselineProgress={handleDashboardOpenBaselineProgress}
            onOpenIntelligence={handleDashboardOpenIntelligence}/>}
          {view==="fieldDashboard"                 &&<FieldDashboard initialProjectId={preferredProjectId} initialVersionId={preferredVersionId}/>}
          {M&&!metricsLoading&&view==="portfolio"  &&<PortfolioView M={M} files={files} selectedIds={selectedIds} allActivities={phasedActivities} onGoToFilter={handleGoToFilter} onGoToProject={handleGoToProject}/>}
          {M&&!metricsLoading&&view==="scurve"     &&<SCurveView M={M} allActivities={phasedActivities} files={files} selectedIds={selectedIds}/>}
          {M&&!metricsLoading&&view==="gantt"      &&<GanttView allActivities={phasedActivities} dataDate={dataDate} onGoToActivity={()=>setView("critical")}/>}
          {M&&!metricsLoading&&view==="critical"   &&<CriticalView M={M} allActivities={phasedActivities} onUpdateActivity={updateActivity} activityUpdates={activityUpdates} dataDate={dataDate}/>}
          {M&&!metricsLoading&&view==="variance"   &&<LegacyMovedNotice title="Variance moved to Activity Analysis"
            body="Baseline Start/Finish variance, delayed/advanced breakdowns and a Biggest Finish Slips preset all read the same activity_analysis.py rows — with working-day-confident variance and Update Movement alongside, which this page didn't have."
            destinationLabel="Activity Analysis" destinationView="activityAnalysis" setView={setView}
            goTo={()=>{const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId); if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}}}/>}
          {M&&!metricsLoading&&view==="evm"        &&<LegacyMovedNotice title="EVM & Man-Hours moved to Project Controls"
            body="Earned Value, Cost Summary and Labor Productivity now live in one workspace on the same cost_engine.py figures, with Performance Trends, Variance Drivers and Management Narrative alongside them."
            destinationLabel="Project Controls" destinationView="projectControls" setView={setView}
            goTo={()=>{const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId); if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}}}/>}
          {M&&!metricsLoading&&view==="histogram"  &&<HistogramView M={M} allActivities={phasedActivities}/>}
          {M&&!metricsLoading&&view==="comparison" &&<LegacyMovedNotice title="Comparison is now part of Portfolio"
            body="The project-comparison table was the same portfolio rollup as Portfolio's card view — click Table View there instead of switching pages."
            destinationLabel="Portfolio" destinationView="portfolio" setView={setView}/>}
          {view==="activities"                      &&<LegacyMovedNotice title="Activities is now Activity Analysis"
            body="Search, named filters (Critical, Overdue, Negative Float, Milestones, etc.) and per-project views all carry over — plus Group by Phase (EPC), Quick Views, working-day-confident variance and Update Movement this page didn't have."
            destinationLabel="Activity Analysis" destinationView="activityAnalysis" setView={setView}
            goTo={()=>{const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId); if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}}}/>}
          {view==="diff"                            &&<LegacyMovedNotice title="Schedule Diff moved to Update Analysis"
            body="Compare Updates in Update Analysis reads the same backend comparison engine (schedule_comparison.py) that AI Chat and Reports already use, so every workspace agrees on what changed between two versions."
            destinationLabel="Update Analysis" destinationView="updateAnalysis" setView={setView}
            goTo={()=>{const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId); if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}}}/>}
          {view==="oos"                              &&<OutOfSequenceView allActivities={phasedActivities}/>}
          {view==="quality"                         &&<QualityView allActivities={phasedActivities}/>}
          {view==="narrative"                       &&<NarrativeView M={M} files={files} selectedIds={selectedIds} allActivities={phasedActivities} dataDate={dataDate}/>}
          {M&&!metricsLoading&&view==="tia"         &&<TIAView M={M} allActivities={phasedActivities}/>}
          {M&&!metricsLoading&&view==="powerbi"     &&<PowerBIView M={M} allActivities={phasedActivities} dataDate={dataDate}/>}
          {view==="resources"                       &&<ResourceView allActivities={phasedActivities}/>}
          {view==="status"                          &&<StatusView allActivities={phasedActivities} dataDate={dataDate}
            onOpenActivityAnalysis={()=>{const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId); if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);} setView("activityAnalysis");}}/>}
          {view==="updateAnalysis"                  &&<UpdateAnalysis initialProjectId={preferredProjectId} onManageVersions={handleManageVersions} onAnalyzeRecovery={handleAnalyzeRecovery}/>}
          {view==="riskIntel"                        &&<RiskIntelligence initialProjectId={preferredProjectId} initialVersionId={preferredVersionId} initialSubTab={preferredSubTab} initialRiskKey={preferredRiskKey}/>}
          {view==="activityAnalysis"                  &&<ActivityAnalysis initialProjectId={preferredProjectId} initialVersionId={preferredVersionId} initialFilters={preferredActivityFilters} onOpenFloatAnalysis={handleOpenFloatAnalysis} onOpenRiskRecovery={handleAnalyzeRecovery}/>}
          {view==="floatAnalysis"                     &&<FloatAnalysis initialProjectId={preferredProjectId} initialVersionId={preferredVersionId} initialActivityId={preferredActivityId} initialFloatFilter={preferredFloatFilter}/>}
          {view==="projectControls"                  &&<ProjectControls initialProjectId={preferredProjectId} initialSubTab={preferredSubTab}/>}
          {view==="baselineProgress"                  &&<BaselineProgress initialProjectId={preferredProjectId} onManageVersions={handleManageVersions}/>}
          {view==="intelligence"                      &&<Intelligence onOpenRecovery={(pid,vid)=>handleAnalyzeRecovery(pid,vid,undefined)}/>}
          {view==="reports"                            &&<LegacyMovedNotice title="Reports moved to Project Controls"
            body="Project Controls' Reports tab uses the authoritative report_service.py pipeline — Float Intelligence, Progress & Milestones, Update Intelligence and Schedule Risk & Recovery sections, persisted snapshots, and real PDF/Excel export — instead of a quick popup summary."
            destinationLabel="Project Controls" destinationView="projectControls" setView={setView}
            goTo={()=>{const f=files.find((x:any)=>selectedIds.includes(x.id)&&x.projectId&&x.scheduleUploadId); if(f){setPreferredProjectId(f.projectId);setPreferredVersionId(f.scheduleUploadId);}}}/>}
        </div>
      </div>
    </div>
    </LogicEditContext.Provider>
    </PrintProjectContext.Provider>
    {/* Ctrl+Z undo toast */}
    {undoToast&&(
      <div style={{position:'fixed',bottom:28,left:'50%',transform:'translateX(-50%)',
        background:C.card,border:`1px solid ${C.accent}`,borderRadius:10,
        padding:'9px 22px',color:C.accent,fontSize:13,fontWeight:700,
        zIndex:99999,boxShadow:'0 4px 24px rgba(0,0,0,0.6)',
        pointerEvents:'none',whiteSpace:'nowrap'}}>
        {undoToast}
      </div>
    )}
    {pendingImportFiles&&pendingImportFiles.length>0&&(
      <ImportCenter files={pendingImportFiles} onCancel={handleImportCancel} onConfirm={handleImportConfirm}
        onNavigate={(v,projectId)=>{setPreferredProjectId(projectId);setPreferredSubTab(undefined);setPreferredVersionId(undefined);setPreferredRiskKey(undefined);setPreferredActivityFilters(undefined);setView(v);}}/>
    )}
    </Fragment>
  );
}
