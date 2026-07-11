from __future__ import annotations

import json, math, re, time
from bisect import bisect_right
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Iterable

CODE_RE = re.compile(r"^\d{6}$")

class ThemeMasterError(ValueError): pass

def num(v):
    try:
        if v in (None, ""): return None
        n=float(v); return n if math.isfinite(n) else None
    except (TypeError,ValueError): return None

def clamp(v,a=0.0,b=100.0): return max(a,min(b,float(v)))
def pw(v,pts):
    if v is None:return 0.0
    x=float(v)
    if x<=pts[0][0]:return float(pts[0][1])
    for (x0,y0),(x1,y1) in zip(pts,pts[1:]):
        if x<=x1:return float(y1 if x1==x0 else y0+(x-x0)/(x1-x0)*(y1-y0))
    return float(pts[-1][1])
def tone(v,neutral=0.0): return "zero" if v is None else "plus" if v>neutral else "minus" if v<neutral else "zero"
def eok(v,signed=False):
    if v is None:return "-"
    n=round(float(v)); return f"{'+' if signed and n>0 else ''}{n:,}억"
def pct(v,d=1,signed=False): return "-" if v is None else f"{'+' if signed and v>0 else ''}{v:.{d}f}%"
def ratio(v,d=2): return "-" if v is None else f"{v:.{d}f}x"
def textnum(v,d=0,signed=False):
    if v is None:return "-"
    p='+' if signed and v>0 else ''
    return f"{p}{round(v):,}" if d<=0 else f"{p}{v:,.{d}f}"
def percentiles(values,positive=False):
    xs=sorted(float(v) for v in values.values() if math.isfinite(float(v)) and (not positive or v>0))
    if not xs:return {k:0.0 for k in values}
    if len(xs)==1:return {k:(100.0 if (not positive or v>0) and v==xs[0] else 0.0) for k,v in values.items()}
    return {k:(0.0 if positive and v<=0 else clamp((bisect_right(xs,float(v))-1)/(len(xs)-1)*100)) for k,v in values.items()}

@dataclass(frozen=True)
class ThemeDef:
    theme_id:str; theme_name:str; short_name:str; enabled:bool; rank_eligible:bool; min_active_members:int; parent_theme_id:str|None=None
@dataclass(frozen=True)
class ThemeMaster:
    schema_version:int; master_version:str; maximum:int; minimum_weight:float
    themes:dict[str,ThemeDef]; by_stock:dict[str,tuple[tuple[str,float,str],...]]; by_theme:dict[str,tuple[tuple[str,float,str],...]]; warnings:tuple[str,...]

def load_theme_master(source:str|Path|dict[str,Any])->ThemeMaster:
    if isinstance(source,dict): p=source
    else:
        try:p=json.loads(Path(source).read_text(encoding="utf-8-sig"))
        except (OSError,json.JSONDecodeError) as e:raise ThemeMasterError(f"theme master load failed: {e}") from e
    if not isinstance(p,dict) or int(p.get("schema_version") or 0)!=1:raise ThemeMasterError("unsupported schema_version")
    version=str(p.get("master_version") or '').strip()
    if not version:raise ThemeMasterError("master_version is required")
    maximum=int(p.get("max_ranked_themes_per_stock") or 3); minimum=float(p.get("minimum_weight") or .15)
    raw=p.get("themes")
    if not isinstance(raw,dict) or not raw:raise ThemeMasterError("themes must be a non-empty object")
    themes={}; names=set()
    for tid,x in raw.items():
        if not isinstance(x,dict):raise ThemeMasterError(f"invalid theme entry: {tid}")
        name=str(x.get("theme_name") or '').strip()
        if not name or name in names:raise ThemeMasterError(f"duplicate or empty theme_name: {name}")
        names.add(name); themes[tid]=ThemeDef(tid,name,str(x.get("short_name") or name),bool(x.get("enabled",True)),bool(x.get("rank_eligible",True)),max(1,int(x.get("min_active_members") or 3)),str(x.get("parent_theme_id")) if x.get("parent_theme_id") else None)
    stocks=p.get("stock_memberships")
    if not isinstance(stocks,dict):raise ThemeMasterError("stock_memberships must be an object")
    by_stock={}; build=defaultdict(list); warnings=[]
    for code,entries in stocks.items():
        if not CODE_RE.match(str(code)) or not isinstance(entries,list) or not entries:raise ThemeMasterError(f"invalid membership list: {code}")
        seen=set(); out=[]; total=0.; count=0
        for x in entries:
            tid=str(x.get("theme_id") or '') if isinstance(x,dict) else ''
            if tid not in themes or tid in seen:raise ThemeMasterError(f"invalid or duplicate theme membership {tid} for {code}")
            seen.add(tid); w=float(x.get("weight") or 0); rel=str(x.get("relation") or 'related'); d=themes[tid]
            if d.enabled and d.rank_eligible:
                if not 0<w<=1 or w+.001<minimum:raise ThemeMasterError(f"invalid weight {w} for {code}/{tid}")
                total+=w; count+=1; build[tid].append((str(code),w,rel))
            elif not 0<=w<=1:raise ThemeMasterError(f"invalid tag weight {w}")
            out.append((tid,w,rel))
        if count>maximum:raise ThemeMasterError(f"too many ranked themes for {code}")
        if count and abs(total-1)>.001:raise ThemeMasterError(f"ranked weight sum must be 1.0 for {code}: {total}")
        if not count:warnings.append(f"no enabled ranked membership: {code}")
        by_stock[str(code)]=tuple(out)
    return ThemeMaster(1,version,maximum,minimum,themes,by_stock,{k:tuple(v) for k,v in build.items()},tuple(warnings))

class ThemeEngine:
    def __init__(self,master:ThemeMaster,display_theme_limit=12):
        self.master=master; self.limit=max(1,int(display_theme_limit)); self.history=defaultdict(lambda:deque(maxlen=720)); self.score_history=defaultdict(lambda:deque(maxlen=240)); self.first=None; self.states={}; self.pending={}; self.order=[]; self.swap=None; self.negative_delta_count=0; self.compute_count=0
    @staticmethod
    def at(history,target):
        found=None
        for x in history:
            if x[0]<=target:found=x
            else:break
        return found
    def flow(self,tid,now,current,window):
        h=self.history[tid]; old=self.at(h,now-window)
        if old is not None:delta=current-old[1]; exact=True
        elif h and now>h[0][0]:delta=(current-h[0][1])/(now-h[0][0])*window; exact=False
        else:return 0.,False
        if delta<0:self.negative_delta_count+=1;delta=0.
        return delta,exact
    @staticmethod
    def cap(score,cov): return score if cov>=.8 else min(score,79 if cov>=.65 else 69 if cov>=.5 else 59)
    @staticmethod
    def fresh(row,key):
        age=row.get("price_age_sec")
        if key=="execution_strength":return age is None or age<=5
        x=row.get("strength_age_sec" if key=="strength_5m" else "program_age_sec")
        return x is None or x<=(180 if key=="strength_5m" else 120)
    def leaders(self,members):
        if not members:return []
        valid_rates=[x["change_rate"] for x in members if x.get("change_rate") is not None]
        med=median(valid_rates) if valid_rates else 0
        shares=percentiles({i:float(x.get("theme_share_pct") or 0) for i,x in enumerate(members)})
        pp=percentiles({i:max(0.,x.get("program_net") or 0.) for i,x in enumerate(members)},True)
        lp=percentiles({i:max(0.,x.get("large_trade_net_count") or 0.) for i,x in enumerate(members)},True)
        out=[]
        for i,x in enumerate(members):
            tv=x.get("weighted_trade_value_eok");cr=x.get("change_rate");sh=x.get("theme_share_pct")
            if tv is None or cr is None or sh is None:continue
            ar=x.get("amount_ratio");ex=x.get("execution_strength");s5=x.get("strength_5m");pr=x.get("program_net");lg=x.get("large_trade_net_count")
            comps=[
                (shares[i]*.6+clamp(sh/30*100)*.4,25,True),
                (clamp(50+(cr-med)*10),20,True),
                (pw(ar,[(.7,0),(1,25),(1.5,45),(2,60),(3,80),(5,100)]),15,ar is not None),
                (pw(ex,[(80,0),(100,35),(120,55),(150,75),(200,100)]),15,ex is not None and self.fresh(x,"execution_strength")),
                (pw(s5,[(80,0),(100,35),(120,55),(150,75),(200,100)]),10,s5 is not None and self.fresh(x,"strength_5m")),
                (20 if pr==0 else pp[i] if pr is not None and pr>0 else 0,8,pr is not None and self.fresh(x,"program_net")),
                (20 if lg==0 else lp[i] if lg is not None and lg>0 else 0,7,lg is not None),
            ]
            score=sum(v*w/100 for v,w,active in comps if active)
            if pr is not None and lg is not None and pr<0 and lg<0:score-=10
            coverage=sum(w for _,w,active in comps if active)/100
            x.update(leader_score=round(self.cap(clamp(score),coverage),2),coverage=round(coverage,4))
            out.append(x)
        out.sort(key=lambda x:(-x["leader_score"],-x["weighted_trade_value_eok"],x["stock_code"]))
        top=out[0]["leader_score"] if out else 0
        for i,x in enumerate(out,1):
            score=x["leader_score"]
            role=("LEADER","주도","lead") if i==1 and score>=85 else ("CO_LEADER","동반","co") if score>=75 and top-score<=10 else ("FOLLOWER","후발","follow") if score>=60 else ("WATCH","관찰","watch")
            if x["coverage"]<.5:role=("WATCH","관찰","watch")
            x.update(display_rank=i,role_key=role[0],role_text=role[1],role_class=role[2])
        return out
    @staticmethod
    def support(xs):
        v=[x["leader_score"] for x in xs[:3]]
        return 0 if not v else v[0] if len(v)==1 else v[0]*.6+v[1]*.4 if len(v)==2 else v[0]*.5+v[1]*.3+v[2]*.2
    def stable_state(self,tid,target,now):
        cur=self.states.get(tid)
        if target=="WAIT_DATA" or cur in (None,"WAIT_DATA"):self.states[tid]=target;self.pending.pop(tid,None);return target
        if cur==target:self.pending.pop(tid,None);return cur
        dwell=1.5 if target in ("RISING","SURGE") else 2 if target=="COOLING" else 3; p=self.pending.get(tid)
        if not p or p[0]!=target:self.pending[tid]=(target,now);return cur
        if now-p[1]>=dwell:self.states[tid]=target;self.pending.pop(tid,None);return target
        return cur
    def stable_order(self,raw,scores,now):
        if not self.order:self.order=list(raw);return list(self.order)
        self.order=[x for x in self.order if x in raw]+[x for x in raw if x not in self.order]; pos={x:i for i,x in enumerate(raw)}; cand=None
        for i in range(1,len(self.order)):
            a,b=self.order[i],self.order[i-1]
            if pos[a]<pos[b] and scores[a]>=scores[b]+3:cand=(a,b);break
        if not cand:self.swap=None;return list(self.order)
        if not self.swap or self.swap[0]!=cand:self.swap=(cand,now);return list(self.order)
        if now-self.swap[1]>=1.5:
            a,b=cand; ia,ib=self.order.index(a),self.order.index(b); self.order[ia],self.order[ib]=b,a; self.swap=None
        return list(self.order)
    def compute(self,rows:Iterable[dict[str,Any]],*,now_mono=None,market_session=None):
        now=float(time.monotonic() if now_mono is None else now_mono)
        self.compute_count+=1
        self.first=now if self.first is None else self.first
        observed=max(0.,now-self.first)
        numeric_keys=("price","change_rate","trade_value_eok","amount_ratio","execution_strength","strength_5m","program_net","large_trade_net_count","price_age_sec","strength_age_sec","program_age_sec")
        rowmap={}
        for source in rows:
            code=str(source.get("stock_code") or '')
            if not CODE_RE.match(code):continue
            row=dict(source)
            for key in numeric_keys:row[key]=num(source.get(key))
            rowmap[code]=row
        agg={}
        for tid,d in self.master.themes.items():
            if not d.enabled or not d.rank_eligible:continue
            members=[];total=0.;up=down=flat=0
            for code,weight,relation in self.master.by_theme.get(tid,()):
                row=rowmap.get(code)
                if not row:continue
                trade_value=row.get("trade_value_eok");change_rate=row.get("change_rate");price=row.get("price")
                if trade_value is None or trade_value<0 or (price is None and change_rate is None):continue
                weighted=trade_value*weight;total+=weighted
                up+=change_rate is not None and change_rate>0;down+=change_rate is not None and change_rate<0;flat+=change_rate==0
                member=dict(row);member.update(theme_id=tid,theme_weight=weight,theme_relation=relation,weighted_trade_value_eok=weighted);members.append(member)
            history=self.history[tid]
            if not history or now-history[-1][0]>=.9:history.append((now,total))
            inflow1,_=self.flow(tid,now,total,60);inflow5,_=self.flow(tid,now,total,300)
            average=inflow5/5 if inflow5>0 else 0;accel=inflow1/average if average>0 else 2 if inflow1>0 else 0;active=len(members)
            agg[tid]=dict(definition=d,members=members,total=total,up=up,down=down,flat=flat,active=active,inflow_1m=inflow1,inflow_5m=inflow5,accel=accel,breadth_score=50 if not active else clamp(50+50*(up-down)/active))
        market_total=sum(item["total"] for item in agg.values())
        for item in agg.values():
            item["market_share_pct"]=item["total"]/market_total*100 if market_total else 0
            total=item["total"]
            for member in item["members"]:member["theme_share_pct"]=member["weighted_trade_value_eok"]/total*100 if total else 0
            scored=self.leaders(item["members"]);item["scored_members"]=scored;item["leader_support"]=self.support(scored);item["concentration"]=scored[0]["theme_share_pct"] if scored else 0
        p1=percentiles({key:item["inflow_1m"] for key,item in agg.items()},True);p5=percentiles({key:item["inflow_5m"] for key,item in agg.items()},True);ps=percentiles({key:item["market_share_pct"] for key,item in agg.items()})
        for tid,item in agg.items():
            weight5=25*min(1,observed/300);weight1=55-weight5
            accel_score=pw(item["accel"],[(.7,0),(.85,20),(1,40),(1.2,65),(1.4,80),(1.7,95),(2,100)])
            score=p1[tid]*weight1/100+p5[tid]*weight5/100+accel_score*.15+ps[tid]*.1+item["breadth_score"]*.1+item["leader_support"]*.1
            penalty=clamp((item["concentration"]-45)*.4,0,15)*(.5 if item["breadth_score"]>=75 else 1);score=clamp(score-penalty)
            previous=self.at(self.score_history[tid],now-60);drop=max(0,previous[1]-score) if previous else 0;definition=item["definition"]
            if item["active"]<definition.min_active_members or observed<10:raw_state="WAIT_DATA"
            elif item["inflow_5m"]>0 and (item["accel"]<.75 or drop>=15):raw_state="COOLING"
            elif observed>=30 and score>=85 and p1[tid]>=85 and item["breadth_score"]>=60 and item["accel"]>=1.25:raw_state="SURGE"
            elif score>=70 and p1[tid]>=70 and item["breadth_score"]>=50:raw_state="RISING"
            else:raw_state="STEADY"
            item.update(theme_score=score,state=self.stable_state(tid,raw_state,now));self.score_history[tid].append((now,score))
        raw_order=sorted(agg,key=lambda key:(agg[key]["state"]=="WAIT_DATA",-agg[key]["theme_score"],-agg[key]["inflow_1m"],-agg[key]["inflow_5m"],key))
        order=self.stable_order(raw_order,{key:item["theme_score"] for key,item in agg.items()},now)
        display_ids=order[:self.limit];max1=max([item["inflow_1m"] for item in agg.values()] or [0]);max5=max([item["inflow_5m"] for item in agg.values()] or [0]);state_meta={"SURGE":("SURGE","surge"),"RISING":("RISING","rising"),"STEADY":("STEADY","steady"),"COOLING":("COOLING","cooling"),"WAIT_DATA":("WAIT","wait-data")};themes=[];details={}
        for rank,tid in enumerate(display_ids,1):
            item=agg[tid];definition=item["definition"];state_text,state_class=state_meta[item["state"]];leaders=[];members=[]
            for member in item["scored_members"]:
                change_rate=member.get("change_rate");trade_value=member.get("trade_value_eok")
                base=dict(display_rank=member["display_rank"],stock_code=str(member.get("stock_code") or ''),stock_name=str(member.get("stock_name") or member.get("stock_code") or ''),change_rate_text=pct(change_rate,2,True),change_rate_tone=tone(change_rate),trade_value_text=eok(trade_value),theme_share_text=pct(member.get("theme_share_pct"),1),role_key=member["role_key"],role_text=member["role_text"],role_class=member["role_class"])
                if len(leaders)<3:leaders.append(base)
                leader_score=member["leader_score"]
                members.append({**base,"price_text":textnum(member.get("price")),"amount_ratio_text":ratio(member.get("amount_ratio"),1),"amount_ratio_tone":tone(member.get("amount_ratio"),1),"execution_strength_text":textnum(member.get("execution_strength")),"execution_strength_tone":tone(member.get("execution_strength"),100),"strength_5m_text":textnum(member.get("strength_5m")),"strength_5m_tone":tone(member.get("strength_5m"),100),"program_net_text":textnum(member.get("program_net"),signed=True),"program_net_tone":tone(member.get("program_net")),"large_trade_text":textnum(member.get("large_trade_net_count"),signed=True),"large_trade_tone":tone(member.get("large_trade_net_count")),"leader_score_text":str(round(leader_score)),"leader_score_class":"score-a" if leader_score>=90 else "score-b" if leader_score>=80 else "score-c" if leader_score>=70 else "score-d" if leader_score>=60 else "score-f","coverage":member["coverage"],"data_state":"LIVE","row_class":""})
            output=dict(theme_id=tid,display_rank=rank,theme_name=definition.theme_name,state_key=item["state"],state_text=state_text,state_class=state_class,theme_score=round(item["theme_score"],2),theme_score_text=str(round(item["theme_score"])),member_count_text=f"{item['active']}종목",breadth_text=f"상승 {item['up']} / 하락 {item['down']}",breadth_tone="plus" if item["up"]>item["down"] else "minus" if item["up"]<item["down"] else "zero",inflow_1m_text=eok(item["inflow_1m"],True),inflow_1m_tone=tone(item["inflow_1m"]),inflow_1m_bar_pct=round(item["inflow_1m"]/max1*100,1) if max1 else 0,inflow_5m_text=eok(item["inflow_5m"],True),inflow_5m_tone=tone(item["inflow_5m"]),inflow_5m_bar_pct=round(item["inflow_5m"]/max5*100,1) if max5 else 0,trade_value_text=eok(item["total"]),market_share_text=pct(item["market_share_pct"],1),flow_accel_text=ratio(item["accel"],2),flow_accel_tone=tone(item["accel"],1),leader_concentration_text=pct(item["concentration"],0),concentration_warning=item["concentration"]>=50,leaders=leaders)
            themes.append(output);details[tid]={"schema_version":1,"theme":{"theme_id":tid,"theme_name":definition.theme_name,"member_count_text":output["member_count_text"],"trade_value_text":output["trade_value_text"],"market_share_text":output["market_share_text"]},"members":members}
        top5=sum(agg[tid]["market_share_pct"] for tid in display_ids[:5]);strong_id=max(agg,key=lambda key:agg[key]["inflow_1m"],default=None);warning_id=max(agg,key=lambda key:agg[key]["concentration"],default=None);rising=sum(1 for item in agg.values() if item["up"]>item["down"])
        strongest=f"{agg[strong_id]['definition'].theme_name} {eok(agg[strong_id]['inflow_1m'],True)}/1분" if strong_id else "-";warning_text=f"{agg[warning_id]['definition'].theme_name} 1위 종목 집중 {pct(agg[warning_id]['concentration'],0)}" if warning_id else "-"
        return {"schema_version":1,"source":"stockboard_v2_theme_engine","master_version":self.master.master_version,"market_session":dict(market_session or {}),"summary":{"top5_concentration_text":f"상위 5개 {top5:.1f}%","strongest_flow_text":strongest,"breadth_text":f"{len(agg)}개 테마 중 {rising}개 상승 우위","warning_text":warning_text,"warning_tone":"warn" if warning_id and agg[warning_id]["concentration"]>=50 else "zero"},"themes":themes,"details":details,"diagnostics":{"theme_count":len(agg),"display_theme_count":len(themes),"observation_sec":round(observed,3),"negative_delta_count":self.negative_delta_count,"compute_count":self.compute_count}}
