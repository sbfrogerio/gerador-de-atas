import csv, html, json, re, subprocess, tempfile, time, unicodedata, urllib.parse, urllib.request
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path

TARGETS={81:'Busca e Apreensão em Alienação Fiduciária',92:'Despejo',93:'Despejo por Falta de Pagamento',94:'Despejo por Falta de Pagamento Cumulado Com Cobrança',113:'Imissão na Posse',1707:'Reintegração / Manutenção de Posse',1709:'Interdito Proibitório',1116:'Execução Fiscal'}
FRESH=[92,93,94,113,1707,1709,1116]
INDEX='https://www2.tjal.jus.br/cdje/index.do'; DOWNLOAD='https://www2.tjal.jus.br/cdje/downloadCaderno.do'; SEARCH='https://www2.tjal.jus.br/cpopg/search.do'
ACCESS='https://datajud-wiki.cnj.jus.br/api-publica/acesso/'; ENDPOINT='https://api-publica.datajud.cnj.jus.br/api_publica_tjal/_search'
cnj_re=re.compile(r'\b\d{7}-\d{2}\.\d{4}\.8\.02\.\d{4}\b')
known={x.strip() for x in Path('tmp/captacao_tjal_known_2026.txt').read_text().splitlines() if x.strip()}

def norm(x):
    x=unicodedata.normalize('NFKD',str(x or '')); x=''.join(c for c in x if not unicodedata.combining(c)).lower()
    return ' '.join(x.replace(' / ','/').replace('/ ','/').replace(' /','/').split())
NAMES={code:norm(name) for code,name in TARGETS.items()}

def get(url,timeout=60):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 TJAL-v0.7-run','Accept-Language':'pt-BR,pt;q=0.9'})
    with urllib.request.urlopen(req,timeout=timeout) as r: return r.read(),r.geturl()

def element(doc,id_):
    m=re.search(rf'(?is)<(?P<t>[a-z0-9]+)\b[^>]*id=["\']{re.escape(id_)}["\'][^>]*>(?P<b>.*?)</(?P=t)>',doc)
    if not m:return None
    s=re.sub(r'(?s)<[^>]+>',' ',m.group('b')); return ' '.join(html.unescape(s).replace('\xa0',' ').split()) or None

def parse_dist(raw):
    if not raw:return None
    m=re.search(r'(\d{2}/\d{2}/\d{4})\s+às\s+(\d{2}:\d{2})',raw)
    if not m:return None
    return datetime.strptime(m.group(1)+' '+m.group(2),'%d/%m/%Y %H:%M').isoformat(timespec='minutes')

def actual_code(class_name):
    n=norm(class_name)
    for code,name in NAMES.items():
        if n==name:return code
    return None

def lookup(num):
    digits=re.sub(r'\D','',num); prefix=f'{digits[:7]}-{digits[7:9]}.{digits[9:13]}'; origin=digits[-4:]
    q={'conversationId':'','cbPesquisa':'NUMPROC','numeroDigitoAnoUnificado':prefix,'foroNumeroUnificado':origin,'dadosConsulta.valorConsultaNuUnificado':num,'dadosConsulta.tipoNuProcesso':'UNIFICADO'}
    try:
        payload,url=get(SEARCH+'?'+urllib.parse.urlencode(q),30); d=payload.decode('utf-8',errors='replace')
        classe=element(d,'classeProcesso')
        return {'status':'FOUND' if element(d,'numeroProcesso') else 'OTHER','numero':digits,'numero_formatado':num,'classe':classe,'classe_codigo':actual_code(classe),'distribuicao_raw':element(d,'dataHoraDistribuicaoProcesso'),'distribuicao':parse_dist(element(d,'dataHoraDistribuicaoProcesso')),'foro':element(d,'foroProcesso'),'vara':element(d,'varaProcesso'),'url':url}
    except Exception as exc:return {'status':'ERROR','numero':digits,'numero_formatado':num,'error':f'{type(exc).__name__}: {exc}'}

# DJe -> candidates
page,_=get(INDEX); doc=page.decode('utf-8',errors='replace')
pat=re.compile(r'dtPublicacao:\s*.*?value="(?P<date>\d{4}-\d{2}-\d{2})".*?cdVolume:\s*(?P<volume>\d+).*?nuDiario:\s*(?P<number>\d+)',re.S)
editions=[]; seen=set()
for m in pat.finditer(doc):
    k=(m.group('date'),int(m.group('number')))
    if k in seen:continue
    seen.add(k); editions.append({'date':k[0],'number':k[1],'volume':int(m.group('volume'))})
cutoff=date.today()-timedelta(days=21)
editions=sorted([e for e in editions if cutoff<=date.fromisoformat(e['date'])<=date.today()],key=lambda e:(e['date'],e['number']),reverse=True)[:10]
candidates=defaultdict(dict); edition_stats=[]
for e in editions:
    params=urllib.parse.urlencode({'dtDiario':datetime.strptime(e['date'],'%Y-%m-%d').strftime('%d/%m/%Y'),'cdCaderno':3,'tpDownload':'D'})
    payload,_=get(DOWNLOAD+'?'+params,90)
    if not payload.startswith(b'%PDF'):continue
    with tempfile.NamedTemporaryFile(suffix='.pdf',delete=False) as f:f.write(payload); p=f.name
    text=subprocess.run(['pdftotext','-layout',p,'-'],check=True,capture_output=True).stdout.decode('utf-8',errors='replace').replace('ﬁ','fi').replace('ﬂ','fl')
    matches=list(cnj_re.finditer(text)); counts=defaultdict(int)
    for i,m in enumerate(matches):
        formatted=m.group(0); digits=re.sub(r'\D','',formatted)
        if digits[9:13]!=str(date.today().year) or digits in known:continue
        end=matches[i+1].start() if i+1<len(matches) else min(len(text),m.end()+1600)
        window=norm(text[m.start():end])
        for code in FRESH:
            if NAMES[code] in window:
                candidates[code][formatted]=max(candidates[code].get(formatted,''),e['date']); counts[code]+=1
    edition_stats.append({'date':e['date'],'edition':e['number'],'cnjs':len(matches),'new_current_year_mentions':dict(counts)})

# round robin up to 120, newest publication then highest sequence
buckets={}
for code in FRESH:
    ordered=sorted(candidates[code].items(),key=lambda kv:(kv[1],int(re.sub(r'\D','',kv[0])[:7])),reverse=True)
    buckets[code]=deque(ordered)
selected=[]
while len(selected)<120 and any(buckets[c] for c in FRESH):
    progressed=False
    for code in FRESH:
        if buckets[code] and len(selected)<120:
            formatted,pub=buckets[code].popleft(); selected.append((code,formatted,pub)); progressed=True
    if not progressed:break
confirmed=[]; checked=[]
for expected,formatted,pub in selected:
    r=lookup(formatted); r['expected_class_code']=expected; r['dje_publication']=pub; checked.append(r)
    if r.get('status')=='FOUND' and r.get('classe_codigo') in FRESH and r.get('distribuicao'):
        confirmed.append(r)
    time.sleep(.12)

# DataJud current 45d
req=urllib.request.Request(ACCESS,headers={'User-Agent':'TJAL-v0.7-run'})
with urllib.request.urlopen(req,timeout=30) as rr: access=rr.read().decode('utf-8',errors='replace')
plain=html.unescape(re.sub(r'<[^>]+>',' ',access)); mm=re.search(r'Authorization:\s*APIKey\s+([A-Za-z0-9_+/=-]{20,})',plain,re.I)
if not mm:raise RuntimeError('DataJud public API key not found')
api_key=mm.group(1)
start=(date.today()-timedelta(days=44)); end=date.today()+timedelta(days=1)
def boundary(d):return int(datetime.combine(d,dt_time.min).strftime('%Y%m%d%H%M%S'))
def post(payload):
    body=json.dumps(payload).encode(); last=None
    for attempt in range(4):
        try:
            rq=urllib.request.Request(ENDPOINT,data=body,method='POST',headers={'Authorization':f'APIKey {api_key}','Content-Type':'application/json','Accept':'application/json','User-Agent':'TJAL-v0.7-run'})
            with urllib.request.urlopen(rq,timeout=90) as rr:return json.loads(rr.read().decode())
        except Exception as exc:last=exc; time.sleep(2**attempt)
    raise last
rows=[]; dj_summary={}
for code in TARGETS:
    sa=None; fetched=0; pages=0; total=0; max_aju=None; max_upd=None
    while True:
        q={'size':1000,'track_total_hits':True,'_source':['numeroProcesso','dataAjuizamento','classe','assuntos','orgaoJulgador','dataHoraUltimaAtualizacao','@timestamp','movimentos'],'query':{'bool':{'filter':[{'term':{'classe.codigo':code}},{'range':{'dataAjuizamento':{'gte':boundary(start),'lt':boundary(end)}}},{'term':{'nivelSigilo':0}}]}},'sort':[{'dataAjuizamento':{'order':'asc'}},{'numeroProcesso.keyword':{'order':'asc'}}]}
        if sa:q['search_after']=sa
        data=post(q); hits=(data.get('hits') or {}).get('hits') or []; t=(data.get('hits') or {}).get('total') or {}; total=int(t.get('value',len(hits))) if isinstance(t,dict) else int(t or len(hits)); pages+=1
        for h in hits:
            s=h.get('_source') or {}; aju=str(s.get('dataAjuizamento') or ''); upd=str(s.get('dataHoraUltimaAtualizacao') or s.get('@timestamp') or '')
            assuntos=' | '.join(str(a.get('nome') or '') for a in (s.get('assuntos') or []) if isinstance(a,dict)); org=(s.get('orgaoJulgador') or {}).get('nome') if isinstance(s.get('orgaoJulgador'),dict) else None
            rows.append({'numero':re.sub(r'\D','',str(s.get('numeroProcesso') or '')),'ajuizamento_raw':aju,'classe_codigo':code,'classe_nome':(s.get('classe') or {}).get('nome') if isinstance(s.get('classe'),dict) else TARGETS[code],'assunto':assuntos,'orgao':org,'source_updated_at':upd})
            max_aju=max(max_aju or aju,aju); max_upd=max(max_upd or upd,upd)
        fetched+=len(hits)
        if len(hits)<1000:break
        sa=hits[-1].get('sort')
        if not sa:break
    dj_summary[str(code)]={'fetched':fetched,'total':total,'pages':pages,'max_ajuizamento_raw':max_aju,'max_source_updated_at':max_upd}

Path('tmp/run_now_output').mkdir(parents=True,exist_ok=True)
with open('tmp/run_now_output/esaj_confirmed.tsv','w',newline='',encoding='utf-8') as f:
    fields=['numero','numero_formatado','classe_codigo','classe','distribuicao','distribuicao_raw','foro','vara','url','expected_class_code','dje_publication']; w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore'); w.writeheader(); w.writerows(confirmed)
with open('tmp/run_now_output/esaj_checked.tsv','w',newline='',encoding='utf-8') as f:
    fields=['numero','numero_formatado','status','classe_codigo','classe','distribuicao','distribuicao_raw','foro','vara','url','expected_class_code','dje_publication','error']; w=csv.DictWriter(f,fieldnames=fields,delimiter='\t',extrasaction='ignore'); w.writeheader(); w.writerows(checked)
with open('tmp/run_now_output/datajud.tsv','w',newline='',encoding='utf-8') as f:
    fields=['numero','ajuizamento_raw','classe_codigo','classe_nome','assunto','orgao','source_updated_at']; w=csv.DictWriter(f,fieldnames=fields,delimiter='\t'); w.writeheader(); w.writerows(rows)
summary={'generated_at':datetime.now().isoformat(timespec='seconds'),'known_input':len(known),'editions':edition_stats,'candidate_counts':{str(c):len(v) for c,v in candidates.items()},'selected_for_esaj':len(selected),'esaj_checked':len(checked),'esaj_confirmed':len(confirmed),'confirmed_by_class':{str(c):sum(1 for r in confirmed if r.get('classe_codigo')==c) for c in FRESH},'latest_confirmed_by_class':{str(c):max((r.get('distribuicao') for r in confirmed if r.get('classe_codigo')==c),default=None) for c in FRESH},'datajud':dj_summary,'datajud_rows':len(rows)}
Path('tmp/run_now_output/summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False))