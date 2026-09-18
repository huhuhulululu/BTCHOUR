"""The fill proxy 'next minute's bid <= mine' counts a fill even when NOTHING
traded. In an illiquid book that invents benign fills and flatters adverse
selection. Require a real print in the filling bar and re-measure both markets.
"""
import sys, json, gzip, pathlib, random
sys.path.insert(0, "/home/user/BTCHOUR")

def money(v):
    try: f=float(v)
    except (TypeError,ValueError): return None
    return f if 0.0 < f < 1.0 else None

def vol_of(st):
    raw = st.get("volume_fp")
    if raw in (None,""): return 0.0
    try: return float(raw)
    except (TypeError,ValueError): return 0.0

def load_kxbtc():
    D = pathlib.Path("/home/user/BTCHOUR/data/kxbtc")
    rows=[]
    for p in sorted(D.glob("*.json.gz")):
        with gzip.open(p,"rt",encoding="utf-8") as h: d=json.load(h)
        mat = d["maturity_ms"]/1000.0
        for tk, mk in d["markets"].items():
            rr = {int(k):v for k,v in mk["rows"].items()}
            out = 1.0 if mk["result"]=="yes" else 0.0
            for ts in sorted(rr):
                if mat - ts < 300.0: continue
                st = rr[ts]
                bid = money((st.get("yes_bid") or {}).get("close_dollars"))
                ask = money((st.get("yes_ask") or {}).get("close_dollars"))
                if bid is None or ask is None or ask<=bid: continue
                nxt = rr.get(ts+60)
                if nxt is None: continue
                nb = money((nxt.get("yes_bid") or {}).get("close_dollars"))
                quote_fill = (nb is not None and nb <= bid)
                traded = vol_of(nxt) > 0
                rows.append((p.stem,bid,ask,quote_fill,traded,out,(bid+ask)/2.0))
    return rows

def load_kxbtcd(limit):
    D = pathlib.Path("/home/user/BTCHOUR/data/archive")
    rows=[]
    for p in sorted(D.glob("*.json.gz"))[:limit]:
        with gzip.open(p,"rt",encoding="utf-8") as h: d=json.load(h)
        mat = d["maturity_ms"]/1000.0
        results = {float(k):v for k,v in (d.get("results") or {}).items()}
        for strike, sticks in (d.get("candles") or {}).items():
            if not isinstance(sticks,dict): continue
            res = results.get(float(strike))
            if res not in {"yes","no"}: continue
            out = 1.0 if res=="yes" else 0.0
            rr = {int(k):v for k,v in sticks.items() if isinstance(v,dict)}
            for ts in sorted(rr):
                if mat - ts < 300.0: continue
                st = rr[ts]
                bid = money((st.get("yes_bid") or {}).get("close_dollars"))
                ask = money((st.get("yes_ask") or {}).get("close_dollars"))
                if bid is None or ask is None or ask<=bid: continue
                nxt = rr.get(ts+60)
                if nxt is None: continue
                nb = money((nxt.get("yes_bid") or {}).get("close_dollars"))
                quote_fill = (nb is not None and nb <= bid)
                traded = vol_of(nxt) > 0
                rows.append((p.stem,bid,ask,quote_fill,traded,out,(bid+ask)/2.0))
    return rows

def boot(ph,iters=4000,seed=31):
    rng=random.Random(seed); hs=list(ph)
    if len(hs)<2: return None
    o=[]
    for _ in range(iters):
        s=n=0.0
        for _ in hs:
            k=rng.choice(hs); s+=ph[k][0]; n+=ph[k][1]
        if n: o.append(s/n)
    o.sort(); return o[int(.025*len(o))], o[int(.975*len(o))]

def line(label, sel):
    if len(sel) < 30:
        print(f"{label:<40}{len(sel):>7}{'样本不足':>16}"); return None
    ph={}
    for r in sel:
        e=r[5]-r[1]
        a=ph.setdefault(r[0],[0.0,0]); a[0]+=e; a[1]+=1
    n=len(sel); mean=sum(r[5]-r[1] for r in sel)/n
    ci=boot(ph)
    cis=f"[{ci[0]*100:+.2f}, {ci[1]*100:+.2f}]" if ci else "—"
    print(f"{label:<40}{n:>7}{mean*100:>+11.2f}分{cis:>22}")
    return mean

for name, rows in (("KXBTC 区间盘", load_kxbtc()), ("KXBTCD 上下轨", load_kxbtcd(32))):
    print(f"\n########## {name}：{len(rows)} 个观察 ##########")
    print(f"{'':<40}{'笔数':>7}{'每张边际':>12}{'95%区间':>22}")
    a = line("上限（假设永远成交）", rows)
    b = line("报价成交（旧口径，可能无成交）", [r for r in rows if r[3]])
    c = line("真成交成交（该 bar 有成交量）", [r for r in rows if r[3] and r[4]])
    sp = sum(r[2]-r[1] for r in rows)/len(rows)
    print(f"\n  价差 {sp*100:.2f} 分")
    if a is not None and b is not None:
        print(f"  逆向选择（旧口径） {(a-b)*100:+.2f} 分")
    if a is not None and c is not None:
        print(f"  逆向选择（要求真成交） {(a-c)*100:+.2f} 分   <-- 诚实的那个")
    nf = sum(1 for r in rows if r[3]); nt = sum(1 for r in rows if r[3] and r[4])
    print(f"  旧口径判成交 {nf}，其中该 bar 真有成交的只有 {nt}（{nt/max(nf,1):.1%}）")
